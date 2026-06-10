# Rapport — voice sales agent

A browser-based voice agent that takes a sales brief (brand, product, elevator
pitch, target persona, what we know about the customer) and **places a live
voice call to you**, opening and driving the conversation to sell the product.

Built on Azure OpenAI's GPT Realtime API over WebRTC. Every call is transcribed
with per-utterance timing and saved to `transcripts/`, ready for the next phase
(sentiment scoring + Overmind agent improvement).

## How it works

```
browser  ──POST /session──▶  FastAPI  ──client_secrets──▶  Azure  (mints ephemeral token)
browser  ──WebRTC SDP (ephemeral token)──────────────────▶  Azure  (audio in/out)
browser  ──POST /transcript─▶  FastAPI  ─▶  transcripts/*.json
```

The real API key never reaches the browser — the backend mints a 1-minute
ephemeral token and the browser uses that for the direct WebRTC connection.

## Setup

```bash
cd Rapport
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then paste your AZURE_REALTIME_API_KEY into .env
```

## Run

```bash
uvicorn app:app --reload --port 8000
```

Open http://localhost:8000 — `getUserMedia` needs a secure context, which
`localhost` counts as, so the mic works without HTTPS.

Fill in the brief, click **Start call**, allow mic access. The agent greets you
and starts selling. Talk back; it listens and responds. Click **End call** to
save the transcript.

## Call goal & length

- **Goal** — set per call in the **Call objective** field (e.g. "Book a product
  demo", "Close the sale today"). It becomes the agent's primary objective; if left
  blank it defaults to closing the deal. The agent follows an explicit structure:
  open/hook → discovery → pitch → objections → close.
- **Length** — the agent is told to aim for ~2 minutes and drive toward the close,
  but that's a soft target, not a cutoff (closing takes priority over the clock).
  The agent ends the call **itself** via an `end_call` tool once the deal is closed,
  a next step is agreed, or the customer wants to stop — recording an `outcome`. You
  can also end manually with **End call**. A 6-minute browser safety cap backstops
  runaways.

## Transcript format

```json
{
  "campaign": { "goal": "Book a product demo", "brand": "...", "product": "...", "pitch": "...", "persona": "...", "customer": "..." },
  "started_at": "2026-06-10T12:00:00.000Z",
  "duration_sec": 92.4,
  "ended_by": "agent",
  "call_outcome": { "outcome": "next_step_agreed", "summary": "Booked a demo for Thursday." },
  "entries": [
    { "role": "agent", "text": "Hi, this is …", "tSec": 0.8,  "at": "..." },
    { "role": "user",  "text": "Sorry, who?",   "tSec": 6.1,  "at": "..." }
  ],
  "sentiment": [
    { "t": 6.2, "kind": "face",  "valence": -0.31, "scores": { "neutral": 0.5, "angry": 0.3 } },
    { "t": 6.1, "kind": "text",  "role": "user", "valence": -0.95, "scores": { "NEGATIVE": 0.95 } },
    { "t": 7.0, "kind": "voice", "valence": -0.42, "scores": { "ang": 0.5, "neu": 0.4 } }
  ]
}
```

`sentiment` is a real-time track captured during the call, each sample stamped
with `t` on the **same `tSec` clock** as `entries` — so a transcript line and the
customer's emotion during it line up directly. `kind` is `face` (webcam,
face-api.js ~2.5 Hz) or `voice` (mic prosody, wav2vec2 every ~3 s). `valence` is a
single −1..+1 score; `scores` is the raw per-emotion breakdown. (Language/content
sentiment is **not** in this live track — it's computed post-call by the LLM, see
below, because a small in-browser text model mislabels neutral business speech.)

## Post-call sentiment analysis (LLM)

When a call is saved, the backend runs one Azure OpenAI chat pass over the
transcript (`analysis.py`) and writes `<name>-analyzed.json` next to the raw file.
It scores **every turn's** customer sentiment, finds the **key moments** where
sentiment shifted and what triggered them, and produces one **overall score** —
the number that feeds Overmind. The live face/voice track is summarised per turn
and handed to the model as nonverbal evidence, so the language read is grounded in
how the customer actually looked/sounded.

The enriched file adds, per entry, `language_sentiment: { score, label, reason }`,
plus top-level:

```json
"overall_sentiment_score": -0.42,
"analysis": {
  "overall": { "score": -0.42, "label": "slightly_negative", "call_health": 35,
               "outcome": "declined", "summary": "…" },
  "key_moments": [ { "tSec": 37.9, "agent_index": 8, "trigger": "long pitch",
                     "shift": "patience → frustration", "fix": "…" } ],
  "what_went_wrong": ["…"], "what_worked": ["…"]
}
```

**Requires a chat deployment.** This uses the *same* Azure resource + key as the
realtime agent, but a **standard chat model** (e.g. `gpt-4o`), which is a separate
deployment. Create one in the Azure AI Foundry / portal, then set
`AZURE_CHAT_DEPLOYMENT` in `.env` to its deployment name. Analysis is best-effort:
if the deployment is missing, the call still saves and the response carries an
`analysis_error` explaining why. Re-run analysis any time with
`POST /analyze {"filename": "<name>.json"}`.

`ended_by` is `"agent"` (hung up via `end_call`) or `"user"`. `call_outcome` is set
only when the agent ended the call — a useful label for the sentiment phase.

`tSec` is seconds since the call connected — that's the timeline the sentiment
step will align scores against to find *what went wrong, when*.

## Notes / knobs

- `AGENT_VOICE` — voice for the agent (`marin`, `alloy`, `coral`, …).
- `INPUT_TRANSCRIPTION_MODEL` — model that transcribes *your* speech. If session
  creation fails, try `gpt-4o-transcribe` or remove transcription in `app.py`.
- The agent prompt is built in `build_instructions()` in [app.py](app.py) — tune
  the sales behaviour, structure, and timing there. The `end_call` tool is defined
  just below it.
- The data-channel filter (`webrtcfilter`) is **off** so the `end_call` function
  events reach the browser. Trade-off: the system prompt is visible in browser
  devtools — fine for an internal demo. To hide it, proxy the SDP exchange through
  the backend instead (see Azure's WebRTC "observer" pattern).
