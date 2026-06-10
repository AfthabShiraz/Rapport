import { useEffect } from 'react'

function toBase64(buf) {
  const u = new Uint8Array(buf)
  let s = ''
  for (let i = 0; i < u.length; i += 0x8000) {
    s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000))
  }
  return btoa(s)
}

/**
 * Streams mic audio as 24kHz PCM16 base64 chunks via onChunk.
 * enabledRef is read per-chunk — the echo gate / manual mute without re-wiring audio.
 */
export function useMicCapture(stream, enabledRef, onChunk) {
  useEffect(() => {
    if (!stream || stream.getAudioTracks().length === 0) return
    let ctx
    let node
    let cancelled = false
    ;(async () => {
      try {
        ctx = new AudioContext()
        await ctx.audioWorklet.addModule('/pcm-worklet.js')
        if (cancelled) return
        const source = ctx.createMediaStreamSource(stream)
        node = new AudioWorkletNode(ctx, 'pcm-downsampler')
        node.port.onmessage = (e) => {
          if (enabledRef.current) onChunk(toBase64(e.data))
        }
        source.connect(node)
        // worklet has no output; no need to connect to destination
      } catch (e) {
        console.error('mic capture failed', e)
      }
    })()
    return () => {
      cancelled = true
      node?.port.close()
      ctx?.close()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream])
}
