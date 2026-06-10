import { useEffect, useRef } from 'react'

export default function TranscriptFeed({ turns, interim, flaggedTurnIds, prospectName }) {
  const endRef = useRef(null)
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns.length, interim])

  const firstName = (prospectName || 'Prospect').split(' ')[0]

  return (
    <div className="h-[160px] overflow-y-auto bg-surface border-t border-line px-5 py-3 space-y-2 text-sm">
      {turns.map((t) => (
        <div key={t.id} className="flex gap-3">
          <span
            className={`shrink-0 w-14 font-semibold text-xs pt-0.5 ${
              t.speaker === 'agent' ? 'text-coral-ink' : 'text-ink-soft'
            }`}
          >
            {t.speaker === 'agent' ? 'Agent' : firstName}
          </span>
          <span className="text-ink">
            {t.text}
            {flaggedTurnIds.has(t.id) && (
              <span className="ml-2 inline-flex items-center rounded bg-coral-soft px-1.5 py-0.5 text-[11px] font-semibold text-coral-ink">
                ⚠ objection
              </span>
            )}
          </span>
        </div>
      ))}
      {interim && (
        <div className="flex gap-3 opacity-50">
          <span className="shrink-0 w-14 font-semibold text-xs pt-0.5 text-ink-soft">{firstName}</span>
          <span className="text-ink-soft italic">{interim}…</span>
        </div>
      )}
      {turns.length === 0 && !interim && (
        <div className="text-xs text-ink-faint">Transcript will appear here…</div>
      )}
      <div ref={endRef} />
    </div>
  )
}
