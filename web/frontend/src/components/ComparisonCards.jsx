import NumberTicker from './NumberTicker'

// Two glass cards racing side by side. The "With Engram" card carries the cyan
// shader accent; the baseline stays muted white. Bars fill proportional to time
// (longest = 100%), animated from zero so the gap is felt, not just read.
export default function ComparisonCards({ data }) {
  const max = Math.max(data.baseline_total_ms, data.engram_total_ms, 1)
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card
        title="Without Engram"
        ms={data.baseline_total_ms}
        pct={(data.baseline_total_ms / max) * 100}
        tone="warm"
        delay={0}
      />
      <Card
        title="With Engram"
        ms={data.engram_total_ms}
        pct={(data.engram_total_ms / max) * 100}
        tone="cyan"
        delay={120}
      />
    </div>
  )
}

function Card({ title, ms, pct, tone, delay }) {
  const cyan = tone === 'cyan'
  const accent = cyan
    ? 'linear-gradient(90deg, rgba(255,157,60,0.5), #ff9d3c)'
    : 'linear-gradient(90deg, rgba(255,255,255,0.25), rgba(255,255,255,0.5))'
  const glow = cyan
    ? 'radial-gradient(120% 120% at 100% 0%, rgba(255,157,60,0.12), transparent 55%)'
    : 'none'

  return (
    <div
      className="glass relative overflow-hidden rounded-3xl p-6"
      style={{ animation: `rise 0.5s ${delay}ms ease both` }}
    >
      <div className="pointer-events-none absolute inset-0" style={{ background: glow }} />
      <div className="relative">
        <div className="text-sm font-semibold text-white">{title}</div>

        <div className="mt-6 flex items-end gap-1.5">
          <NumberTicker
            value={ms}
            decimals={0}
            className="text-5xl font-semibold tracking-[-0.03em] text-white"
          />
          <span className="mb-1.5 text-lg font-light text-mute">ms</span>
        </div>

        <div className="mt-5 h-2.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
          <div
            className="h-full rounded-full"
            style={{
              width: `${pct}%`,
              background: accent,
              boxShadow: cyan ? '0 0 16px rgba(255,157,60,0.4)' : 'none',
              transformOrigin: 'left',
              animation: `grow 0.9s ${delay + 150}ms cubic-bezier(0.22,1,0.36,1) both`,
            }}
          />
        </div>
      </div>
    </div>
  )
}
