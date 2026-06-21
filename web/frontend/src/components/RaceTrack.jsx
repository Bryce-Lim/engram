import { useEffect, useRef, useState } from 'react'

// A deliberately simple live race: two tall vertical bars that fill from the
// bottom as the agent completes calls. One dot per call — it lights up the
// instant that call finishes. Engram's bar fills almost at once (the calls were
// prefetched during the think); the baseline fills one dot at a time as each
// call waits on the network. Whichever bar reaches the top first wins.
//
// No stripes, no sparks, no moving runner — just "dots fill the bar until done."
export default function RaceTrack({ running, events, numCalls }) {
  const [clock, setClock] = useState(0)
  const startRef = useRef(null)
  const rafRef = useRef(null)

  useEffect(() => {
    if (!running) return
    startRef.current = performance.now()
    const loop = (now) => {
      setClock(now - startRef.current)
      rafRef.current = requestAnimationFrame(loop)
    }
    rafRef.current = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(rafRef.current)
  }, [running])

  const engram = deriveLane('engram', events)
  const baseline = deriveLane('baseline', events)

  const bothDone = engram.doneMs != null && baseline.doneMs != null
  const speedup =
    bothDone && engram.doneMs > 0 ? baseline.doneMs / engram.doneMs : null

  // Freeze the timer once both lanes are done.
  const elapsed = bothDone
    ? Math.max(engram.doneMs, baseline.doneMs)
    : clock

  return (
    <div className="glass rounded-3xl p-6">
      <div className="mb-6 flex items-baseline justify-between">
        <div className="text-sm font-semibold text-white">Live race</div>
        <div className="tnum text-sm font-light text-mute">
          {(elapsed / 1000).toFixed(2)}s
        </div>
      </div>

      <div className="flex items-stretch justify-center gap-10">
        <Bar
          label="With Engram"
          tone="cyan"
          completed={engram.completed}
          total={numCalls}
          doneMs={engram.doneMs}
          won={bothDone}
        />
        <Bar
          label="Without Engram"
          tone="warm"
          completed={baseline.completed}
          total={numCalls}
          doneMs={baseline.doneMs}
          won={false}
        />
      </div>

      {speedup && (
        <div
          className="mt-7 text-center text-base font-light text-white/80"
          style={{ animation: 'fadein 0.6s ease both' }}
        >
          Engram finished{' '}
          <span className="font-semibold text-cyan">{speedup.toFixed(2)}× faster</span>
          {' — '}
          {Math.round(baseline.doneMs - engram.doneMs)} ms saved
        </div>
      )}
    </div>
  )
}

