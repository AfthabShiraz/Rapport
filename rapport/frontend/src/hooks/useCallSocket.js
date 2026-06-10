import { useEffect, useRef, useState } from 'react'

/** Single WebSocket per call. Dispatches parsed events to onEvent, exposes send(). */
export function useCallSocket(callId, onEvent) {
  const [ready, setReady] = useState(false)
  const wsRef = useRef(null)
  const onEventRef = useRef(onEvent)
  onEventRef.current = onEvent

  useEffect(() => {
    if (!callId) return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/ws/calls/${callId}`)
    wsRef.current = ws
    ws.onopen = () => setReady(true)
    ws.onclose = () => setReady(false)
    ws.onmessage = (e) => {
      try {
        onEventRef.current(JSON.parse(e.data))
      } catch {
        /* ignore malformed frames */
      }
    }
    return () => {
      ws.onmessage = null
      ws.close()
    }
  }, [callId])

  const send = (obj) => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj))
  }

  return { ready, send }
}
