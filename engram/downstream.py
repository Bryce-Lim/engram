"""The downstream client — multiplexed MCP stdio connection to the tool server.

Engram spawns the real MCP server as a subprocess and talks to it over the same
stdio JSON-RPC framing an agent would use. The key requirement is *concurrency*:
a real call and several speculative calls may all be in flight at once, so we
cannot use a simple request/response loop. Instead a single reader thread
demultiplexes responses by JSON-RPC ``id`` and resolves the corresponding
:class:`Future`.

Request ids issued downstream are namespaced (prefixed) so they never collide
with the ids the upstream agent chooses. The proxy keeps its own mapping
between the two id spaces.
"""

import itertools
import subprocess
import sys
import threading
from typing import Any, Callable, Dict, List, Optional

from engram import jsonrpc


class Future:
    """A minimal one-shot result slot, resolved by the reader thread."""

    __slots__ = ("_event", "_value", "_is_error")

    def __init__(self) -> None:
        self._event = threading.Event()
        self._value = None  # type: Any
        self._is_error = False

    def set_result(self, value: Any) -> None:
        self._value = value
        self._is_error = False
        self._event.set()

    def set_error(self, error: Any) -> None:
        self._value = error
        self._is_error = True
        self._event.set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._event.wait(timeout)

    @property
    def done(self) -> bool:
        return self._event.is_set()

    @property
    def is_error(self) -> bool:
        return self._is_error

    @property
    def value(self) -> Any:
        return self._value


