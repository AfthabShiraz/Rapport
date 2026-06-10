import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { createCall, getCall, getCallOptimization } from '../api/calls'
import {
  acceptOptimization,
  listOptimizations,
  rejectOptimization,
} from '../api/optimizations'
import PromptDiff from '../components/PromptDiff'
import ScoreCards from '../components/ScoreCards'

function duration(call) {
  if (!call?.started_at || !call?.ended_at) return ''
  const s = Math.max(0, Math.round((new Date(call.ended_at) - new Date(call.started_at)) / 1000))
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
}

export default function ReportPage() {
  const { callId } = useParams()
  const navigate = useNavigate()
  const [call, setCall] = useState(null)
  const [opt, setOpt] = useState(null)
  const [allOpts, setAllOpts] = useState([])
  const [decided, setDecided] = useState(null) // accepted | rejected
  const [busy, setBusy] = useState(false)
  const pollRef = useRef(null)

  useEffect(() => {
    let alive = true
    getCall(callId)
      .then(({ call }) => {
        if (!alive) return
        setCall(call)
        return listOptimizations(call.agent_id).then((o) => alive && setAllOpts(o))
      })
      .catch(() => navigate('/setup'))

    const poll = async () => {
      try {
        const o = await getCallOptimization(callId)
        if (o && alive) {
          setOpt(o)
          if (o.status !== 'pending') setDecided(o.status)
          clearInterval(pollRef.current)
        }
      } catch {
        /* keep polling */
      }
    }
    poll()
    pollRef.current = setInterval(poll, 1500)
    return () => {
      alive = false
      clearInterval(pollRef.current)
    }
  }, [callId, navigate])

  const decide = async (action) => {
    if (busy || !opt) return
    setBusy(true)
    try {
      const updated =
        action === 'accept' ? await acceptOptimization(opt.id) : await rejectOptimization(opt.id)
      setOpt(updated)
      setDecided(updated.status)
      if (call) setAllOpts(await listOptimizations(call.agent_id))
    } catch (e) {
      alert(`Failed: ${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  const startNextCall = async () => {
    if (!call || busy) return
    setBusy(true)
    try {
      const next = await createCall(call.agent_id, call.prospect_name)
      navigate(`/call/${next.id}`)
    } catch (e) {
      alert(`Failed: ${e.message}`)
      setBusy(false)
    }
  }

  const accepted = allOpts.filter((o) => o.status === 'accepted').length
  const lifts = allOpts
    .map((o) => parseFloat(o.engagement_lift))
    .filter((v) => !Number.isNaN(v))
  const avgLift = lifts.length
    ? (lifts.reduce((a, b) => a + b, 0) / lifts.length).toFixed(1)
    : null

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-10 space-y-6">
        <div className="flex items-end justify-between">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-coral mb-1.5">
              Overmind
            </div>
            <h1 className="flex items-center gap-2.5 text-3xl font-extrabold tracking-tight text-ink">
              <span className="text-coral">✦</span> Optimization report
            </h1>
          </div>
          <span className="rounded-full border border-line bg-surface px-3 py-1 text-sm text-ink-soft">
            {call?.prospect_name} · {duration(call)}
          </span>
        </div>

        <ScoreCards scores={call?.scores_json} />

        {!opt ? (
          <div className="rounded-2xl border border-line bg-surface p-12 text-center shadow-card">
            <div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-coral border-t-transparent mb-3" />
            <p className="text-sm text-ink-soft">Overmind is analysing this call…</p>
          </div>
        ) : (
          <>
            <section className="rounded-2xl border border-line bg-surface p-6 shadow-card">
              <h2 className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-wide text-ink-soft mb-4">
                <span className="text-coral">▲</span> Failure diagnosis
              </h2>
              <ol className="space-y-3">
                {opt.diagnosis_json.map((d, i) => (
                  <li key={i} className="text-sm leading-relaxed text-ink">
                    <span className="font-semibold">
                      {i + 1}. {d.title}
                      {d.timestamp ? ` (${d.timestamp})` : ''}.
                    </span>{' '}
                    <span className="text-ink-soft">{d.detail}</span>
                  </li>
                ))}
              </ol>
            </section>

            <section className="rounded-2xl border border-line bg-surface p-6 space-y-4 shadow-card">
              <h2 className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-wide text-ink-soft">
                <span className="text-coral">⚐</span> Recommended prompt change
              </h2>
              <PromptDiff before={opt.before_behavior} after={opt.after_behavior} />

              {decided === 'accepted' ? (
                <div className="flex items-center justify-between rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3">
                  <p className="text-sm text-emerald-700 font-medium">
                    ✓ Optimization applied — next call will use the updated prompt.
                  </p>
                  <button
                    onClick={startNextCall}
                    disabled={busy}
                    className="rounded-xl bg-coral px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-coral-dark disabled:opacity-50"
                  >
                    Start next call →
                  </button>
                </div>
              ) : decided === 'rejected' ? (
                <p className="text-sm text-ink-soft">Optimization rejected.</p>
              ) : (
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => decide('accept')}
                    disabled={busy}
                    className="rounded-xl bg-coral px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-coral-dark disabled:opacity-50"
                  >
                    ✓ Accept & apply to next call
                  </button>
                  <button
                    onClick={() => decide('reject')}
                    disabled={busy}
                    className="rounded-xl border border-line-strong px-4 py-2.5 text-sm font-medium text-ink-soft transition hover:bg-cream-deep disabled:opacity-50"
                  >
                    Reject
                  </button>
                  <span className="ml-auto text-xs text-ink-faint">
                    Overmind will track if this improves conversion
                  </span>
                </div>
              )}
            </section>
          </>
        )}

        <footer className="flex items-center gap-3 border-t border-line pt-5 pb-8">
          <div className="flex gap-1.5">
            {allOpts
              .slice()
              .reverse()
              .map((o) => (
                <span
                  key={o.id}
                  title={o.status}
                  className={`h-2.5 w-2.5 rounded-full ${
                    o.id === opt?.id
                      ? 'bg-coral'
                      : o.status === 'accepted'
                        ? 'bg-emerald-500'
                        : 'bg-line-strong'
                  }`}
                />
              ))}
          </div>
          <span className="text-xs text-ink-soft">
            {accepted} of {allOpts.length} optimizations applied
            {avgLift !== null && ` · avg lift ${avgLift > 0 ? '+' : ''}${avgLift} engagement`}
          </span>
        </footer>
      </div>
    </div>
  )
}
