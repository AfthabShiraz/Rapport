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
        borderColor: '#2b2622',
        backgroundColor: 'rgba(43,38,34,0.07)',
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 2.5,
      },
    ],
  }
  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { maxTicksLimit: 10 }, grid: { display: false } },
      y: { min: 0, max: 10, ticks: { maxTicksLimit: 6 }, grid: { color: 'rgba(0,0,0,0.04)' } },
    },
  }
  return (
    <div className="h-56">
      <Line data={data} options={options} />
    </div>
  )
}
