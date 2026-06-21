import NumberTicker from './NumberTicker'

// Three headline figures: speedup, time saved, calls warmed. Glass tiles,
// white figures with an orange-tinted gradient, light labels. No chips/badges.
export default function StatRow({ data }) {
  return (
    <div className="grid grid-cols-3 gap-4">
      <Stat label="Faster end-to-end" delay={0}>
        <NumberTicker value={data.speedup} decimals={2} suffix="×" />
      </Stat>
      <Stat label="Time saved" delay={80}>
        <NumberTicker value={data.saved_ms} decimals={0} suffix=" ms" />
      </Stat>
      <Stat label="Calls served warm" delay={160}>
        <span className="tnum">
          {data.hits}/{data.num_calls}
        </span>
      </Stat>
    </div>
  )
}

function Stat({ label, children, delay }) {
  return (
    <div
      className="glass rounded-3xl px-5 py-5 text-center"
      style={{ animation: `rise 0.5s ${delay}ms ease both` }}
    >
      <div
        className="bg-clip-text text-3xl font-semibold tracking-[-0.03em] text-transparent"
        style={{ backgroundImage: 'linear-gradient(90deg, #ffffff, #ff9d3c)' }}
      >
        {children}
      </div>
      <div className="mt-1.5 text-xs font-light text-faint">{label}</div>
    </div>
  )
}
