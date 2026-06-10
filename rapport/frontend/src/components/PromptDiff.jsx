export default function PromptDiff({ before, after }) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <div className="rounded-2xl border border-coral/25 bg-coral-soft/60 p-4">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-coral-ink mb-2">
          Current behaviour
        </div>
        <p className="text-sm text-ink leading-relaxed">{before}</p>
      </div>
      <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-emerald-700 mb-2">
          Optimized behaviour
        </div>
        <p className="text-sm text-emerald-950 leading-relaxed">{after}</p>
      </div>
    </div>
  )
}
