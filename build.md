# Rapport — Overmind Sales Agent — Build Spec

## For Claude Code

Local-only hackathon build. Every UI element maps to a backend endpoint, data model, or real-time event. **There is no scripted/fake layer in the product.** The stage fallback is a pre-recorded screen capture of a successful run — it lives outside this codebase. The only seeded data is the analytics fallback when fewer than 5 real calls exist (clearly labelled).

---

## Stack

**Backend:** Python + FastAPI — async-native, single `main.py` entry, `uvicorn main:app --reload`.

**Frontend:** React + Vite (plain JS) + Tailwind. One native browser `WebSocket` per call. No Socket.IO.

**Voice agent:** **OpenAI Realtime API (`gpt-realtime`)** — one speech-to-speech WebSocket per call, opened from the backend. Handles STT, the agent's reasoning, and TTS in a single model. Server-side VAD does turn-taking. No Deepgram, no ElevenLabs.

**Media capture:** browser-native `getUserMedia` (mic + camera, one prompt). The agent has no WebRTC presence — its audio streams back over the app WebSocket.
- Audio: `AudioWorklet` resamples mic to **24 kHz mono PCM16** (gpt-realtime's native input format), sent every ~100 ms.
- Video: `<canvas>` grabs a JPEG frame from the local video element every 3 s (scaled to ≤512 px wide).

**Vision sentiment:** GPT-4o chat completions with `response_format=json_object` — one call per frame, fully off the conversation path.

**Database:** SQLite via SQLAlchemy (`overmind.db`).

**Overmind:** Python SDK (`pip install overmind`). `overmind.init()` at startup auto-wraps chat-completions calls (vision, diagnosis, outcome classifier). Realtime turns get manual child spans. Post-call `evaluate()` scores against the call's stored `trace_id`.

---

## Environment Variables (`.env`)

```
OPENAI_API_KEY=
OVERMIND_API_KEY=
```

---

## Data Models (SQLite via SQLAlchemy)

### `agents`
```
id              TEXT  PRIMARY KEY  (uuid)
brand_name      TEXT  NOT NULL
product         TEXT  NOT NULL
elevator_pitch  TEXT  NOT NULL
target_persona  TEXT  NOT NULL
objections      TEXT  NOT NULL     (JSON array of strings)
system_prompt   TEXT  NOT NULL     (active prompt — updated by Overmind accept)
created_at      TEXT  NOT NULL
```

### `calls`
```
id                TEXT  PRIMARY KEY  (uuid)
agent_id          TEXT  NOT NULL     FK → agents.id
prospect_name     TEXT  NOT NULL
status            TEXT  NOT NULL     ENUM: pending | live | ended
optimized         INTEGER NOT NULL   0/1 — agent had ≥1 accepted optimization at call start (drives the badge)
started_at        TEXT
ended_at          TEXT
outcome           TEXT               ENUM: converted | follow_up | lost | null
scores_json       TEXT               JSON — the 4 post-call scorecard values (see Post-Call)
overmind_trace_id TEXT
```

### `turns`
```
id              TEXT  PRIMARY KEY  (uuid)
call_id         TEXT  NOT NULL     FK → calls.id
turn_number     INTEGER NOT NULL
speaker         TEXT  NOT NULL     ENUM: agent | prospect
text            TEXT  NOT NULL
sentiment_json  TEXT               JSON (only on prospect turns)
timestamp       TEXT  NOT NULL
```

### `optimizations`
```
id              TEXT  PRIMARY KEY  (uuid)
agent_id        TEXT  NOT NULL     FK → agents.id
call_id         TEXT  NOT NULL     FK → calls.id (the call that triggered it)
status          TEXT  NOT NULL     ENUM: pending | accepted | rejected
diagnosis_json  TEXT  NOT NULL     JSON array of { title, detail, timestamp } (1–3 items)
before_behavior TEXT  NOT NULL     snippet shown on the diff (current behaviour)
after_behavior  TEXT  NOT NULL     snippet shown on the diff (optimized behaviour)
before_prompt   TEXT  NOT NULL     full system_prompt at time of optimization
after_prompt    TEXT  NOT NULL     full system_prompt with the override block appended
engagement_lift TEXT               e.g. "+1.8 engagement" — set once ≥1 later call completes; else null
created_at      TEXT  NOT NULL
applied_at      TEXT
```

---

## Backend API

Base URL: `http://localhost:8000`. Internal writes are direct DB calls — no endpoint calls another endpoint over HTTP.

### Agents

- **`POST /agents`** — body `{brand_name, product, elevator_pitch, target_persona, objections[]}`. Generates `system_prompt` from the template (below), inserts, returns `201` + agent. *UI: "Save & deploy agent"; frontend stores `id` in `localStorage`.*
- **`GET /agents`** — all agents, newest first (active-agent resolution when localStorage is empty).
- **`GET /agents/:agentId`** — single agent (Setup prefill).
- **`PATCH /agents/:agentId`** — partial update. If form fields change, regenerate `system_prompt` (preserving any appended OVERMIND OVERRIDE blocks). Also used internally on optimization accept.

### Calls

- **`POST /calls`** — body `{agent_id, prospect_name}`. Inserts with `status=pending`, sets `optimized` from whether the agent has an accepted optimization. Returns `201` + call. *UI: Start call → navigate `/call/:id`.*
- **`POST /calls/:callId/start`** — sets `status=live`, `started_at`; opens the Overmind root span and stores `trace_id`; emits `call_started` on the WS. *Fired once mic granted + WS open.*
- **`POST /calls/:callId/end`** — optional body `{outcome}`. Sets `status=ended`, `ended_at`. If no outcome given, a GPT-4o classifier over the transcript decides (`converted|follow_up|lost`; keyword heuristic if the API call fails). Kicks off `_run_post_call_evaluation` as a background task. Returns the call.
- **`GET /calls/:callId`** — `{call, turns[]}` (Report score cards + transcript).
- **`GET /calls?agentId=`** — all calls for an agent, newest first.
- **`GET /calls/:callId/optimization`** — the optimization generated by this call, or `204` while the background task runs. *Report polls this.*

### Optimizations

- **`GET /optimizations?agentId=`** — newest first (Analytics list, Report iteration dots).
- **`GET /optimizations/:id`** — single (Report diagnosis + diff).
- **`POST /optimizations/:id/accept`** — sets `status=accepted`, `applied_at`; sets agent `system_prompt = after_prompt`. Returns updated row.
- **`POST /optimizations/:id/reject`** — sets `status=rejected`.

### Analytics

- **`GET /analytics/:agentId`** — computed from real rows:

```json
{
  "total_calls": 14,
  "conversion_rate": 0.34,
  "avg_engagement": 6.8,
  "avg_duration_seconds": 252,
  "objection_handle_rate": 0.78,
  "per_call_engagement": [3.1, 2.8, 4.2],
  "per_call_converted": [false, false, true],
  "optimization_call_indices": [1],
  "optimizations_applied": 3,
  "seeded": false
}
```

`optimization_call_indices` = positions in the `per_call_*` arrays of calls that triggered an *accepted* optimization — computed server-side so the chart can place ▲ markers without joining `call_id`s client-side.

**Seeded fallback:** if `total_calls < 5`, return the seeded 50-call dataset (`seed.py`) with `seeded: true`. The frontend shows a small "demo data" hint. This is the only non-live data in the product.

---

## WebSocket — Single Real-Time Channel

**`WS /ws/calls/:callId`** — one connection carries everything for the call's duration.

### Browser → backend

- **`audio_chunk`** `{type, data: "<base64 PCM16 24k mono>"}` — every ~100 ms. Forwarded to the Realtime session as `input_audio_buffer.append`. **Gated:** not sent while agent audio is playing (echo control).
- **`video_frame`** `{type, data: "<base64 JPEG>", timestamp}` — every 3 s.
- **`local_sentiment`** `{type, kind: "face"|"voice", valence: -1..1, scores}` — browser-side multimodal signals: face-api.js facial expressions (~2.5 Hz) and Transformers.js voice-tone emotion (~3 s windows), both running locally in the browser. Voice capture is gated on the echo control so the agent's own voice never colours the reading. Merged into the server's sentiment state as `face_valence` / `voice_valence`.

### Backend → browser

- **`call_started`** `{type, started_at}`
- **`transcript_chunk`** `{type, text, is_final: false}` — interim prospect speech (from Realtime transcription deltas), shown in a typing state.
- **`turn_complete`** `{type, turn: {id, speaker, text, sentiment_json}}` — committed turn, either speaker.
- **`agent_speaking`** `{type, value: true|false}` — `true` with the first audio chunk of a response, `false` when the response's audio has fully streamed. Frontend mutes the mic while `true` **or** while audio is still playing locally.
- **`agent_text`** `{type, text}` — incremental agent transcript (from output-audio transcript deltas); the speech bubble updates as it speaks.
- **`agent_audio`** `{type, audio_b64: "<base64 PCM16 24k>"}` — streamed chunks, played via Web Audio as they arrive (queued back-to-back, no gaps).
- **`sentiment_update`** `{type, engagement: 0-10, trust_signal: 0-1, emotion, eye_contact, posture, objection_risk: 0-1, face_valence: -1..1|null, voice_valence: -1..1|null}` — emitted per analyzed vision frame (~3 s) and per local-sentiment sample. **Fixed scale mapping:** Engagement bar = `engagement*10`%, Trust = `trust_signal*100`%, Objection risk = `objection_risk*100`%, valences = `(v+1)/2*100`%.
- **`agent_ended_call`** `{type, outcome: "converted"|"follow_up"|"lost", summary}` — the agent hung up via its `end_call` function tool (registered on the Realtime session; outcomes `deal_closed→converted`, `next_step_agreed→follow_up`, else `lost`). Sent after the closing line finishes streaming; the frontend then calls `POST /calls/:id/end` with that outcome.
- **`flag`** `{type, severity, message, timestamp: "02:08", turn_id: "<uuid>|null"}` — `turn_id` set when the flag refers to a specific turn (e.g. the objection flags), so the transcript can mark that row.

> No `optimization_ready` WS event: the call WS is always closed by the time the post-call task finishes (End call navigates to the Report). The Report polls `GET /calls/:callId/optimization`.

---

## Backend Call Loop (`call_loop.py`)

One async session per call, all state keyed by `call_id`. The backend is a bridge: browser WS ↔ OpenAI Realtime WS, plus a frame-analysis task.

```
1. On browser WS connect: open wss://api.openai.com/v1/realtime?model=gpt-realtime
   session.update:
     - instructions = agent.system_prompt + per-call context (prospect name, date)
     - voice: "marin"; input/output format: pcm16 @ 24 kHz
     - turn_detection: server_vad (silence_duration_ms ~600 — tune at rehearsal)
     - input transcription enabled (gpt-4o-mini-transcribe)
   Then send response.create → the agent speaks the greeting first.

2. Browser→OpenAI: audio_chunk → input_audio_buffer.append (dropped while agent_responding).

3. OpenAI→browser relay loop:
   - input transcription delta            → transcript_chunk (is_final:false)
   - input transcription completed        → insert prospect turn (with latest sentiment_state)
                                            + turn_complete + objection flag check
                                            + open/close manual Overmind child span "agent-turn"
   - response output-audio transcript delta → agent_text
   - response audio delta                 → agent_audio (+ agent_speaking:true on first chunk)
   - response done                        → insert agent turn + turn_complete + agent_speaking:false
                                            + "no reframe" flag check

4. Frame task (every 3 s, latest frame only):
   - GPT-4o vision → sentiment JSON; on malformed response keep previous state (never crash)
   - update sentiment_state[call_id] → emit sentiment_update → threshold flags
   - if objection_risk ≥ 0.75 or posture = closed (rate-limited to once / 15 s):
     inject a one-line system item into the Realtime conversation, e.g.
     "[Live signal] Prospect shows closed posture and high objection risk — address hesitation
      proactively, keep it short." This is how the optimized agent reacts to body language.

5. If the Realtime WS drops mid-call: reconnect once with the same instructions +
   a transcript summary item; emit a flag. If reconnect fails, emit flag "voice agent
   unavailable" — the call can still be ended cleanly.

6. On browser WS close: close Realtime WS, cancel frame task, free state.
```

**Echo control:** no barge-in — the mic gate (browser drops chunks while agent audio plays) plus headphones makes self-transcription impossible. One less failure mode on stage.

**Stage tracking** (`intro | pitch | objection | close`) is a simple heuristic: intro for the first agent turn, pitch after, objection once an objection keyword appears in a prospect turn, close after the agent's reframe. Stored per call; set as a span attribute and available to context.

---

## Overmind Integration

```python
# startup
import overmind
overmind.init(service_name="sales-agent")
```

```python
# POST /calls/:id/start — root span, keep handle open in per-call state
tracer = overmind.get_tracer()
call_root = tracer.start_span("sales-call")
trace_id = format(call_root.get_span_context().trace_id, "032x")  # → calls.overmind_trace_id
overmind.set_user(user_id=agent_id, email=f"{agent_id}@demo.local")
```

```python
# per prospect-turn→agent-response cycle (manual span; Realtime isn't auto-wrapped)
with tracer.start_as_current_span("agent-turn") as span:
    span.set_attribute("call.id", call_id)
    span.set_attribute("turn.number", n)
    span.set_attribute("call.stage", stage)
    span.set_attribute("sentiment.engagement", s["engagement"])
    span.set_attribute("sentiment.emotion", s["emotion"])
    span.set_attribute("sentiment.objection_risk", s["objection_risk"])
```

```python
# post-call
overmind.evaluate(trace_id=call.overmind_trace_id, scores={
    "objection_handled": 1 or 0,
    "prospect_engagement_end": final_engagement,
    "call_outcome": 1 if outcome == "converted" else 0,
    "sentiment_delta": end_engagement - start_engagement,
})
```

All Overmind calls are wrapped in try/except — tracing degrades to no-op, never breaks a call.

---

## Post-Call Evaluation (`post_call.py`)

Background task from `POST /calls/:callId/end`:

```
1. Load turns. Compute scores → persist to calls.scores_json:
     { "objection_handled": false, "avg_engagement": 2.8, "sentiment_delta": -4.1,
       "outcome": "lost", "engagement_vs_recent": -1.4 }   # vs mean of last 5 ended calls
2. overmind.evaluate(...); end the call root span.
3. Diagnosis — GPT-4o, response_format=json_object, parse defensively (retry once):
     System: "You are a sales call analyst. Given the transcript, sentiment timeline,
              scores, and the agent's prior call history, identify the 1–3 most impactful
              failures. Cite timestamps. Reference cross-call patterns when the history
              supports them. Return JSON only:
              { diagnosis: [{title, detail, timestamp}],
                before_behavior: string, after_behavior: string }"
     User:   [transcript + sentiment timeline + scores
              + summary of this agent's previous ended calls: outcome + objection keywords hit]
4. Insert optimization (status=pending, before_prompt=current, after_prompt=inject_override(...)).
5. Update engagement_lift on previously-accepted optimizations that now have ≥1 later call:
     lift = avg_engagement(calls after applied_at) − avg_engagement(calls before).
6. Done — the Report discovers the row via its `GET /calls/:callId/optimization` poll.
```

`inject_override(prompt, after_behavior)` appends:
```
=== OVERMIND OVERRIDE (applied {date}) ===
{after_behavior}
```

**Fallback:** if the diagnosis LLM call fails twice, build a deterministic diagnosis from real data (the actual objection turn + timestamps + flag log) with a stock scarcity/social-proof `after_behavior`. The Report never hangs.

---

## System Prompt Template (`POST /agents`)

```
You are Aria, a professional sales agent for {brand_name}.

Product: {product}
Pitch: {elevator_pitch}
Target customer: {target_persona}

OBJECTION HANDLING:
When you hear any of these objections: {objections}
— Acknowledge the concern genuinely
— Provide a specific reframe with evidence or social proof
— Offer a low-friction next step
— Never release the prospect without anchoring a next action

BEHAVIOURAL RULES:
- Keep responses under 3 sentences unless explaining a benefit
- Always use the prospect's first name
- If you detect hesitation or low engagement, shorten your next response and ask an open question
- React to [Live signal] system notes — they describe the prospect's body language right now
- Never use filler phrases like "Great question!" or "Absolutely!"

CALL STAGES:
1. intro — greet, confirm you have 5 minutes, set the agenda
2. pitch — deliver the elevator pitch, stop and ask for reaction
3. objection — handle concerns using the framework above
4. close — ask for a specific next step (survey booking, follow-up call)
```

---

## Vision Sentiment Prompt (GPT-4o, per frame)

```
Analyze this video frame of a person on a sales call.
Return ONLY valid JSON:
{ "engagement": <int 0-10>, "trust_signal": <float 0-1>,
  "emotion": <"neutral"|"interested"|"skeptical"|"frustrated"|"positive">,
  "eye_contact": <bool>, "posture": <"open"|"closed"|"leaning_in"|"leaning_back">,
  "objection_risk": <float 0-1> }
```

---

## Flag Thresholds

| Condition | Flag |
|---|---|
| `eye_contact=false` ×2 consecutive frames | "Prospect broke eye contact — hesitation detected" |
| `posture=closed` | "Crossed arms posture — disengagement signal" |
| `objection_risk ≥ 0.75` | "High objection risk detected — consider reframing" |
| `engagement ≤ 3` ×2 consecutive frames | "Engagement dropping — shorten next response" |
| Prospect turn has "think about it"/"not sure"/"maybe" and next agent turn lacks a reframe keyword | "Objection raised — agent gave no reframe" |
| Same, but agent turn **has** a reframe keyword | ✓ "Objection detected — agent reframed with scarcity/social proof" (severity: ok) |
| `voice_valence ≤ -0.4` ×2 consecutive samples | "Negative vocal tone — frustration in voice" |

Each flag type rate-limited to once per 15 s. Reframe keywords: `scarcity, neighbours, social proof, limited, slots, this week, no-obligation` + brand terms.

---

## File Layout

```
/
├── backend/
│   ├── main.py              — FastAPI app, routes, WS handler
│   ├── models.py            — SQLAlchemy models
│   ├── database.py          — engine + session factory
│   ├── services/
│   │   ├── realtime.py      — OpenAI Realtime WS bridge (session config, event relay, reconnect)
│   │   ├── openai_client.py — GPT-4o chat + vision (json_object, defensive parsing)
│   │   └── overmind_client.py — init, span helpers, evaluate (all try/except no-op safe)
│   ├── call_loop.py         — per-call orchestration (state keyed by call_id)
│   ├── post_call.py         — scoring, diagnosis, optimization gen, lift update
│   ├── seed.py              — seeded analytics dataset (<5 real calls)
│   ├── optimizer/           — Overmind *optimizer* harness (offline loop)
│   │   ├── sim_agent.py     — run() entrypoint: simulated call vs role-played prospect
│   │   ├── build_dataset.py — scenarios rebuilt from real calls in overmind.db
│   │   ├── eval_spec.json / policies.md
│   │   └── apply_prompt.py  — ships a winning prompt back via a normal optimization row
│   └── requirements.txt
├── frontend/
│   ├── public/pcm-worklet.js — AudioWorklet: mic Float32 → 24 kHz PCM16
│   ├── src/
│   │   ├── api/             — agents.js, calls.js, optimizations.js, analytics.js
│   │   ├── hooks/           — useCallSocket, useMedia (one getUserMedia for mic+cam),
│   │   │                      useMicCapture, useCamera, useCallTimer, useAgentAudio (chunk queue player)
│   │   ├── state/activeAgent.js
│   │   ├── pages/           — SetupPage, LiveCallPage, ReportPage, AnalyticsPage
│   │   ├── components/      — Sidebar, StartCallControl, MetricBar, EngagementSparkline,
│   │   │                      FlagsFeed, TranscriptFeed, AgentSpeechBubble, SentimentBadge,
│   │   │                      PromptDiff, ScoreCards, StatCards, ConversionChart, EngagementChart
│   │   │                      (page-local subcomponents per the UI.md hierarchy live inside their pages)
│   │   ├── App.jsx, main.jsx, index.css
│   ├── index.html, vite.config.js, package.json
├── .env
└── README.md
```

> Note: Realtime API event names differ between the beta and GA wire formats — verify against current OpenAI docs when writing `services/realtime.py`.

---

## Dependencies

**Backend:** `fastapi`, `uvicorn[standard]`, `sqlalchemy`, `python-dotenv`, `openai`, `overmind`, `websockets`

**Frontend:** `react`, `react-dom`, `react-router-dom`, `chart.js`, `react-chartjs-2`, `tailwindcss`

---

## Run Instructions

```bash
# Backend
cd backend && pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Frontend
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Vite proxies `/agents`, `/calls`, `/optimizations`, `/analytics`, `/ws` → `localhost:8000`. **Wear headphones for live calls** (with the mic gate this guarantees no self-transcription).

---

## Demo Fallback (outside the codebase)

Record one clean end-to-end run (Call 1 → Report → Accept → Call 2 → Analytics) with QuickTime, mic on, agent audio through speakers. Keep it paused behind the browser on stage; if live stalls > 5 s, Cmd+Tab and narrate over it. No fallback code exists in the product.