class DownstreamClient:
    """Owns the server subprocess and a demultiplexing reader thread."""

    def __init__(self, command: List[str], id_prefix: str = "engram-down-",
                 on_log: Optional[Callable[[str], None]] = None,
                 max_line_bytes: int = 16 * 1024 * 1024):
        self.command = command
        self.id_prefix = id_prefix
        self._on_log = on_log or (lambda m: None)
        # A single framed message may not exceed this; a server emitting an
        # unbounded line (e.g. no newline ever) would otherwise grow memory
        # without limit. We resync past such garbage instead of buffering it.
        self.max_line_bytes = max_line_bytes
        self._proc = None               # type: Optional[subprocess.Popen]
        self._counter = itertools.count(1)
        self._pending = {}              # type: Dict[str, Future]
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._reader = None             # type: Optional[threading.Thread]
        self._stderr_reader = None      # type: Optional[threading.Thread]
        self._closed = threading.Event()
        # Callback invoked with every response message the server sends, so the
        # proxy can forward responses to ids it routed through verbatim.
        self.on_response = None         # type: Optional[Callable[[Dict[str, Any]], None]]

    def start(self) -> None:
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._reader = threading.Thread(target=self._read_loop, name="engram-downstream-reader")
        self._reader.daemon = True
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._drain_stderr, name="engram-downstream-stderr")
        self._stderr_reader.daemon = True
        self._stderr_reader.start()

    def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for line in iter(self._proc.stderr.readline, b""):
            try:
                self._on_log("[server] " + line.decode("utf-8", "replace").rstrip())
            except Exception:
                pass

    def _bounded_lines(self, stream):
        """Yield newline-terminated chunks, capping any single line's size.

        Reads in fixed blocks and splits on ``\\n``. If an unterminated line
        grows past ``max_line_bytes``, the oversize bytes are dropped and the
        reader resyncs at the next newline, so a misbehaving server cannot
        exhaust memory or wedge the reader thread.
        """
        buf = bytearray()
        dropping = False  # True while discarding an over-length line
        while True:
            block = stream.read(65536)
            if not block:
                if buf and not dropping:
                    yield bytes(buf)
                return
            buf.extend(block)
            while True:
                nl = buf.find(b"\n")
                if nl == -1:
                    if len(buf) > self.max_line_bytes:
                        # Over the cap with no newline yet: drop and resync.
                        self._on_log("downstream line exceeded %d bytes; dropping"
                                     % self.max_line_bytes)
                        del buf[:]
                        dropping = True
                    break
                if dropping:
                    # This newline ends a discarded over-length line; resume.
                    del buf[: nl + 1]
                    dropping = False
                    continue
                if nl > self.max_line_bytes:
                    # The line terminated, but it is over the cap: drop it
                    # rather than yield/buffer an oversize frame.
                    self._on_log("downstream line exceeded %d bytes; dropping"
                                 % self.max_line_bytes)
                    del buf[: nl + 1]
                    continue
                line = bytes(buf[:nl])
                del buf[: nl + 1]
                yield line

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stdout = self._proc.stdout
        for raw in self._bounded_lines(stdout):
            if not raw.strip():
                continue
            try:
                msg = jsonrpc.decode(raw)
            except ValueError as exc:
                self._on_log("downstream decode error: %s" % exc)
                continue
            self._dispatch(msg)
        # Server closed stdout: fail every outstanding future so no caller hangs.
        self._closed.set()
        with self._pending_lock:
            pending = list(self._pending.items())
            self._pending.clear()
        for _id, fut in pending:
            fut.set_error(jsonrpc.make_error(_id, jsonrpc.INTERNAL_ERROR,
                                             "downstream server closed connection")["error"])

    def _dispatch(self, msg: Dict[str, Any]) -> None:
        if jsonrpc.is_response(msg):
            msg_id = msg.get("id")
            fut = None
            if isinstance(msg_id, str):
                with self._pending_lock:
                    fut = self._pending.pop(msg_id, None)
            if fut is not None:
                if "error" in msg:
                    fut.set_error(msg["error"])
                else:
                    fut.set_result(msg.get("result"))
                return
        # Not one of our tracked requests (e.g. a server-initiated notification
        # or a passthrough response): hand it to the proxy if it wants it.
        if self.on_response is not None:
            self.on_response(msg)

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Future:
        """Send a request and return a Future for its response.

        If the downstream pipe is already gone, the returned future is settled
        with an error rather than raising, so callers on worker threads always
        get a clean ``(result, error)`` instead of an unhandled exception.
        """
        return self.call_with_id(method, params)[1]

    def call_with_id(self, method: str, params: Optional[Dict[str, Any]] = None):
        """Like :meth:`call` but also returns the request id.

        Callers that may *abandon* the future on a timeout should use this and
        pass the id to :meth:`cancel`, otherwise the pending-response slot leaks
        for the life of a long-lived (but selectively unresponsive) server.
        """
        msg_id = self.id_prefix + str(next(self._counter))
        fut = Future()
        with self._pending_lock:
            self._pending[msg_id] = fut
        try:
            self._send(jsonrpc.make_request(msg_id, method, params))
        except (BrokenPipeError, ValueError, RuntimeError) as exc:
            with self._pending_lock:
                self._pending.pop(msg_id, None)
            fut.set_error(jsonrpc.make_error(
                msg_id, jsonrpc.INTERNAL_ERROR,
                "downstream send failed: %s" % exc)["error"])
        return msg_id, fut

    def cancel(self, msg_id: str) -> None:
        """Reclaim a pending slot whose future was abandoned (e.g. on timeout).

        Safe to race with :meth:`_dispatch` settling the same id: whichever
        runs first wins, and ``pop(..., None)`` tolerates the absence.
        """
        with self._pending_lock:
            self._pending.pop(msg_id, None)

    def send_raw(self, msg: Dict[str, Any]) -> None:
        """Forward a message downstream verbatim (used for passthrough)."""
        self._send(msg)

    def _send(self, msg: Dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("downstream client not started")
        data = jsonrpc.encode(msg)
        with self._write_lock:
            try:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except (BrokenPipeError, ValueError):
                self._closed.set()
                raise

    def call_and_wait(self, method: str, params: Optional[Dict[str, Any]] = None,
                      timeout: Optional[float] = None) -> Dict[str, Any]:
        """Convenience: send a request and block for its result/error."""
        fut = self.call(method, params)
        if not fut.wait(timeout):
            raise TimeoutError("downstream call timed out: %s" % method)
        if fut.is_error:
            raise RuntimeError("downstream error: %s" % fut.value)
        return fut.value

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def close(self) -> None:
        self._closed.set()
        if self._proc is None:
            return
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
                self._proc.wait(timeout=5)
            except Exception:
                pass
        # Close the remaining stdio handles so the interpreter doesn't warn
        # about leaked file objects on teardown.
        for stream in (self._proc.stdout, self._proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
