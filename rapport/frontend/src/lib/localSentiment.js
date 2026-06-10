/*
 * Browser-side multimodal sentiment (adapted from the root prototype's sentiment.js).
 *
 *   face  — face-api.js (TinyFaceDetector + faceExpressionNet), ~2.5 Hz
 *   voice — Transformers.js wav2vec2 speech-emotion, every ~3 s of mic audio
 *
 * Each sample collapses to a valence in [-1, +1] and is handed to onSample
 * ({kind, valence, scores}); the caller ships it to the backend. Models load
 * from CDN independently — a failure of one never takes down the other, and
 * total failure just means no local samples (GPT-4o vision still runs).
 */
const TRANSFORMERS_URL = 'https://cdn.jsdelivr.net/npm/@xenova/transformers@2.17.2'
const FACE_MODEL_URL = 'https://cdn.jsdelivr.net/npm/@vladmandic/face-api/model'
const VOICE_MODEL = 'onnx-community/wav2vec2-base-Speech_Emotion_Recognition-ONNX'

const FACE_INTERVAL_MS = 400
const VOICE_WINDOW_S = 3
const VOICE_RATE = 16000 // wav2vec2 expects 16 kHz mono

const clamp = (x) => Math.max(-1, Math.min(1, x))

function faceValence(e) {
  return clamp(
    (e.happy || 0) + 0.5 * (e.surprised || 0)
      - (e.angry || 0) - (e.sad || 0) - (e.disgusted || 0) - 0.5 * (e.fearful || 0)
  )
}

function labelValence(scores) {
  let pos = 0
  let neg = 0
  for (const [l, s] of Object.entries(scores)) {
    const k = l.toLowerCase()
    if (/(hap|joy|pos|surpr|excit)/.test(k)) pos += s
    else if (/(ang|sad|fear|disg|neg|frust)/.test(k)) neg += s
  }
  return clamp(pos - neg)
}

export function startLocalSentiment({ stream, videoEl, voiceEnabledRef, onSample }) {
  let stopped = false
  let faceLoop = null
  let voiceTimer = null
  let audioCtx = null
  let proc = null
  let chunks = []
  let chunkSamples = 0

  // ---- face (face-api.js UMD global, loaded via index.html script tag) ----
  ;(async () => {
    try {
      const fa = window.faceapi
      if (!fa) throw new Error('face-api global not present')
      await fa.nets.tinyFaceDetector.loadFromUri(FACE_MODEL_URL)
      await fa.nets.faceExpressionNet.loadFromUri(FACE_MODEL_URL)
      if (stopped) return
      const opts = new fa.TinyFaceDetectorOptions({ inputSize: 224, scoreThreshold: 0.4 })
      let busy = false
      faceLoop = setInterval(async () => {
        if (busy || !videoEl || videoEl.readyState < 2) return
        busy = true
        try {
          const det = await fa.detectSingleFace(videoEl, opts).withFaceExpressions()
          if (det) {
            const e = det.expressions
            const scores = {}
            for (const k of Object.keys(e)) scores[k] = Number(e[k].toFixed(3))
            onSample({ kind: 'face', valence: faceValence(e), scores })
          }
        } catch {
          /* skip frame */
        } finally {
          busy = false
        }
      }, FACE_INTERVAL_MS)
    } catch (e) {
      console.warn('local face sentiment unavailable:', e.message)
    }
  })()

  // ---- voice tone (Transformers.js, dynamic import) ----
  ;(async () => {
    try {
      const mod = await import(/* @vite-ignore */ TRANSFORMERS_URL)
      mod.env.allowLocalModels = false
      const voicePipe = await mod.pipeline('audio-classification', VOICE_MODEL)
      if (stopped || !stream || stream.getAudioTracks().length === 0) return

      audioCtx = new AudioContext({ sampleRate: VOICE_RATE })
      const src = audioCtx.createMediaStreamSource(stream)
      proc = audioCtx.createScriptProcessor(4096, 1, 1)
      const mute = audioCtx.createGain()
      mute.gain.value = 0
      src.connect(proc)
      proc.connect(mute)
      mute.connect(audioCtx.destination)
      proc.onaudioprocess = (ev) => {
        // unlike the prototype, gate on the echo control so the agent's own
        // voice through speakers never colours the prospect's tone
        if (voiceEnabledRef && !voiceEnabledRef.current) return
        const buf = new Float32Array(ev.inputBuffer.getChannelData(0))
        chunks.push(buf)
        chunkSamples += buf.length
        const max = audioCtx.sampleRate * (VOICE_WINDOW_S + 1)
        while (chunkSamples > max) {
          chunkSamples -= chunks[0].length
          chunks.shift()
        }
      }

      let busy = false
      voiceTimer = setInterval(async () => {
        if (busy || !chunks.length) return
        busy = true
        try {
          let total = 0
          for (const c of chunks) total += c.length
          if (total < VOICE_RATE * 0.5) return // need >= 0.5s of speech
          const merged = new Float32Array(total)
          let o = 0
          for (const c of chunks) {
            merged.set(c, o)
            o += c.length
          }
          const out = await voicePipe(merged)
          const scores = {}
          ;(Array.isArray(out) ? out : [out]).forEach((r) => {
            scores[r.label] = Number(r.score.toFixed(3))
          })
          onSample({ kind: 'voice', valence: labelValence(scores), scores })
        } catch {
          /* skip window */
        } finally {
          busy = false
        }
      }, VOICE_WINDOW_S * 1000)
    } catch (e) {
      console.warn('local voice sentiment unavailable:', e.message)
    }
  })()

  return function stop() {
    stopped = true
    if (faceLoop) clearInterval(faceLoop)
    if (voiceTimer) clearInterval(voiceTimer)
    try {
      proc?.disconnect()
      audioCtx?.close()
    } catch {
      /* already closed */
    }
    chunks = []
  }
}
