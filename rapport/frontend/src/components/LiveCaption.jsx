// Floating pop-up at the base of the call stage that shows the latest
// streaming sentence live — agent text while it speaks, or the prospect's
// interim transcript while they speak.
export default function LiveCaption({ label, text, speaking, isAgent }) {
  if (!text) return null
  return (
    <div className="pointer-events-none absolute bottom-5 left-1/2 z-20 w-[min(88%,620px)] -translate-x-1/2">
      <div className="rounded-2xl bg-black/70 px-5 py-3 shadow-lift ring-1 ring-white/10 backdrop-blur-md">
        <div className="mb-1 flex items-center gap-2">
          <span
            className={`inline-block h-1.5 w-1.5 rounded-full ${speaking ? 'animate-pulse' : ''} ${
              isAgent ? 'bg-violet-400' : 'bg-emerald-400'
            }`}
          />
          <span
            className={`text-[10px] font-semibold uppercase tracking-wider ${
              isAgent ? 'text-violet-300' : 'text-emerald-300'
            }`}
          >
            {label}
            {speaking && ' · speaking'}
          </span>
        </div>
        <p className="text-[15px] leading-snug text-white">
          {text}
          {speaking && <span className="ml-0.5 animate-pulse text-white/60">…</span>}
        </p>
      </div>
    </div>
  )
}
