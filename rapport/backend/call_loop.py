"""Per-call orchestration: browser WS <-> gpt-realtime bridge + frame analysis.

All state lives on a CallSession keyed by call_id, so concurrent calls don't
clobber each other. Nothing in here may raise into the WebSocket handler — every
external interaction is wrapped."""
import asyncio
import json
import logging
import statistics
import time

from fastapi import WebSocketDisconnect

from database import SessionLocal
from models import Turn, now_iso
from services import overmind_client
from services.openai_client import DEFAULT_SENTIMENT, analyze_frame
from services.realtime import RealtimeSession

log = logging.getLogger("call_loop")

sessions: dict[str, "CallSession"] = {}
root_spans: dict[str, object] = {}  # call_id -> overmind root span (ended in post_call)

OBJECTION_KEYWORDS = [
    "think about it", "not sure", "maybe", "too expensive", "need some time",
    "get back to you", "speak to my wife", "speak to my husband", "already got quotes",
    "need to think", "talk it over", "bit much", "can't afford",
]
REFRAME_KEYWORDS = [
    "scarcity", "neighbour", "neighbor", "social proof", "limited", "slots",
    "this week", "no-obligation", "no obligation", "filling up", "lock in",
    "locked in", "other customers", "last month", "booked", "waiting list",
]
FLAG_COOLDOWN_S = 15

# end_call tool outcome -> calls.outcome enum
OUTCOME_MAP = {
    "deal_closed": "converted",
    "next_step_agreed": "follow_up",
    "declined": "lost",
    "customer_ended": "lost",
    "other": "lost",
}


def contains_any(text: str, keywords) -> bool:
    t = text.lower()
    return any(k in t for k in keywords)


