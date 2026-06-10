import { useEffect, useState } from 'react'
import { getAgent } from '../api/agents'
import { getAnalytics } from '../api/analytics'
import { listOptimizations } from '../api/optimizations'
import ConversionChart from '../components/ConversionChart'
import EngagementChart from '../components/EngagementChart'
import StatCards from '../components/StatCards'
import { resolveActiveAgentId } from '../state/activeAgent'

export default function AnalyticsPage() {
  const [agent, setAgent] = useState(null)
  const [analytics, setAnalytics] = useState(null)
  const [opts, setOpts] = useState([])
  const [error, setError] = useState(null)

  useEffect(() => {
    ;(async () => {
      try {
        const id = await resolveActiveAgentId()
        if (!id) {
          setError('No agent deployed yet — create one on the Setup screen.')
          return
        }
        const [a, an, o] = await Promise.all([
          getAgent(id),
          getAnalytics(id),
          listOptimizations(id),
        ])
        setAgent(a)
        setAnalytics(an)
        setOpts(o.filter((x) => x.status === 'accepted'))
      } catch (e) {
        setError(String(e.message || e))
      }
    })()
  }, [])

  if (error) {
    return <div className="flex-1 flex items-center justify-center text-sm text-ink-soft">{error}</div>
  }
  if (!analytics) {
    return <div className="flex-1 flex items-center justify-center text-sm text-ink-faint">Loading…</div>
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-10 space-y-6">
        <div className="flex items-end justify-between">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-coral mb-1.5">
              Agent performance
            </div>
            <h1 className="flex items-center gap-2.5 text-3xl font-extrabold tracking-tight text-ink">
              <svg className="h-6 w-6 text-coral" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.2} d="M3 17l5-6 4 4 6-8" />
              </svg>
              {agent?.brand_name}
              {analytics.seeded && (
                <span className="rounded-full bg-cream-deep px-2 py-0.5 text-[11px] font-medium text-ink-soft">
                  demo data
                </span>
              )}
            </h1>
          </div>
          <span className="rounded-full border border-line bg-surface px-3 py-1 text-sm text-ink-soft">
            {analytics.total_calls} calls · {analytics.optimizations_applied} optimizations applied
          </span>
        </div>

        <StatCards analytics={analytics} />

        <div className="grid grid-cols-2 gap-4">
          <div className="rounded-2xl border border-line bg-surface p-5 shadow-card">
            <h2 className="text-[13px] font-semibold uppercase tracking-wide text-ink-soft mb-4">Conversion rate over calls</h2>
            <ConversionChart
              perCallConverted={analytics.per_call_converted}
              optIndices={analytics.optimization_call_indices || []}
            />
          </div>
          <div className="rounded-2xl border border-line bg-surface p-5 shadow-card">
            <h2 className="text-[13px] font-semibold uppercase tracking-wide text-ink-soft mb-4">Avg engagement score</h2>
            <EngagementChart perCallEngagement={analytics.per_call_engagement} />
          </div>
        </div>

        <section className="rounded-2xl border border-line bg-surface p-6 shadow-card">
          <h2 className="text-[13px] font-semibold uppercase tracking-wide text-ink-soft mb-4">Optimizations applied</h2>
          {opts.length === 0 ? (
            <p className="text-xs text-ink-faint">No optimizations accepted yet.</p>
          ) : (
            <ol className="space-y-2.5">
              {opts.map((o, i) => (
                <li key={o.id} className="flex items-center gap-3 text-sm">
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-coral-soft text-xs font-bold text-coral-ink">
                    {i + 1}
                  </span>
                  <span className="text-ink truncate">{o.after_behavior}</span>
                  <span
                    className={`ml-auto shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                      o.engagement_lift
                        ? 'bg-emerald-50 text-emerald-700'
                        : 'bg-cream-deep text-ink-soft'
                    }`}
                  >
                    {o.engagement_lift || 'tracking…'}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
    </div>
  )
}
