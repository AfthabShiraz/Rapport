# Rapport — project documentation

> A voice sales agent that calls a prospect, reads their emotion in real time
> across face / voice / text, and (next) uses that signal to rewrite itself into
> a better agent.

This document covers **what we're building**, **what's built so far**, and **what's
next**. For setup/run instructions see [README.md](README.md).

---

## 1. The vision

Most sales-call tooling tells you a call went badly *after the fact* and in
aggregate. Rapport's bet is that the useful signal is **what went wrong, and
exactly when** — and that you can close the loop automatically:

```
   ┌──────────────┐   ┌────────────────────┐   ┌─────────────────────┐   ┌────────────────────┐
   │ 1. Voice agent│──▶│ 2. Real-time, multi│──▶│ 3. Align sentiment  │──▶│ 4. Overmind rewrites│
   │   makes a sale │   │   modal sentiment  │   │   to the transcript │   │   the agent, better │
   │   call         │   │   (face/voice/text)│   │   on one timeline   │   │   prompt next round │
   └──────────────┘   └────────────────────┘   └─────────────────────┘   └────────────────────┘
            ▲                                                                       │
            └───────────────────────── improved agent loops back ───────────────────┘
```

The core idea that makes steps 3–4 work: **everything is stamped on the same
call clock** (`tSec` = seconds since the call connected). A transcript line and
the customer's facial/vocal/textual emotion *during that line* share the same
timeline, so we can point at the exact moment the mood turned and feed that —
with surrounding context — into agent improvement.

**Phases 1 and 2 (the voice agent + live sentiment capture) are built. Phases 3
and 4 (analysis + Overmind self-improvement) are next.**

---

## 2. What's built so far

### 2a. The voice agent (call) — DONE

A browser-based voice agent that takes a sales brief and **places a live voice
call to you**, opening and driving the conversation toward a goal you set.

- **Brief, set in the UI:** call objective (what to optimise for), brand,
  product, elevator pitch, target persona, and what we know about this customer.
- **Goal-driven:** the objective you type (e.g. "Book a product demo") becomes
  the agent's primary objective; it follows an explicit structure — open/hook →
  discovery → pitch → objections → close — and aims for ~2 minutes (soft target,
  closing takes priority over the clock).
- **Agent speaks first** and runs the call naturally over WebRTC audio.
- **Agent ends the call itself** via an `end_call` function tool once the deal
  closes, a next step is agreed, or the customer wants to stop — recording a
  structured `outcome`. Manual **End call** and a 6-minute safety cap also exist.

Built on **Azure OpenAI GPT Realtime** (deployment `gpt-realtime-2`,
swedencentral) over **WebRTC**, using the GA endpoints
`/openai/v1/realtime/client_secrets` and `/openai/v1/realtime/calls`.

**Security model:** the real API key never reaches the browser. The Python
backend mints a short-lived (~1 min) *ephemeral* token; the browser uses that to
open the WebRTC connection directly to Azure.

### 2b. Real-time multimodal sentiment — DONE

A sentiment overlay ([static/sentiment.js](static/sentiment.js)) runs entirely
**in the browser, off the call path** (it only reads the mic + webcam; it never
touches the Azure/WebRTC connection). Three independent off-the-shelf signals:

| Signal | Model | Cadence | What it reads |
| --- | --- | --- | --- |
| **face** | face-api.js (TinyFaceDetector + faceExpressionNet) | ~2.5 Hz | webcam expressions |
| **voice** | Transformers.js `wav2vec2-base-superb-er` | every ~3 s | mic prosody / tone |
| **text** | Transformers.js `distilbert-sst-2` | per utterance | transcript words |

Each sample collapses to a **valence in [-1, +1]**, plus the raw per-emotion
`scores`. A weighted blend (text 0.40 / face 0.35 / voice 0.25) gives an
**overall customer sentiment**, shown live as a number, per-signal bars, and a
scrolling timeline chart. Every sample is stamped with `t` on the **same `tSec`
clock** as the transcript.

Resilience built in: face-api (a UMD global) and Transformers.js (dynamic
import) load independently, so if one CDN/model fails the others still run.

### 2c. Persistence — DONE

On call end, the browser POSTs the full record to the backend, saved to
`transcripts/*.json`. This is the artifact phases 3–4 consume.

---

## 3. Data model

The saved transcript (`transcripts/<epoch>-<brand>.json`):

```json
{
  "campaign": { "goal": "Book a product demo", "brand": "...", "product": "...",
                "pitch": "...", "persona": "...", "customer": "..." },
  "started_at": "2026-06-10T12:00:00.000Z",
  "duration_sec": 92.4,
  "ended_by": "agent",
  "call_outcome": { "outcome": "next_step_agreed", "summary": "Booked a demo for Thursday." },
  "entries": [
    { "role": "agent", "text": "Hi, this is …", "tSec": 0.8, "at": "..." },
    { "role": "user",  "text": "Sorry, who?",   "tSec": 6.1, "at": "..." }
  ],
  "sentiment": [
    { "t": 6.2, "kind": "face",  "valence": -0.31, "scores": { "neutral": 0.5, "angry": 0.3 } },
    { "t": 6.1, "kind": "text",  "role": "user", "valence": -0.95, "scores": { "NEGATIVE": 0.95 } },
    { "t": 7.0, "kind": "voice", "valence": -0.42, "scores": { "ang": 0.5, "neu": 0.4 } }
  ]
}
```

