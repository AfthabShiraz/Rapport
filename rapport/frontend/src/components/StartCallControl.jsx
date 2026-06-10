import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createCall } from '../api/calls'

export default function StartCallControl({ agentId, inputRef, defaultName = '' }) {
  const [name, setName] = useState(defaultName)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const navigate = useNavigate()

  const start = async () => {
    if (!agentId || !name.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      const call = await createCall(agentId, name.trim())
      navigate(`/call/${call.id}`)
    } catch (e) {
      setError(String(e.message || e))
      setBusy(false)
    }
  }

  return (
    <div className="flex items-center gap-2">
      <input
        ref={inputRef}
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && start()}
        placeholder="Prospect name"
        className="rounded-xl border border-line-strong bg-surface px-3.5 py-2.5 text-sm w-44 text-ink placeholder:text-ink-faint focus:outline-none focus:border-coral focus:ring-2 focus:ring-coral/25"
      />
      <button
        onClick={start}
        disabled={!agentId || !name.trim() || busy}
        className="inline-flex items-center gap-1.5 rounded-xl bg-coral px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-coral-dark disabled:opacity-40"
      >
        {busy ? 'Starting…' : 'Start call'}
        {!busy && <span aria-hidden>→</span>}
      </button>
      {!agentId && <span className="text-xs text-ink-faint">save the agent first</span>}
      {error && <span className="text-xs text-coral-ink">{error}</span>}
    </div>
  )
}
