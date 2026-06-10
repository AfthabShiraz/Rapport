import {
  CategoryScale,
  Chart as ChartJS,
  Filler,
  LineElement,
  LinearScale,
  PointElement,
  Tooltip,
} from 'chart.js'
import { Line } from 'react-chartjs-2'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Tooltip)

export default function EngagementChart({ perCallEngagement }) {
  const data = {
    labels: perCallEngagement.map((_, i) => i + 1),
    datasets: [
      {
        data: perCallEngagement,
        borderColor: '#e6dccd',
        backgroundColor: 'rgba(230,220,205,0.08)',
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 2.5,
      },
    ],
  }
  const tick = '#b0a596'
  const gridColor = 'rgba(255,255,255,0.06)'
  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { maxTicksLimit: 10, color: tick }, grid: { display: false } },
      y: { min: 0, max: 10, ticks: { maxTicksLimit: 6, color: tick }, grid: { color: gridColor } },
    },
  }
  return (
    <div className="h-56">
      <Line data={data} options={options} />
    </div>
  )
}
