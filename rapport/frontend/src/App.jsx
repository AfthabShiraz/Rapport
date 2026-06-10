import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import AnalyticsPage from './pages/AnalyticsPage'
import LiveCallPage from './pages/LiveCallPage'
import ReportPage from './pages/ReportPage'
import SetupPage from './pages/SetupPage'

export default function App() {
  const { pathname } = useLocation()
  // sidebar hidden on Report (screen 3) and Analytics (screen 4)
  const showSidebar = pathname.startsWith('/setup') || pathname.startsWith('/call')

  return (
    <div className="flex h-full">
      {showSidebar && <Sidebar />}
      <Routes>
        <Route path="/" element={<Navigate to="/setup" replace />} />
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/call/:callId" element={<LiveCallPage />} />
        <Route path="/report/:callId" element={<ReportPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
      </Routes>
    </div>
  )
}
