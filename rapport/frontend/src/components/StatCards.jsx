function fmtDuration(s) {
  return `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, '0')}s`
}

function delta(arr, fmt) {
  if (!arr || arr.length < 2) return null
  const d = arr[arr.length - 1] - arr[0]
  if (d === 0) return null
  return `${d > 0 ? '↑ +' : '↓ '}${fmt(d)} vs call 1`
}

export default function StatCards({ analytics }) {
  const a = analytics
  const convRolling = []
  let conv = 0
  a.per_call_converted.forEach((c, i) => {
    if (c) conv++
    convRolling.push(Math.round((conv / (i + 1)) * 100))
  })

  const cards = [
    {
      label: 'Conversion rate',
      value: `${Math.round(a.conversion_rate * 100)}%`,
      sub: delta(convRolling, (d) => `${Math.round(d)}pp`),
    },
    {
      label: 'Avg engagement',
      value: a.avg_engagement,
      sub: delta(a.per_call_engagement, (d) => d.toFixed(1)),
    },
    { label: 'Avg call duration', value: fmtDuration(a.avg_duration_seconds), sub: null },
    {
      label: 'Objections handled',
      value: `${Math.round(a.objection_handle_rate * 100)}%`,
      sub: null,
    },
  ]
  return (
    <div className="grid grid-cols-4 gap-3">
      {cards.map((c) => (
        <div key={c.label} className="rounded-2xl border border-line bg-surface p-4 shadow-card">
          <div className="text-[11px] font-medium uppercase tracking-wide text-ink-faint mb-1.5">{c.label}</div>
          <div className="text-2xl font-extrabold tracking-tight text-ink">{c.value}</div>
          {c.sub && <div className="text-xs font-medium text-emerald-600 mt-1">{c.sub}</div>}
        </div>
      ))}
    </div>
  )
}
