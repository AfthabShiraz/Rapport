import { useEffect } from 'react'

/** Attaches the stream to videoRef and grabs a JPEG frame every 3s. */
export function useCamera(stream, videoRef, onFrame) {
  useEffect(() => {
    if (!stream || !videoRef.current || stream.getVideoTracks().length === 0) return
    const video = videoRef.current
    video.srcObject = stream
    video.play().catch(() => {})

    const canvas = document.createElement('canvas')
    canvas.width = 480
    canvas.height = 360
    const ctx2d = canvas.getContext('2d')

    const id = setInterval(() => {
      if (video.readyState < 2) return
      try {
        ctx2d.drawImage(video, 0, 0, canvas.width, canvas.height)
        const dataUrl = canvas.toDataURL('image/jpeg', 0.7)
        onFrame(dataUrl.split(',')[1])
      } catch {
        /* frame grab can fail transiently; skip */
      }
    }, 3000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream, videoRef])
}
