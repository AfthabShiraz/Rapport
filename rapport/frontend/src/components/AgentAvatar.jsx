// Centered avatar for the AI agent's video tile.
// Pulses a ring while the agent is speaking. Kept deliberately compact so the
// agent tile reads as secondary to the prospect's (larger) webcam tile.
export default function AgentAvatar({ speaking, name = 'Aria' }) {
  return (
    <div className="relative h-24 w-24">
      {speaking && (
        <span className="absolute inset-0 rounded-full bg-violet-500/40 animate-ping" />
      )}
      <div
        className={`relative flex h-24 w-24 items-center justify-center rounded-full bg-gradient-to-br from-violet-400 to-indigo-600 text-4xl font-bold text-white shadow-lg ring-2 transition ${
          speaking ? 'ring-violet-300' : 'ring-white/20'
        }`}
      >
        {name[0]?.toUpperCase()}
      </div>
    </div>
  )
}
