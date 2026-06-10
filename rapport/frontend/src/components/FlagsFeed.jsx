export default function FlagsFeed({ flags }) {
  return (
    <div className="flex-1 min-h-0 overflow-y-auto space-y-2 pr-1">
      {flags.length === 0 && <div className="text-xs text-white/35">No flags yet</div>}
      {flags.map((f, i) => (
        <div key={i} className="flex items-start gap-2 rounded-lg bg-white/[0.04] px-2.5 py-2 text-xs leading-snug">
          <span className="mt-px shrink-0">
            {f.severity === 'ok' ? '✓' : '⚠'}
          </span>
          <span className={f.severity === 'ok' ? 'text-emerald-300' : f.severity === 'error' ? 'text-coral' : 'text-amber-300'}>
            {f.message}
            <span className="text-white/30 ml-1">· {f.timestamp}</span>
          </span>
        </div>
      ))}
    </div>
  )
}