class CallSession:
    def __init__(self, ws, call, agent):
        self.ws = ws
        self.call_id = call.id
        self.prospect_name = call.prospect_name
        self.agent = agent
        self.start_mono = time.monotonic()

        self.sentiment = dict(DEFAULT_SENTIMENT)
        # local (browser-side) multimodal signals: face expressions + voice tone
        self.sentiment["face_valence"] = None
        self.sentiment["voice_valence"] = None
        self.sentiment_history = []  # [{t, **sentiment}] for post-call
        # measured multimodal affect (face + voice), aligned to each agent line so
        # post-call learning knows which parts of the script actually landed
        self.affect_buffer = []  # [{t, face, voice}] raw browser samples (mono time)
        self.script_feedback = []  # per agent line -> prospect's measured reaction
        self.pending_agent_line = None  # the agent line awaiting a reaction
        self.last_agent_end_mono = None
        self.latest_frame = None
        self.last_analyzed_frame = None
        self.neg_voice_count = 0
        self.agent_end_request = None  # set when the agent calls end_call

        self.turn_number = 0
        self.stage = "intro"
        self.interim = ""
        self.agent_text_buf = ""
        self.agent_responding = False
        self.greeted = False
        self.closing = False
        self.reconnected = False

        self.objection_turn = None  # (turn_id, text) awaiting the agent's reframe
        self.flag_last: dict[str, float] = {}
        self.eye_off_count = 0
        self.low_eng_count = 0
        self.turn_span = None

        self.realtime = RealtimeSession(agent.system_prompt, self._on_realtime_event)

    # ------------------------------------------------------------------ utils

    def elapsed_ts(self) -> str:
        s = int(time.monotonic() - self.start_mono)
        return f"{s // 60:02d}:{s % 60:02d}"

    # ----------------------------------------------- multimodal affect helpers

    def _record_affect(self, face, voice):
        """Buffer one raw browser sample (face XOR voice) against mono time."""
        self.affect_buffer.append({"t": time.monotonic(), "face": face, "voice": voice})
        if len(self.affect_buffer) > 4000:
            del self.affect_buffer[:1000]

    def _affect_window(self, t0, t1):
        """Average measured face/voice valence the prospect showed in [t0, t1]."""
        faces = [s["face"] for s in self.affect_buffer if t0 <= s["t"] <= t1 and s["face"] is not None]
        voices = [s["voice"] for s in self.affect_buffer if t0 <= s["t"] <= t1 and s["voice"] is not None]
        face = round(statistics.fmean(faces), 2) if faces else None
        voice = round(statistics.fmean(voices), 2) if voices else None
        return face, voice

    @staticmethod
    def _fuse_valence(face, voice, engagement):
        """Collapse face/voice valence + vision engagement into one [-1,1] score."""
        parts = [v for v in (face, voice) if v is not None]
        if engagement is not None:
            parts.append(engagement / 10 * 2 - 1)  # 0..10 -> -1..1
        return round(statistics.fmean(parts), 2) if parts else None

    async def emit(self, obj):
        try:
            await self.ws.send_text(json.dumps(obj))
        except Exception:
            pass  # browser gone; the run loop will wind down

    async def flag(self, key, severity, message, turn_id=None):
        now = time.monotonic()
        if now - self.flag_last.get(key, -1e9) < FLAG_COOLDOWN_S:
            return
        self.flag_last[key] = now
        await self.emit(
            {
                "type": "flag",
                "severity": severity,
                "message": message,
                "timestamp": self.elapsed_ts(),
                "turn_id": turn_id,
            }
        )

    def _insert_turn(self, speaker, text, with_sentiment):
        db = SessionLocal()
        try:
            self.turn_number += 1
            turn = Turn(
                call_id=self.call_id,
                turn_number=self.turn_number,
                speaker=speaker,
                text=text,
                sentiment_json=json.dumps(self.sentiment) if with_sentiment else None,
                timestamp=now_iso(),
            )
            db.add(turn)
            db.commit()
            return turn.to_dict()
        finally:
            db.close()

    # ------------------------------------------------------------------- run

    async def run(self):
        """Main loop: receive from the browser until it disconnects."""
        try:
            await self.realtime.connect()
        except Exception as e:
            log.warning("realtime connect failed: %s", e)
            await self.flag(
                "rt_down", "error", "Voice agent unavailable — check API credentials"
            )

        frame_task = asyncio.create_task(self._frame_loop())
        await self.maybe_greet()
        try:
            while True:
                raw = await self.ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                mtype = msg.get("type")
                if mtype == "audio_chunk":
                    # server-side half of the echo gate (browser also gates)
                    if self.realtime.connected and not self.agent_responding:
                        await self.realtime.send_audio(msg.get("data", ""))
                elif mtype == "video_frame":
                    self.latest_frame = msg.get("data")
                elif mtype == "local_sentiment":
                    await self._on_local_sentiment(msg)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.warning("browser ws loop ended: %s", e)
        finally:
            self.closing = True
            frame_task.cancel()
            await self.realtime.close()
            sessions.pop(self.call_id, None)

    async def maybe_greet(self):
        """The agent speaks first — once the call is live and realtime is up.
        Called from both the /start route and connect, whichever lands last."""
        if self.greeted or not self.realtime.connected:
            return
        from models import Call  # local import to avoid cycles

        db = SessionLocal()
        try:
            call = db.get(Call, self.call_id)
            if not call or call.status != "live":
                return
        finally:
            db.close()
        self.greeted = True
        await self.realtime.add_system_note(
            f"The call is starting now. The prospect's name is {self.prospect_name}. "
            "Begin with the intro stage — greet them and speak first."
        )
        await self.realtime.create_response()

    # ------------------------------------------------------- realtime events

    async def _on_realtime_event(self, ev):
        t = ev.get("type", "")

        if t == "input_audio_buffer.speech_started":
            self.interim = ""
            await self.emit({"type": "transcript_chunk", "text": "", "is_final": False})

        elif t in (
            "conversation.item.input_audio_transcription.delta",
            "conversation.item.audio_transcription.delta",
        ):
            self.interim += ev.get("delta", "")
            await self.emit(
                {"type": "transcript_chunk", "text": self.interim, "is_final": False}
            )

        elif t == "conversation.item.input_audio_transcription.completed":
            text = (ev.get("transcript") or "").strip()
            self.interim = ""
            if text:
                await self._commit_prospect_turn(text)

        elif t in ("response.output_audio.delta", "response.audio.delta"):
            if not self.agent_responding:
                self.agent_responding = True
                await self.emit({"type": "agent_speaking", "value": True})
            await self.emit({"type": "agent_audio", "audio_b64": ev.get("delta", "")})

        elif t in (
            "response.output_audio_transcript.delta",
            "response.audio_transcript.delta",
        ):
            self.agent_text_buf += ev.get("delta", "")
            await self.emit({"type": "agent_text", "text": self.agent_text_buf})

        elif t == "response.output_item.done":
            item = ev.get("item") or {}
            if item.get("type") == "function_call" and item.get("name") == "end_call":
                try:
                    args = json.loads(item.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                self.agent_end_request = {
                    "outcome": OUTCOME_MAP.get(args.get("outcome"), "lost"),
                    "summary": args.get("summary", ""),
                }
                log.info("agent requested end_call: %s", self.agent_end_request)

        elif t == "response.done":
            await self._commit_agent_turn()
            # notify the browser only after the closing line has fully streamed
            if self.agent_end_request is not None:
                await self.emit({"type": "agent_ended_call", **self.agent_end_request})
                self.agent_end_request = None

        elif t == "error":
            log.warning("realtime error event: %s", json.dumps(ev)[:400])

        elif t == "_closed" and not self.closing:
            await self._try_reconnect()

    async def _commit_prospect_turn(self, text):
        now = time.monotonic()
        # grade the preceding agent line by the prospect's MEASURED facial/vocal
        # reaction to it (the window between that line ending and this response)
        if self.pending_agent_line is not None and self.last_agent_end_mono is not None:
            face, voice = self._affect_window(self.last_agent_end_mono, now)
            fused = self._fuse_valence(face, voice, self.sentiment.get("engagement"))
            self.script_feedback.append(
                {
                    "t": self.elapsed_ts(),
                    "stage": self.stage,
                    "agent_line": self.pending_agent_line,
                    "prospect_response": text,
                    "face_reaction": face,
                    "voice_reaction": voice,
                    "engagement": self.sentiment.get("engagement"),
                    "emotion": self.sentiment.get("emotion"),
                    "fused_valence": fused,
                }
            )
            self.pending_agent_line = None
            # live coaching: if the line landed badly on face/voice, tell the agent
            if fused is not None and fused <= -0.4 and self.realtime.connected:
                if now - self.flag_last.get("_affect_signal", -1e9) >= FLAG_COOLDOWN_S:
                    self.flag_last["_affect_signal"] = now
                    await self.realtime.add_system_note(
                        "[Live signal] Your last line landed poorly — the prospect's facial "
                        f"and vocal tone turned negative (valence {fused}). Change tack: "
                        "acknowledge their reaction, shorten, and ask an open question."
                    )

        turn = self._insert_turn("prospect", text, with_sentiment=True)
        await self.emit({"type": "turn_complete", "turn": turn})

        if contains_any(text, OBJECTION_KEYWORDS):
            self.stage = "objection"
            self.objection_turn = (turn["id"], text)
        elif self.stage == "intro" and self.turn_number > 2:
            self.stage = "pitch"

        # manual child span around the prospect-turn -> agent-response cycle
        self.turn_span = overmind_client.start_span("agent-turn")
        overmind_client.set_attrs(
            self.turn_span,
            {
                "call.id": self.call_id,
                "turn.number": self.turn_number,
                "call.stage": self.stage,
                "sentiment.engagement": self.sentiment["engagement"],
                "sentiment.emotion": self.sentiment["emotion"],
                "sentiment.objection_risk": self.sentiment["objection_risk"],
            },
        )

    async def _commit_agent_turn(self):
        text = self.agent_text_buf.strip()
        self.agent_text_buf = ""
        was_responding = self.agent_responding
        self.agent_responding = False
        if was_responding:
            await self.emit({"type": "agent_speaking", "value": False})
        if self.turn_span is not None:
            overmind_client.end_span(self.turn_span)
            self.turn_span = None
        if not text:
            return

        turn = self._insert_turn("agent", text, with_sentiment=False)
        await self.emit({"type": "turn_complete", "turn": turn})

        # this line is now "on the air" — the prospect's next reaction grades it
        self.pending_agent_line = text
        self.last_agent_end_mono = time.monotonic()

        if self.objection_turn is not None:
            obj_turn_id, _ = self.objection_turn
            if contains_any(text, REFRAME_KEYWORDS):
                self.stage = "close"
                await self.flag(
                    "reframe_ok",
                    "ok",
                    "Objection detected — agent reframed with scarcity/social proof",
                    turn_id=obj_turn_id,
                )
            else:
                await self.flag(
                    "no_reframe",
                    "warn",
                    "Objection raised — agent gave no reframe",
                    turn_id=obj_turn_id,
                )
            self.objection_turn = None

    async def _try_reconnect(self):
        if self.reconnected or self.closing:
            return
        self.reconnected = True
        log.info("realtime dropped — attempting one reconnect")
        try:
            self.realtime = RealtimeSession(self.agent.system_prompt, self._on_realtime_event)
            await self.realtime.connect()
            db = SessionLocal()
            try:
                turns = (
                    db.query(Turn)
                    .filter(Turn.call_id == self.call_id)
                    .order_by(Turn.turn_number.desc())
                    .limit(10)
                    .all()
                )
            finally:
                db.close()
            summary = "\n".join(
                f"{'Agent' if t.speaker == 'agent' else self.prospect_name}: {t.text}"
                for t in reversed(turns)
            )
            await self.realtime.add_system_note(
                "The audio link blipped and has reconnected mid-call. Conversation so far:\n"
                f"{summary}\nContinue naturally — do not restart the call."
            )
            await self.flag("rt_blip", "warn", "Voice link blipped — reconnected")
        except Exception as e:
            log.warning("realtime reconnect failed: %s", e)
            await self.flag("rt_down", "error", "Voice agent unavailable — connection lost")

    # ----------------------------------------- local (browser) sentiment

    async def _on_local_sentiment(self, msg):
        """Browser-side multimodal signals: face expressions (~2.5 Hz) and
        voice-tone emotion (~3 s windows), each a valence in [-1, +1]."""
        kind = msg.get("kind")
        try:
            valence = max(-1.0, min(1.0, float(msg.get("valence"))))
        except (TypeError, ValueError):
            return
        if kind == "face":
            self.sentiment["face_valence"] = round(valence, 2)
            self._record_affect(round(valence, 2), None)
        elif kind == "voice":
            self.sentiment["voice_valence"] = round(valence, 2)
            self._record_affect(None, round(valence, 2))
            self.neg_voice_count = self.neg_voice_count + 1 if valence <= -0.4 else 0
            if self.neg_voice_count >= 2:
                await self.flag(
                    "voice_tone", "warn", "Negative vocal tone — frustration in voice"
                )
        else:
            return
        await self.emit({"type": "sentiment_update", "source": "local", **self.sentiment})

    # ---------------------------------------------------------------- frames

    async def _frame_loop(self):
        while True:
            await asyncio.sleep(3)
            frame = self.latest_frame
            if not frame or frame is self.last_analyzed_frame:
                continue
            self.last_analyzed_frame = frame
            result = await analyze_frame(frame)
            if result is None:
                continue  # hold previous state — never crash the panel
            self.sentiment.update(result)
            self.sentiment_history.append(
                {"t": self.elapsed_ts(), **self.sentiment}
            )
            await self.emit({"type": "sentiment_update", "source": "vision", **self.sentiment})
            await self._check_thresholds(result)

    async def _check_thresholds(self, s):
        self.eye_off_count = 0 if s["eye_contact"] else self.eye_off_count + 1
        self.low_eng_count = self.low_eng_count + 1 if s["engagement"] <= 3 else 0

        if self.eye_off_count >= 2:
            await self.flag(
                "eye", "warn", "Prospect broke eye contact — hesitation detected"
            )
        if s["posture"] == "closed":
            await self.flag("posture", "warn", "Crossed arms posture — disengagement signal")
        if s["objection_risk"] >= 0.75:
            await self.flag(
                "obj_risk", "warn", "High objection risk detected — consider reframing"
            )
        if self.low_eng_count >= 2:
            await self.flag("low_eng", "warn", "Engagement dropping — shorten next response")

        # live body-language signal into the conversation (rate-limited like flags)
        if (s["objection_risk"] >= 0.75 or s["posture"] == "closed") and self.realtime.connected:
            now = time.monotonic()
            if now - self.flag_last.get("_live_signal", -1e9) >= FLAG_COOLDOWN_S:
                self.flag_last["_live_signal"] = now
                await self.realtime.add_system_note(
                    f"[Live signal] The prospect shows {s['posture']} posture, emotion "
                    f"{s['emotion']}, objection risk {int(s['objection_risk'] * 100)}%. "
                    "Address the hesitation proactively and keep your next response short."
                )
