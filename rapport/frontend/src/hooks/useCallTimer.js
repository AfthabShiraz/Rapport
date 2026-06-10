import { useEffect, useState } from 'react'

/** mm:ss elapsed since startedAt (epoch ms); 00:00 until started. */
export function useCallTimer(startedAt) {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!startedAt) return
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - startedAt) / 1000)), 1000)
    return () => clearInterval(id)
  }, [startedAt])
  const m = String(Math.floor(elapsed / 60)).padStart(2, '0')
  const s = String(elapsed % 60).padStart(2, '0')
  return `${m}:${s}`
}
