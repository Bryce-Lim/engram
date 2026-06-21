import { useEffect, useRef, useState } from 'react'

// An animated count-up number that races from 0 (or a previous value) to the
// target. Faithful to 21st.dev's "Number Ticker": ease-out, tabular figures so
// the width doesn't jitter, and a configurable suffix/decimals.
export default function NumberTicker({
  value = 0,
  duration = 1100,
  decimals = 0,
  suffix = '',
  className = '',
}) {
  const [display, setDisplay] = useState(0)
  const fromRef = useRef(0)
  const rafRef = useRef(null)

  useEffect(() => {
    const from = fromRef.current
    const to = Number(value) || 0
    const start = performance.now()
    cancelAnimationFrame(rafRef.current)

    const tick = (now) => {
      const t = Math.min(1, (now - start) / duration)
      // easeOutExpo for a snappy, decelerating race
      const eased = t === 1 ? 1 : 1 - Math.pow(2, -10 * t)
      setDisplay(from + (to - from) * eased)
      if (t < 1) {
        rafRef.current = requestAnimationFrame(tick)
      } else {
        fromRef.current = to
      }
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
  }, [value, duration])

  const formatted = display.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })

  return (
    <span className={`tnum ${className}`}>
      {formatted}
      {suffix}
    </span>
  )
}
