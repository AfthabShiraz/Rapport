export default function AgentSpeechBubble({ speaking, text }) {
  if (!speaking && !text) return null
  return (
    <div className="absolute bottom-3 left-3 right-3 rounded-xl bg-black/65 backdrop-blur px-4 py-3">
      {speaking && (
        <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-coral mb-1">
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-coral animate-pulse" />
          Agent (Aria) is speaking
        </div>
      )}
      <p className="text-sm text-white leading-snug">{text}</p>
    </div>
  )
}
