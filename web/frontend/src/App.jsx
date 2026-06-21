import { useRef, useState } from 'react'
import ShaderBackground from './components/ShaderBackground'
import HeroPrompt from './components/HeroPrompt'
import RaceTrack from './components/RaceTrack'
import StatRow from './components/StatRow'
import ComparisonCards from './components/ComparisonCards'
import CallList from './components/CallList'

const LATENCY = 0.4
const THINK = 1.0

const EXAMPLES = [
  'Investigate a refund dispute for Alice and Bob — pull their orders and profiles, check invoice ORD-1001, review system health, then email Alice a resolution.',
  'Look up the orders for carol, check her profile and tier, then review system status, metrics, and alerts.',
  'Pull the recent orders for dave and fetch the invoice for ORD-1002.',
]

export default function App() {
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [data, setData] = useState(null)
  const [events, setEvents] = useState([])
  const [meta, setMeta] = useState(null)
  const [racing, setRacing] = useState(false)
  const [countdown, setCountdown] = useState(null) // 3,2,1,"GO" or null
  const resultsRef = useRef(null)
  const started = meta || loading || data || countdown !== null

  // Kick off a run: reset state, play a 3-2-1-GO countdown, THEN launch the
  // real race. The countdown is purely client-side, so the measured race
  // timing is untouched — it only starts once we fire launch().
  const run = (prompt) => {
    const q = (prompt ?? query).trim()
    if (!q || loading || countdown !== null) return
    setQuery(q)
    setError('')
    setData(null)
    setEvents([])
    setMeta(null)
    setRacing(false)
    setTimeout(() => {
      resultsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 150)

    const steps = [3, 2, 1, 'GO']
    let i = 0
    setCountdown(steps[0])
    const tick = () => {
      i += 1
      if (i < steps.length) {
        setCountdown(steps[i])
        setTimeout(tick, 700)
      } else {
        // "GO" has shown for one beat — clear it and launch the real race.
        setCountdown(null)
        launch(q)
      }
    }
    setTimeout(tick, 700)
  }

  const launch = async (q) => {
    setLoading(true)
    try {
      const res = await fetch('/api/compare/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: q, latency: LATENCY, think: THINK }),
      })
      if (!res.ok || !res.body) {
        const j = await res.json().catch(() => ({}))
        throw new Error(j.error || 'request failed')
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      const collected = []
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        let nl
        while ((nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl).trim()
          buf = buf.slice(nl + 1)
          if (!line) continue
          const ev = JSON.parse(line)
          if (ev.ev === 'start') {
            setMeta(ev)
            setRacing(true)
          } else if (ev.ev === 'done') {
            setData(ev.summary)
            setRacing(false)
          } else if (ev.ev === 'error') {
            throw new Error(ev.error)
          } else {
            collected.push(ev)
            setEvents(collected.slice())
          }
        }
      }
    } catch (e) {
      setError(String(e.message || e))
      setRacing(false)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="relative min-h-full bg-black">
      {/* ===== HERO ===== exact match to the source design */}
      <section className="relative h-screen w-full overflow-hidden bg-black text-white">
        <ShaderBackground
          intensity={1}
          className="absolute inset-0 z-0 h-full w-full"
        />
        {/* radial darken so the headline reads */}
        <div
          className="absolute inset-0 z-[1]"
          style={{
            background:
              'radial-gradient(54% 50% at 50% 46%, rgba(0,0,0,0.62) 0%, rgba(0,0,0,0.12) 58%, rgba(0,0,0,0) 80%)',
          }}
        />

        {/* logo */}
        <div className="absolute left-8 top-7 z-[3] flex items-center gap-[9px]">
          <span className="text-[21px] font-light tracking-[-0.01em] text-white">
            Engram
          </span>
        </div>

        {/* centered headline + prompt */}
        <div className="relative z-[2] flex h-full w-full flex-col items-center justify-center px-6 text-center">
          <h1
            className="m-0 max-w-[960px] font-semibold leading-[1.0] tracking-[-0.04em] text-white"
            style={{
              fontSize: 'clamp(42px,7vw,86px)',
              textWrap: 'balance',
              textShadow: '0 2px 50px rgba(0,0,0,0.45)',
            }}
          >
            Your agent's next call is already done.
          </h1>

          <HeroPrompt
            value={query}
            onChange={setQuery}
            onSubmit={() => run()}
            loading={loading}
          />

          <div className="mt-6 flex flex-wrap items-center justify-center gap-x-5 gap-y-2">
            {EXAMPLES.map((ex, i) => (
              <button
                key={i}
                onClick={() => run(ex)}
                disabled={loading}
                className="max-w-[260px] truncate text-[13px] font-light text-faint transition hover:text-white disabled:opacity-40"
                title={ex}
              >
                {exLabel(i)}
              </button>
            ))}
          </div>

          {!started && (
            <div className="absolute bottom-10 left-1/2 -translate-x-1/2 text-[13px] font-light text-white/30">
              Enter a prompt to race it — with and without Engram.
            </div>
          )}
        </div>
      </section>

      {/* ===== COMPARISON ===== subtle, fully static background ===== */}
      {started && (
        <section
          ref={resultsRef}
          className="relative w-full overflow-hidden bg-black"
        >
          {/* A crisp orange hairline where this section meets the hero, with a
              faint warm bloom beneath it that dissolves into pure black. Fully
              static — no shader, no animation. */}
          <div
            className="pointer-events-none absolute inset-x-0 top-0 z-0 h-px"
            style={{
              background:
                'linear-gradient(90deg, transparent, rgba(255,157,60,0.55), transparent)',
            }}
          />
          <div
            className="pointer-events-none absolute inset-x-0 top-0 z-0 h-72"
            style={{
              background:
                'radial-gradient(80% 100% at 50% 0%, rgba(255,157,60,0.10) 0%, transparent 70%)',
            }}
          />

          <div className="relative z-[2] mx-auto w-full max-w-3xl px-6 py-20">
            <div className="mb-2 text-[13px] font-light tracking-[-0.01em] text-cyan">
              The race
            </div>
            <h2 className="mb-10 text-3xl font-semibold tracking-[-0.03em] text-white sm:text-4xl">
              Same plan, two timelines.
            </h2>

            {error && (
              <div className="glass mb-8 rounded-3xl px-6 py-5 text-sm font-light text-white/80">
                <span className="text-cyan">Couldn't run the race.</span> {error}
              </div>
            )}

            {countdown !== null && <Countdown value={countdown} />}

            {meta && (
              <div className="space-y-5">
                <RaceTrack
                  running={racing}
                  events={events}
                  numCalls={meta.num_calls}
                  latencyMs={meta.latency_ms}
                  thinkMs={meta.think_ms}
                />
              </div>
            )}

            {data && (
              <div className="mt-5 space-y-5">
                <StatRow data={data} />
                <ComparisonCards data={data} />
                <CallList calls={data.calls} latencyMs={data.latency_ms} />
                <Reasoning text={data.reasoning} />
              </div>
            )}

            {loading && !meta && countdown === null && (
              <div className="py-10 text-center text-[15px] font-light text-mute">
                Spinning up two agents…
              </div>
            )}
          </div>
        </section>
      )}
    </div>
  )
}

function exLabel(i) {
  return ['Refund dispute', 'Account health', 'Quick lookup'][i] || 'Example'
}

function Reasoning({ text }) {
  return (
    <div className="glass rounded-3xl p-6">
      <p className="text-sm font-light leading-relaxed text-white/80">{text}</p>
    </div>
  )
}

// 3-2-1-GO countdown shown before the race launches. `key={value}` restarts the
// pop animation on every beat. Big orange figure, centered in a tall panel so
// the layout doesn't jump when the race bars replace it.
function Countdown({ value }) {
  const isGo = value === 'GO'
  return (
    <div
      className="glass flex items-center justify-center rounded-3xl"
      style={{ height: 'min(58vh, 540px)' }}
    >
      <div
        key={value}
        className="font-semibold tracking-[-0.04em] text-cyan"
        style={{
          fontSize: isGo ? 'clamp(64px,12vw,150px)' : 'clamp(90px,16vw,210px)',
          textShadow: '0 0 60px rgba(255,157,60,0.55)',
          animation: 'pop 0.5s cubic-bezier(0.22,1,0.36,1) both',
        }}
      >
        {value}
      </div>
    </div>
  )
}
