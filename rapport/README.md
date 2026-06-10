# Rapport — Overmind Sales Agent

Built from `build.md` + `UI.md`. Fully live pipeline: gpt-realtime voice agent,
GPT-4o vision sentiment, post-call diagnosis → one-click prompt optimization.

## Run

```bash
# Backend (port 8001 — 8000 is used by the root prototype)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # first time
.venv/bin/uvicorn main:app --port 8001

# Frontend
cd frontend
npm install        # first time
npm run dev        # http://localhost:5173
```

## Environment

Backend reads `backend/.env` first, then falls back to the repo-root `.env`.

```
OPENAI_API_KEY=        # required (or the AZURE_REALTIME_* trio)
OVERMIND_API_KEY=      # optional — tracing degrades to no-op without it
AGENT_VOICE=marin      # optional
VAD_SILENCE_MS=600     # optional — raise if the agent interrupts you
```

## Demo flow

1. `/setup` — form is pre-filled with Lumio Solar. Save & deploy agent.
2. Start call → speak with the agent (wear headphones). Object with
   "I need to think about it" → End call.
3. Report → read diagnosis → Accept & apply → Start next call.
4. Call 2 runs the optimized prompt — badge reads "After optimization".
5. `/analytics` — seeded demo data until 5 real calls exist.
