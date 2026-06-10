import { useCallback, useEffect, useRef, useState } from 'react'

/** Gapless streaming playback of 24kHz PCM16 base64 chunks via Web Audio. */
export function useAgentAudio() {
  const ctxRef = useRef(null)
  const nextTimeRef = useRef(0)
  const activeRef = useRef(0)
  const [playing, setPlaying] = useState(false)

  const playChunk = useCallback((b64) => {
    if (!b64) return
    try {
      if (!ctxRef.current) ctxRef.current = new AudioContext()
      const ctx = ctxRef.current
      if (ctx.state === 'suspended') ctx.resume()

      const bin = atob(b64)
      const bytes = new Uint8Array(bin.length)
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
      const i16 = new Int16Array(bytes.buffer, 0, Math.floor(bytes.length / 2))
      if (i16.length === 0) return
      const f32 = new Float32Array(i16.length)
      for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768

      const buf = ctx.createBuffer(1, f32.length, 24000)
      buf.copyToChannel(f32, 0)
      const src = ctx.createBufferSource()
      src.buffer = buf
      src.connect(ctx.destination)

      const t = Math.max(ctx.currentTime + 0.03, nextTimeRef.current)
      src.start(t)
      nextTimeRef.current = t + buf.duration

      activeRef.current += 1
      setPlaying(true)
      src.onended = () => {
        activeRef.current -= 1
        if (activeRef.current <= 0) {
          activeRef.current = 0
          setPlaying(false)
        }
      }
    } catch (e) {
      console.error('agent audio chunk failed', e)
    }
  }, [])

  useEffect(() => () => ctxRef.current?.close(), [])

  return { playChunk, playing }
}
