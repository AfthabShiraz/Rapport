const STYLES = {
  interested: ['bg-emerald-400', 'text-emerald-300'],
  positive: ['bg-emerald-400', 'text-emerald-300'],
  neutral: ['bg-slate-400', 'text-slate-300'],
  skeptical: ['bg-amber-400', 'text-amber-300'],
  frustrated: ['bg-amber-400', 'text-amber-300'],
}

export default function SentimentBadge({ emotion }) {
  const [dot, text] = STYLES[emotion] || STYLES.neutral
  return (
    <div className="absolute top-3 left-3 flex items-center gap-1.5 rounded-full bg-black/55 backdrop-blur px-3 py-1">
      <span className={`h-2 w-2 rounded-full ${dot}`} />
      <span className={`text-xs font-medium capitalize ${text}`}>{emotion}</span>
    </div>
  )
}
