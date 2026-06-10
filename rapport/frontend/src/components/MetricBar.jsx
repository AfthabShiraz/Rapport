function colorFor(pct, inverted) {
  const v = inverted ? 100 - pct : pct
  if (v < 40) return 'bg-red-500'
  if (v < 60) return 'bg-amber-500'
  return 'bg-emerald-500'
}

export default function MetricBar({ label, percent, inverted = false }) {
  const pct = Math.max(0, Math.min(100, Math.round(percent)))
  return (
    <div>
      <div className="flex justify-between text-xs mb-1.5">
        <span className="text-white/55">{label}</span>
        <span className="font-semibold tabular-nums text-white">{pct}%</span>
      </div>
      <div className="h-1.5 rounded-full bg-white/10 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-700 ${colorFor(pct, inverted)}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}
