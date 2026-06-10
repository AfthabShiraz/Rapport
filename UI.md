# Rapport — Overmind Sales Agent — UI Specification

## Overview

Four screens, React + Tailwind. The product is an AI sales agent that takes voice calls (OpenAI `gpt-realtime`), reads prospect sentiment from video in real time, and uses Overmind to self-optimize after every call.

**Realtime:** one native browser `WebSocket` per call. **Media:** local `getUserMedia` (mic + camera, one permission prompt). **Every element is live-driven** — there is no scripted layer. The only non-live data is the seeded analytics fallback (`seeded: true` when < 5 real calls), labelled in the UI.

---

## Screen 1 — Manager Setup (`/setup`)

**Purpose:** onboard a brand, configure the agent, start a call.

**Layout:** two-column. Left: 200px sidebar. Right: scrollable form.

**Sidebar nav (only screens that exist):** Setup (active) · New call (→ `/setup`, scrolls to + focuses the prospect-name input) · Analytics.

**Form sections:**

- **Brand & product** — `Brand name` ("Lumio Solar"), `Product`, `Elevator pitch` (textarea) → `agents.*`
- **Ideal customer profile** — `Target persona` → `agents.target_persona`
- **Common objections** — tag/pill list with ✕ remove + "+ Add" pill → `agents.objections`. Example tags: "Too expensive", "Not enough sun in the UK", "Need to think about it", "Already got quotes"
- **Learning status** — live line: `{N} calls traced · {M} optimizations applied` + green "active" badge. Source: `GET /calls?agentId=` + `GET /optimizations?agentId=` (accepted count). Refresh icon re-fetches.

**Footer actions:**

- `Save & deploy agent` → `POST /agents` (or `PATCH /agents/:id` on edit); store `id` in `localStorage`.
- **Start call control:** `Prospect name` input + `Start call` → `POST /calls` → navigate `/call/:id`.

> For the demo, the form mounts pre-filled with the Lumio Solar example when no agent exists yet.

---

## Screen 2 — Live Call View (`/call/:id`)

**Purpose:** the main demo screen. 100% WebSocket-driven.

**Layout:** two-column, full viewport height. Left ~65%, right ~35% (280px min).

On mount: `GET /calls/:id` (→ `prospect_name` for the title, `optimized` for the badge), `getUserMedia` (mic + cam), open `WS /ws/calls/:id`, fire `POST /calls/:id/start` once media + WS are ready. Start the pcm-worklet mic stream and the 3 s frame grab.

### Left pane — call area

**Top bar:**
- Red pulsing dot (recording)
- "Call with {prospect_name}"
- Right: **optimization badge** + timer
  - Badge reads `calls.optimized`: "Before optimization" (amber) when 0, "After optimization" (green) when 1. Real derived state — no toggle.
  - Timer from `useCallTimer` (since `call_started`).

