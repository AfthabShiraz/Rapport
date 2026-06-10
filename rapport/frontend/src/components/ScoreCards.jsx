function Card({ label, value, sub, tone }) {
  const toneCls =
    tone === 'red' ? 'text-coral-ink' : tone === 'amber' ? 'text-amber-400' : 'text-emerald-400'
  return (
    <div className="rounded-2xl border border-line bg-surface p-4 shadow-card">
      <div className="text-[11px] font-medium uppercase tracking-wide text-ink-faint mb-1.5">{label}</div>
      <div className={`text-2xl font-extrabold tracking-tight ${toneCls}`}>{value}</div>
      {sub && <div className="text-xs text-ink-soft mt-1">{sub}</div>}
    </div>
  )
}

export default function ScoreCards({ scores }) {
  if (!scores) return null
  const { objection_handled, avg_engagement, sentiment_delta, outcome, engagement_vs_recent } =
    scores
  const outcomeLabel = { converted: 'Converted', follow_up: 'Follow-up', lost: 'Lost' }[outcome] || '—'
  return (
    <div className="grid grid-cols-4 gap-3">
      <Card
        label="Objection handled"
        value={objection_handled ? '✓ Yes' : '✗ No'}
        sub={objection_handled ? 'reframed in-call' : 'dropped from call'}
        tone={objection_handled ? 'green' : 'red'}
      />
      <Card
        label="Avg engagement"
        value={`${avg_engagement}/10`}
        sub={
          engagement_vs_recent === 0
            ? 'no recent calls to compare'
            : `${engagement_vs_recent > 0 ? '↑' : '↓'} ${Math.abs(engagement_vs_recent)} vs last 5 calls`
        }
        tone={avg_engagement < 4 ? 'red' : avg_engagement < 6 ? 'amber' : 'green'}
      />
      <Card
        label="Sentiment delta"
        value={sentiment_delta > 0 ? `+${sentiment_delta}` : `${sentiment_delta}`}
        sub={sentiment_delta >= 0 ? 'mood improved' : 'mood worsened'}
        tone={sentiment_delta > 0 ? 'green' : sentiment_delta > -2 ? 'amber' : 'red'}
      />
      <Card
        label="Outcome"
        value={outcomeLabel}
        sub={
          outcome === 'converted'
            ? 'commitment made'
            : outcome === 'follow_up'
              ? 'next step booked'
              : 'no follow-up booked'
        }
        tone={outcome === 'converted' ? 'green' : outcome === 'follow_up' ? 'amber' : 'red'}
      />
    </div>
  )
}
