export default function EngagementSparkline({ points }) {
  const w = 240
  const h = 52
  if (points.length < 2) {
    return (
      <div className="h-[52px] flex items-center text-xs text-white/35">
        waiting for sentiment…
      </div>
    )
  }
  const xs = points.map((_, i) => (i / (points.length - 1)) * (w - 8) + 4)
  const ys = points.map((p) => h - 6 - (p / 10) * (h - 12))
  const path = xs.map((x, i) => `${x},${ys[i]}`).join(' ')
  const rising = points[points.length - 1] >= points[0]
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-[52px]">
      <polyline
        points={path}
        fill="none"
        stroke={rising ? '#34d399' : '#ff5436'}
        strokeWidth="2"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle
        cx={xs[xs.length - 1]}
        cy={ys[ys.length - 1]}
        r="3.5"
        fill={rising ? '#34d399' : '#ff5436'}
      />
    </svg>
  )
}