**Video area (dark #1a1a1a):**
- Prospect's own camera feed (local `<video>`); initials placeholder if denied. Name label: "James Thornton · Prospect".
- Top-left: `SentimentBadge` — dot + label from `sentiment_update.emotion` (amber = skeptical/frustrated, green = interested/positive).
- Bottom: `AgentSpeechBubble` (semi-transparent dark) — "Agent (Aria) is speaking" while `agent_speaking`, text streams in via `agent_text` deltas.
- Small "muted (agent speaking)" hint while the mic gate is active.

**Transcript panel (bottom, ~160px, scrollable) — `TranscriptFeed`:**
- Interim prospect speech shown in a typing state (`transcript_chunk`), replaced on `turn_complete`.
- Rows: agent = green tint, prospect = blue tint. A row gets an inline red ⚠ "objection" marker when a `flag` event arrives with that row's `turn_id`.

### Right pane — live sentiment

**Header:** "Live sentiment" + eye icon.

**Metrics — `MetricBar`** (all from `sentiment_update`, fixed mapping):

| Metric | Field | Bar % | Color rule |
|---|---|---|---|
| Engagement | `engagement` (0–10) | `engagement*10` | <40 red, <60 amber, else green |
| Trust signal | `trust_signal` (0–1) | `*100` | same |
| Objection risk | `objection_risk` (0–1) | `*100` | inverted: >70 red, >40 amber, else green |
| Voice tone | `voice_valence` (−1..1, browser-side model) | `(v+1)/2*100` | shown once first sample arrives |
| Facial read | `face_valence` (−1..1, browser-side model) | `(v+1)/2*100` | shown once first sample arrives |

**Sparkline — `EngagementSparkline`:** "Engagement over call", SVG polyline (~52px), one point per `sentiment_update`, end dot colored by trend.

**Flags feed — `FlagsFeed`:** appends per `flag` event (`message` + `timestamp`). ⚠ amber/red for warnings, ✓ green for `severity: ok` (e.g. successful reframe).

**Call controls (bottom):**
- `Mute` — manual mic toggle (separate from the automatic agent-speaking gate)
- `End call` — danger styling → `POST /calls/:id/end` → navigate `/report/:callId`

**Agent-initiated hang-up:** on `agent_ended_call` (the agent called its `end_call` tool after a closing line), the page waits ~1.5 s for the audio to drain, ends the call with the agent's structured outcome, and navigates to the Report automatically.

---

## Screen 3 — Overmind Post-Call Report (`/report/:callId`)

**Purpose:** the centrepiece — diagnosis + one-click optimization.

**Load sequence:**
1. `GET /calls/:callId` → score cards (`call.scores_json`) + transcript (`turns`)
2. Poll `GET /calls/:callId/optimization` (1.5 s interval) — "Overmind is analysing this call…" until `200`
3. `GET /optimizations?agentId=` → iteration dots

**Layout:** single column, full-width card, no sidebar.

**Top bar:** sparkles icon + "Overmind optimization report" · right: "{prospect_name} · {duration}".

### Scores row (4 cards) — `ScoreCards`, source `call.scores_json`:

| Card | Field | Example | Sub-label |
|---|---|---|---|
| Objection handled | `objection_handled` | ✗ No (red) | "dropped from call" |
| Avg engagement | `avg_engagement` | 2.8/10 (red) | from `engagement_vs_recent`: "↓ vs last 5 calls" |
| Sentiment delta | `sentiment_delta` | −4.1 (amber) | "mood worsened" / "mood improved" |
| Outcome | `outcome` | Lost (red) | "no follow-up booked" etc. |

### Failure diagnosis

Red warning triangle + "Failure diagnosis". Renders `optimization.diagnosis_json` (1–3 items), each `{title, detail, timestamp}` — model-generated from the real transcript, sentiment timeline, and prior-call history.

### Recommended prompt change

Wand icon + "Recommended prompt change". `PromptDiff` two-column: left red "Current behaviour" = `before_behavior`; right green "Optimized behaviour" = `after_behavior`. (Accept applies the full `after_prompt` under the hood.)

**Action row:**
- ✓ "Accept & apply to next call" (green) → `POST /optimizations/:id/accept`
- "Reject" → `POST /optimizations/:id/reject`
- Info badge: "Overmind will track if this improves conversion"

**Accepted state (replaces action row):**
- Green check + "Optimization applied — next call will use the updated prompt."
- **"Start next call" button** — pre-filled with the same prospect name → `POST /calls` → `/call/:id`. (This is the demo's Call-2 entry point.)

### Iteration history (footer)

Dots from `GET /optimizations?agentId=`: green = accepted, blue = current, gray = pending. Label: "{accepted} of {total} optimizations applied" + average lift from non-null `engagement_lift` values (omit if none).

---

## Screen 4 — Analytics Dashboard (`/analytics`)

**Purpose:** closing slide — compound improvement.

**Load:** `GET /analytics/:agentId` + `GET /optimizations?agentId=`. If `seeded: true`, show a small "demo data" hint.

**Layout:** single column, no sidebar.

**Header:** chart icon + "{brand_name} — agent performance" · right: "{total_calls} calls · {optimizations_applied} optimizations applied".

### Stats row (4 cards):

| Stat | Field |
|---|---|
| Conversion rate | `conversion_rate` |
| Avg engagement | `avg_engagement` |
| Avg call duration | `avg_duration_seconds` |
| Objections handled | `objection_handle_rate` |

Deltas ("↑ +19pp vs call 1") computed client-side from first vs latest `per_call_*` values.

### Charts row (two columns):

- **"Conversion rate over calls"** (`ConversionChart`) — rolling conversion % from `per_call_converted`; ▲ markers at `optimization_call_indices` (server-computed). Green #1D9E75, light fill.
- **"Avg engagement score"** (`EngagementChart`) — `per_call_engagement`, 0–10. Blue #185FA5, light fill.

Both: no legend, minimal gridlines, ≤10 x-ticks.

### Optimizations applied (list) — `GET /optimizations?agentId=`:

Numbered blue circle + `after_behavior` (short) + lift badge (`engagement_lift` green, or gray "tracking…" when null).

---

## State Management Notes

- **Active agent:** `localStorage` after `POST /agents`; resolved via `GET /agents` if absent.
- **Single WebSocket per call** (`useCallSocket`): connect on Screen 2 mount, dispatch events, expose `send()`. Close on unmount → backend tears down the Realtime session + frame task.
- **Echo control:** mic chunks dropped while `agent_speaking` is true or agent audio is still playing locally; resumed when playback ends. Headphones for live demos.
- **Agent audio:** `useAgentAudio` queues PCM chunks into Web Audio back-to-back for gapless streaming playback.
- **Accept (Screen 3):** one-shot; replaces action row with confirmation + "Start next call". No undo.
- **Analytics fallback:** `seeded: true` → charts use the seeded dataset + "demo data" hint.

---

## Component Hierarchy

```
App
├── Sidebar (Setup · New call · Analytics; hidden on Screens 3 & 4)
├── SetupPage
│   ├── BrandForm
│   ├── ObjectionTagList
│   ├── LearningStatus        (live counts)
│   └── StartCallControl      (prospect name → POST /calls)
├── LiveCallPage
│   ├── CallHeader            (rec dot, title, optimization badge, timer)
│   ├── VideoArea             (local camera, SentimentBadge, AgentSpeechBubble)
│   ├── TranscriptFeed
│   └── SentimentPanel
│       ├── MetricBar ×3      (Engagement, Trust signal, Objection risk)
│       ├── EngagementSparkline
│       └── FlagsFeed
├── ReportPage
│   ├── ScoreCards ×4         (call.scores_json)
│   ├── DiagnosisSection      (diagnosis_json)
│   ├── PromptDiff            (before/after behavior)
│   ├── AcceptRejectRow       (→ accepted state + "Start next call")
│   └── IterationHistory
└── AnalyticsPage
    ├── StatCards ×4
    ├── ConversionChart
    ├── EngagementChart
    └── OptimizationsList
```

---

## Key Demo Interactions

1. **Start call** — Setup → prospect name → `POST /calls` → live screen, real pipeline.
2. **End call** — auto-navigates to the Report; "analysing…" resolves when the background diagnosis lands.
3. **Accept** — the live moment judges watch; really applies `after_prompt` to the agent.
4. **Start next call** — from the accepted state; Call 2 runs the optimized prompt and the badge reads "After optimization".
5. **Analytics** — closing slide; real data when ≥5 calls, seeded fallback otherwise.
