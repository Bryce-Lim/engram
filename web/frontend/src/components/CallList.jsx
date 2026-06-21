// Per-call breakdown: each call in order, its served time, and whether Engram
// served it warm or it ran fresh — shown as plain text (no pills). Side-effecting
// tools are noted as never speculated. Glass surface, cyan = warm.
export default function CallList({ calls, latencyMs }) {
  return (
    <div className="glass rounded-3xl p-2">
      <div className="px-4 pb-2 pt-3 text-xs font-light text-faint">
        Per-call breakdown · server latency {latencyMs} ms/call · a warm hit
        returns in ~0 ms
      </div>
      <div className="scroll-thin max-h-80 overflow-y-auto">
        {calls.map((c, i) => (
          <Row key={i} c={c} index={i} latencyMs={latencyMs} />
        ))}
      </div>
    </div>
  )
}

function Row({ c, index, latencyMs }) {
  const warm = c.outcome === 'hit'
  const barPct = Math.min(100, (c.ms / Math.max(latencyMs, 1)) * 100)
  return (
    <div
      className="flex items-center gap-4 rounded-2xl px-4 py-3 transition hover:bg-white/[0.03]"
      style={{ animation: `rise 0.4s ${index * 40}ms ease both` }}
    >
      <div className="tnum w-5 shrink-0 text-right text-xs font-light text-white/30">
        {index + 1}
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="font-semibold text-white">{c.name}</span>
          <span className="truncate text-xs font-light text-faint">
            {argPreview(c.arguments)}
          </span>
        </div>
        {!c.read_only && (
          <div className="mt-0.5 text-[11px] font-light text-faint">
            side-effecting — never speculated by design
          </div>
        )}
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-white/[0.05]">
          <div
            className="h-full rounded-full"
            style={{
              width: `${warm ? 4 : barPct}%`,
              background: warm
                ? 'linear-gradient(90deg, rgba(255,157,60,0.6), #ff9d3c)'
                : 'rgba(255,255,255,0.4)',
              boxShadow: warm ? '0 0 8px rgba(255,157,60,0.5)' : 'none',
              transformOrigin: 'left',
              animation: `grow 0.7s ${index * 40 + 120}ms cubic-bezier(0.22,1,0.36,1) both`,
            }}
          />
        </div>
      </div>

      <div className="w-28 shrink-0 text-right">
        <div className="tnum text-sm font-semibold text-white">
          {c.ms < 1 ? '<1' : Math.round(c.ms)} ms
        </div>
        <div
          className="text-[11px] font-light"
          style={{ color: warm ? '#ff9d3c' : 'rgba(255,255,255,0.45)' }}
        >
          {warm ? 'served warm' : 'ran fresh'}
        </div>
      </div>
    </div>
  )
}

function argPreview(args) {
  const s = JSON.stringify(args || {})
  if (s === '{}') return ''
  return s.length > 38 ? s.slice(0, 35) + '…' : s
}
