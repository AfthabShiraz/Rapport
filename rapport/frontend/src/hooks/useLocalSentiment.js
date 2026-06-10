import { useEffect } from 'react'
import { startLocalSentiment } from '../lib/localSentiment'

/** Browser-side face + voice-tone sentiment; samples go to onSample. */
export function useLocalSentiment(stream, videoRef, voiceEnabledRef, onSample) {
  useEffect(() => {
    if (!stream) return
    const stop = startLocalSentiment({
      stream,
      videoEl: videoRef.current,
      voiceEnabledRef,
      onSample,
    })
    return stop
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream])
}
