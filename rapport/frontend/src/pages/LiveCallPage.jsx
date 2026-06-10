import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { endCall, getCall, startCall } from '../api/calls'
import AgentSpeechBubble from '../components/AgentSpeechBubble'
import EngagementSparkline from '../components/EngagementSparkline'
import FlagsFeed from '../components/FlagsFeed'
import MetricBar from '../components/MetricBar'
import SentimentBadge from '../components/SentimentBadge'
import TranscriptFeed from '../components/TranscriptFeed'
import { useAgentAudio } from '../hooks/useAgentAudio'
import { useCallSocket } from '../hooks/useCallSocket'
import { useCallTimer } from '../hooks/useCallTimer'
import { useCamera } from '../hooks/useCamera'
import { useLocalSentiment } from '../hooks/useLocalSentiment'
import { useMedia } from '../hooks/useMedia'
import { useMicCapture } from '../hooks/useMicCapture'

export default function LiveCallPage() {
  const { callId } = useParams()
  const navigate = useNavigate()

  const [call, setCall] = useState(null)
  const [turns, setTurns] = useState([])
  const [interim, setInterim] = useState('')
  const [agentText, setAgentText] = useState('')
  const [agentSpeaking, setAgentSpeaking] = useState(false)
  const [sentiment, setSentiment] = useState(null)
  const [engHistory, setEngHistory] = useState([])
  const [flags, setFlags] = useState([])
  const [flaggedTurnIds, setFlaggedTurnIds] = useState(new Set())
  const [startedAt, setStartedAt] = useState(null)
  const [muted, setMuted] = useState(false)
  const [ending, setEnding] = useState(false)

  const videoRef = useRef(null)
  const startedRef = useRef(false)
  const micEnabledRef = useRef(true)

  const { stream, error: mediaError } = useMedia()
  const audio = useAgentAudio()
  const timer = useCallTimer(startedAt)

  // echo gate: mic off while the agent is speaking/playing, or manually muted
  micEnabledRef.current = !agentSpeaking && !audio.playing && !muted

  useEffect(() => {
    getCall(callId)
      .then(({ call, turns }) => {
        setCall(call)
        setTurns(turns)
        if (call.started_at) setStartedAt(new Date(call.started_at).getTime())
      })
      .catch(() => navigate('/setup'))
  }, [callId, navigate])

  const onEvent = useCallback(
    (ev) => {
      switch (ev.type) {
        case 'call_started':
          setStartedAt(Date.now())
          break
        case 'transcript_chunk':
          setInterim(ev.text)
          break
        case 'turn_complete':
          setTurns((t) => [...t, ev.turn])
          if (ev.turn.speaker === 'prospect') setInterim('')
          break
        case 'agent_speaking':
          setAgentSpeaking(ev.value)
          break
        case 'agent_text':
          setAgentText(ev.text)
          break
        case 'agent_audio':
          audio.playChunk(ev.audio_b64)
          break
        case 'sentiment_update': {
          const { type, source, ...s } = ev
          setSentiment(s)
          // sparkline tracks the 3s vision cadence only — local face/voice
          // samples (2.5/s) would flood it with duplicate points
          if (source !== 'local') {
            setEngHistory((h) => [...h.slice(-60), s.engagement])
          }
          break
        }
        case 'flag':
          setFlags((f) => [...f, ev])
          if (ev.turn_id) {
            setFlaggedTurnIds((ids) => new Set(ids).add(ev.turn_id))
          }
          break
        case 'agent_ended_call':
          // the agent hung up via its end_call tool; let the closing audio
          // drain briefly, then end with the agent's own structured outcome
          setEnding(true)
          setTimeout(async () => {
            try {
              await endCall(callId, ev.outcome)
            } catch (e) {
              console.error(e)
            }
            navigate(`/report/${callId}`)
          }, 1500)
          break
        default:
          break
      }
    },
    [audio]
  )

  const { ready, send } = useCallSocket(callId, onEvent)

  // fire POST /start once media + socket are both up
  useEffect(() => {
    if (ready && stream && !startedRef.current) {
      startedRef.current = true
      startCall(callId).catch((e) => console.error('start failed', e))
    }
  }, [ready, stream, callId])

  useMicCapture(stream, micEnabledRef, (b64) => send({ type: 'audio_chunk', data: b64 }))
  useCamera(stream, videoRef, (b64) =>
    send({ type: 'video_frame', data: b64, timestamp: Date.now() })
  )
  useLocalSentiment(stream, videoRef, micEnabledRef, (sample) =>
    send({ type: 'local_sentiment', ...sample })
  )

  const handleEnd = async () => {
    if (ending) return
    setEnding(true)
    try {
      await endCall(callId)
    } catch (e) {
      console.error(e)
    }
    navigate(`/report/${callId}`)
  }

  const initials = (call?.prospect_name || '?')
    .split(' ')
    .map((w) => w[0])
    .join('')
    .toUpperCase()

  return (
    <div className="flex-1 flex min-h-0">
      {/* left pane */}
      <div className="flex-[65] flex flex-col min-w-0">
        <div className="flex items-center gap-3 bg-surface border-b border-line px-5 py-3">
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-coral opacity-60" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-coral" />
          </span>
          <span className="text-sm font-semibold text-ink">
            Call with {call?.prospect_name || '…'}
          </span>
          <div className="ml-auto flex items-center gap-3">
            <span
              className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                call?.optimized
                  ? 'bg-emerald-50 text-emerald-700'
                  : 'bg-coral-soft text-coral-ink'
              }`}
            >
              {call?.optimized ? 'After optimization' : 'Before optimization'}
            </span>
            <span className="rounded-md bg-cream px-2 py-1 text-sm font-mono tabular-nums text-ink-soft">
              {timer}
            </span>
          </div>
        </div>

        <div className="relative flex-1 min-h-0 bg-[#1a1a1a]">
          {stream?.getVideoTracks().length > 0 && !mediaError ? (
            <video
              ref={videoRef}
              muted
              playsInline
              className="absolute inset-0 h-full w-full object-cover"
            />
          ) : (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="h-24 w-24 rounded-full bg-white/10 flex items-center justify-center text-3xl font-bold text-white/70">
                {initials}
              </div>
            </div>
          )}
          {sentiment && <SentimentBadge emotion={sentiment.emotion} />}
          <div className="absolute top-3 right-3 rounded bg-black/55 px-2 py-1 text-xs text-slate-200">
            {call?.prospect_name} · Prospect
          </div>
          {!micEnabledRef.current && !muted && (
            <div className="absolute bottom-24 right-3 rounded bg-black/55 px-2 py-1 text-[11px] text-slate-300">
              muted (agent speaking)
            </div>
          )}
          <AgentSpeechBubble speaking={agentSpeaking || audio.playing} text={agentText} />
        </div>

        <TranscriptFeed
          turns={turns}
          interim={interim}
          flaggedTurnIds={flaggedTurnIds}
          prospectName={call?.prospect_name}
        />
      </div>

      {/* right pane */}
      <div className="flex-[35] min-w-[280px] max-w-[360px] bg-night text-white flex flex-col p-5 gap-5">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M15 12a3 3 0 11-6 0 3 3 0 016 0z M2.5 12C4.3 7.4 7.9 4.5 12 4.5s7.7 2.9 9.5 7.5c-1.8 4.6-5.4 7.5-9.5 7.5S4.3 16.6 2.5 12z"
            />
          </svg>
          Live sentiment
        </div>

        <div className="space-y-3">
          <MetricBar label="Engagement" percent={(sentiment?.engagement ?? 0) * 10} />
          <MetricBar label="Trust signal" percent={(sentiment?.trust_signal ?? 0) * 100} />
          <MetricBar
            label="Objection risk"
            percent={(sentiment?.objection_risk ?? 0) * 100}
            inverted
          />
          {/* local multimodal signals (browser-side models); valence -1..1 → % */}
          {sentiment?.voice_valence != null && (
            <MetricBar
              label="Voice tone"
              percent={((sentiment.voice_valence + 1) / 2) * 100}
            />
          )}
          {sentiment?.face_valence != null && (
            <MetricBar
              label="Facial read"
              percent={((sentiment.face_valence + 1) / 2) * 100}
            />
          )}
        </div>

        <div>
          <div className="text-[11px] font-semibold uppercase tracking-wide text-white/40 mb-1.5">
            Engagement over call
          </div>
          <EngagementSparkline points={engHistory} />
        </div>

        <FlagsFeed flags={flags} />

        <div className="flex gap-2 pt-1">
          <button
            onClick={() => setMuted((m) => !m)}
            className={`flex-1 rounded-xl px-3 py-2.5 text-sm font-semibold transition ${
              muted ? 'bg-amber-400 text-black' : 'bg-white/10 text-white hover:bg-white/15'
            }`}
          >
            {muted ? 'Unmute' : 'Mute'}
          </button>
          <button
            onClick={handleEnd}
            disabled={ending}
            className="flex-1 rounded-xl bg-coral px-3 py-2.5 text-sm font-semibold text-white transition hover:bg-coral-dark disabled:opacity-50"
          >
            {ending ? 'Ending…' : 'End call'}
          </button>
        </div>
        {mediaError && (
          <p className="text-xs text-amber-400">
            Camera/mic denied — voice and sentiment need permissions.
          </p>
        )}
      </div>
    </div>
  )
}