function Bar({ label, tone, completed, total, doneMs, won }) {
  const cyan = tone === 'cyan'
  const done = doneMs != null
  const n = Math.max(total, 1)
  const fillPct = (Math.min(completed, n) / n) * 100

  // Liquid tint: warm orange glass for Engram, cool white glass for baseline.
  const liquid = cyan
    ? {
        body: 'linear-gradient(180deg, rgba(255,157,60,0.78) 0%, rgba(255,140,40,0.42) 55%, rgba(255,120,20,0.30) 100%)',
        surface: 'rgba(255,196,130,0.95)',
        glow: '0 -8px 26px rgba(255,157,60,0.55)',
        dot: '#ffce9b',
      }
    : {
        body: 'linear-gradient(180deg, rgba(255,255,255,0.42) 0%, rgba(255,255,255,0.18) 60%, rgba(255,255,255,0.10) 100%)',
        surface: 'rgba(255,255,255,0.85)',
        glow: '0 -6px 18px rgba(255,255,255,0.22)',
        dot: 'rgba(255,255,255,0.85)',
      }

  // One dot per call, evenly spaced up the bar; lit once that call completes.
  const dots = []
  for (let i = 0; i < n; i++) {
    const lit = i < completed
    dots.push(
      <div
        key={i}
        className="absolute left-1/2 h-2.5 w-2.5 -translate-x-1/2 rounded-full"
        style={{
          bottom: `calc(${((i + 0.5) / n) * 100}% - 5px)`,
          background: lit ? liquid.dot : 'rgba(255,255,255,0.12)',
          boxShadow: lit
            ? '0 0 9px 1px rgba(255,255,255,0.55), inset 0 0 3px rgba(255,255,255,0.9)'
            : 'none',
          transition: 'background 0.2s ease, box-shadow 0.2s ease',
        }}
      />
    )
  }

  return (
    <div className="flex w-40 flex-col items-center">
      {/* ===== liquid-glass track ===== */}
      <div
        className="relative w-20 overflow-hidden rounded-[28px]"
        style={{
          height: 'min(58vh, 540px)',
          // frosted glass tube
          background:
            'linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.015))',
          backdropFilter: 'blur(14px) saturate(150%)',
          WebkitBackdropFilter: 'blur(14px) saturate(150%)',
          border: '1px solid rgba(255,255,255,0.16)',
          boxShadow:
            'inset 0 1px 0 rgba(255,255,255,0.35), inset 0 -16px 40px rgba(255,255,255,0.04), inset 0 0 0 1px rgba(255,255,255,0.03), 0 24px 60px -28px rgba(0,0,0,0.8)',
        }}
      >
        {/* glass vertical specular streak (left) */}
        <div
          className="pointer-events-none absolute inset-y-2 left-[14%] w-[10%] rounded-full"
          style={{
            background:
              'linear-gradient(180deg, rgba(255,255,255,0.5), rgba(255,255,255,0.05))',
            filter: 'blur(2px)',
            opacity: 0.5,
            zIndex: 3,
          }}
        />

        {/* ===== the liquid fill, rising from the bottom ===== */}
        <div
          className="absolute inset-x-0 bottom-0"
          style={{
            height: `${fillPct}%`,
            transition: 'height 0.5s cubic-bezier(0.22,1,0.36,1)',
            zIndex: 1,
          }}
        >
          {/* liquid body */}
          <div
            className="absolute inset-0"
            style={{ background: liquid.body }}
          />
          {/* internal sheen sweeping down the liquid */}
          <div
            className="absolute inset-0"
            style={{
              background:
                'linear-gradient(115deg, transparent 35%, rgba(255,255,255,0.22) 50%, transparent 65%)',
            }}
          />
          {/* bright glossy surface line at the top of the liquid */}
          <div
            className="absolute inset-x-0 top-0 h-[3px] rounded-full"
            style={{
              background: liquid.surface,
              boxShadow: liquid.glow,
            }}
          />
          {/* a soft meniscus highlight just under the surface */}
          <div
            className="absolute inset-x-0 top-0 h-5"
            style={{
              background:
                'linear-gradient(180deg, rgba(255,255,255,0.30), transparent)',
            }}
          />
        </div>

        {/* per-call dots (above the liquid) */}
        <div className="absolute inset-0" style={{ zIndex: 2 }}>
          {dots}
        </div>

        {/* top glass rim highlight */}
        <div
          className="pointer-events-none absolute inset-x-0 top-0 h-px"
          style={{ background: 'rgba(255,255,255,0.4)', zIndex: 4 }}
        />
      </div>

      {/* labels under the bar */}
      <div className="mt-4 text-center">
        <div className="text-sm font-semibold text-white">{label}</div>
        <div
          className="tnum mt-1 text-sm font-light"
          style={{ color: cyan ? '#ff9d3c' : 'rgba(255,255,255,0.55)' }}
        >
          {completed}/{total} calls
          {done && <> · {Math.round(doneMs)} ms</>}
        </div>
        {won && done && (
          <div className="mt-1 text-xs font-light text-cyan">finished first</div>
        )}
      </div>
    </div>
  )
}

function deriveLane(name, events) {
  let completed = 0
  let doneMs = null
  for (const e of events) {
    if (e.lane !== name) continue
    if (e.ev === 'call' && e.phase === 'end') completed += 1
    else if (e.ev === 'lane_done') doneMs = e.total_ms
  }
  return { completed, doneMs }
}
