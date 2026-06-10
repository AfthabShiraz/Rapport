import { useEffect, useRef } from 'react'

// Dark, compact transcript for the live-sentiment rail.
export default function TranscriptFeed({ turns, interim, flaggedTurnIds, prospectName }) {
  const endRef = useRef(null)
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns.length, interim])

  const firstName = (prospectName || 'Prospect').split(' ')[0]

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-white/40">
        Transcript
      </div>
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1 text-sm">
        {turns.map((t) => (
          <div key={t.id} className="leading-snug">
            <span
              className={`mr-2 text-[11px] font-semibold uppercase tracking-wide ${
                t.speaker === 'agent' ? 'text-violet-300' : 'text-emerald-300'
              }`}
            >
              {t.speaker === 'agent' ? 'Aria' : firstName}
            </span>
            <span className="text-white/85">
              {t.text}
              {flaggedTurnIds.has(t.id) && (
                <span className="ml-1.5 inline-flex items-center rounded bg-coral/20 px-1.5 py-0.5 text-[10px] font-semibold text-coral">
                  ⚠ objection
                </span>
              )}
            </span>
          </div>
        ))}
        {interim && (
          <div className="leading-snug opacity-50">
            <span className="mr-2 text-[11px] font-semibold uppercase tracking-wide text-emerald-300">
              {firstName}
            </span>
            <span className="italic text-white/70">{interim}…</span>
          </div>
        )}
        {turns.length === 0 && !interim && (
          <div className="text-xs text-white/35">Transcript will appear here…</div>
        )}
        <div ref={endRef} />
      </div>
    </div>
  )
}
