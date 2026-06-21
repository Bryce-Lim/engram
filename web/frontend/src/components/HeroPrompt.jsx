// The hero prompt pill — a 1:1 recreation of the source design's glass input:
// 76px tall, 24px radius, layered highlight + skewed shine sweep, 21px/300 text,
// and the 54px square arrow button with the exact two-stroke chevron icon.
export default function HeroPrompt({ value, onChange, onSubmit, loading }) {
  const submit = (e) => {
    e.preventDefault()
    onSubmit()
  }

  return (
    <form onSubmit={submit} className="mt-[54px] w-full max-w-[640px]">
      <div
        className="relative flex h-[76px] items-center gap-3 overflow-hidden rounded-[24px] pl-7 pr-[11px]"
        style={{
          background:
            'linear-gradient(135deg, rgba(255,255,255,0.045) 0%, rgba(255,255,255,0.01) 100%)',
          backdropFilter: 'blur(26px) saturate(160%)',
          WebkitBackdropFilter: 'blur(26px) saturate(160%)',
          border: '1px solid rgba(255,255,255,0.12)',
          boxShadow:
            'inset 0 1px 0 rgba(255,255,255,0.2), inset 0 -10px 28px rgba(255,255,255,0.02), 0 26px 80px -30px rgba(0,0,0,0.75)',
        }}
      >
        {/* top highlight */}
        <div
          className="pointer-events-none absolute inset-0 rounded-[24px]"
          style={{
            background:
              'linear-gradient(180deg, rgba(255,255,255,0.12) 0%, rgba(255,255,255,0) 42%)',
          }}
        />
        {/* skewed shine */}
        <div
          className="pointer-events-none absolute top-0 h-full"
          style={{
            left: '-30%',
            width: '55%',
            background:
              'linear-gradient(90deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0.1) 50%, rgba(255,255,255,0) 100%)',
            transform: 'skewX(-18deg)',
          }}
        />
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Enter a prompt..."
          className="relative min-w-0 flex-1 border-none bg-transparent font-light tracking-[-0.01em] text-white caret-white outline-none"
          style={{ fontSize: 21 }}
        />
        <button
          type="submit"
          aria-label="Submit"
          disabled={loading || !value.trim()}
          className="pc-submit relative flex h-[54px] w-[54px] flex-none items-center justify-center rounded-[16px] text-white disabled:opacity-40"
          style={{
            border: '1px solid rgba(255,255,255,0.24)',
            background: 'rgba(255,255,255,0.12)',
            cursor: loading || !value.trim() ? 'not-allowed' : 'pointer',
          }}
        >
          {loading ? (
            <svg className="animate-spin" width="22" height="22" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="9" stroke="#fff" strokeWidth="2" opacity="0.25" />
              <path d="M21 12a9 9 0 0 0-9-9" stroke="#fff" strokeWidth="2" strokeLinecap="round" />
            </svg>
          ) : (
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#ffffff"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M5 12h13" />
              <path d="M12 5l7 7-7 7" />
            </svg>
          )}
        </button>
      </div>
    </form>
  )
}
