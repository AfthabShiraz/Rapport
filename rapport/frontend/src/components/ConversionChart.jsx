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

export default function ConversionChart({ perCallConverted, optIndices }) {
  const rolling = []
  let conv = 0
  perCallConverted.forEach((c, i) => {
    if (c) conv++
    rolling.push(Math.round((conv / (i + 1)) * 100))
  })
  const labels = rolling.map((_, i) => i + 1)
  const markers = rolling.map((v, i) => (optIndices.includes(i) ? v : null))

  const data = {
    labels,
    datasets: [
      {
        data: rolling,
        borderColor: '#ff5436',
        backgroundColor: 'rgba(255,84,54,0.12)',
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 2.5,
      },
      {
        data: markers,
        pointStyle: 'triangle',
        pointRadius: 7,
        pointBackgroundColor: '#b8341a',
        pointBorderColor: '#b8341a',
        showLine: false,
      },
    ],
  }
  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { maxTicksLimit: 10 }, grid: { display: false } },
      y: {
        min: 0,
        suggestedMax: 50,
        ticks: { callback: (v) => `${v}%`, maxTicksLimit: 5 },
        grid: { color: 'rgba(0,0,0,0.04)' },
      },
    },
  }
  return (
    <div className="h-56">
      <Line data={data} options={options} />
    </div>
  )
}