- **`tSec` / `t`** — the shared clock. Join `entries` and `sentiment` on it to ask
  "what was the customer feeling while the agent said X?"
- **`call_outcome` / `ended_by`** — a labeled result for each call, useful as
  training/evaluation signal in phase 4.

---

## 4. Architecture & request flow

```
browser  ──POST /session (brief)──────────▶  FastAPI  ──client_secrets (api-key)──▶  Azure
                                                  └── builds system prompt + end_call tool
browser  ◀── ephemeral token + webrtc_url ──  FastAPI
browser  ──WebRTC SDP (ephemeral token)──────────────────────────────────────────▶  Azure
browser  ◀───────────── audio in/out + data-channel events ───────────────────────  Azure
   │
   ├─ webcam + mic ──▶ sentiment.js (face/voice/text → valence, on tSec clock)   [stays in browser]
   │
browser  ──POST /transcript (entries + sentiment + outcome)──▶  FastAPI  ─▶  transcripts/*.json
```

### Components

| File | Role |
| --- | --- |
| [app.py](app.py) | FastAPI backend: `/session` (mint token + build prompt), `/transcript` (save), `/health`. Prompt is built in `build_instructions()`; the hang-up tool is `END_CALL_TOOL`. |
| [static/index.html](static/index.html) | UI: the brief form, live transcript, live sentiment panel; WebRTC setup, data-channel event handling, `end_call` handling, save. |
| [static/sentiment.js](static/sentiment.js) | In-browser face/voice/text sentiment, fusion, live rendering, `window.RapportSentiment` API. |
| `transcripts/` | Saved calls (gitignored). |

---

## 5. Tech stack

- **Backend:** Python 3.12, FastAPI + Uvicorn, `requests`, `python-dotenv`.
  Dedicated conda env **`rapport`** (isolated from the shared `morpheus-clean`).
- **Realtime voice:** Azure OpenAI GPT Realtime (`gpt-realtime-2`) over WebRTC, GA API.
- **Sentiment (all client-side):** face-api.js, Transformers.js (wav2vec2 +
  DistilBERT), Web Audio API, canvas.

Configuration lives in `.env` (see [.env.example](.env.example)):
`AZURE_REALTIME_ENDPOINT`, `AZURE_REALTIME_DEPLOYMENT`, `AZURE_REALTIME_API_KEY`,
`AGENT_VOICE`, `INPUT_TRANSCRIPTION_MODEL`.

---

## 6. What's next (not built yet)

### Phase 3 — sentiment ↔ transcript analysis
Given a saved call, align the `sentiment` track to `entries` on `tSec` and
surface the moments the mood dropped: which agent turn preceded a negative swing,
how the customer recovered (or didn't), and where the call's outcome was decided.
Output a structured "what went wrong, when" report per call.

### Phase 4 — Overmind self-improvement loop  (SCAFFOLDED)
The integration with [Overmind](https://github.com/overmind-core/overmind) is wired
up — see [overmind/README.md](overmind/README.md). In short:

- The sales prompt is now the single optimizable artifact (`SALES_SYSTEM_PROMPT` in
  [sales_agent.py](sales_agent.py)), shared by the live call and the optimizer, so an
  optimized prompt ships straight to real calls.
- `sales_agent.py:run()` exposes the Overmind `dict -> dict` contract by **simulating**
  a call (our prompt vs. a simulated customer in Azure chat) — a live mic call can't
  sit in an optimization loop.
- [overmind/build_dataset.py](overmind/build_dataset.py) turns real, analysis-enriched
  calls into scenarios whose simulated customer replays the moments our sentiment
  analysis flagged as failures; `expected_output` is the objective we want hit.
- [overmind/policies.md](overmind/policies.md) + [overmind/eval_spec.json](overmind/eval_spec.json)
  define what "good" means (objective hit + honesty + sentiment recovery).

Remaining: install Overmind, run the optimize loop, and paste the winning prompt back
into `sales_agent.py`. The `call_outcome` labels and sentiment trajectory are the
optimisation signal. This closes the loop in section 1.

---

## 7. Known trade-offs / notes

- **Prompt is visible in the browser.** The WebRTC data-channel filter is *off*
  because the `end_call` function events are only delivered when it's off. The
  system prompt is therefore visible in devtools — fine for an internal demo. To
  hide it, proxy the SDP exchange through the backend (Azure's "observer" pattern).
- **Transcription model.** Human speech is transcribed with `whisper-1` by
  default; if session creation 400s, switch `INPUT_TRANSCRIPTION_MODEL` to
  `gpt-4o-transcribe`.
- **Sentiment models download on first load** from CDN/HF hub; the call can start
  before they finish (signals just begin appearing once ready).
- **Privacy:** webcam + mic are read locally for sentiment and never sent to a
  third party; only the derived valence/scores are saved in the transcript.
