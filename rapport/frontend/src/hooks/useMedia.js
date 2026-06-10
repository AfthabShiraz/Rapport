import { useEffect, useRef, useState } from 'react'

/** One getUserMedia for mic + camera (single permission prompt). */
export function useMedia() {
  const [stream, setStream] = useState(null)
  const [error, setError] = useState(null)
  const streamRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    navigator.mediaDevices
      .getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: { width: 640, height: 480 },
      })
      .then((s) => {
        if (cancelled) {
          s.getTracks().forEach((t) => t.stop())
          return
        }
        streamRef.current = s
        setStream(s)
      })
      .catch((e) => !cancelled && setError(e))
    return () => {
      cancelled = true
      streamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }, [])

  return { stream, error }
}
