"""
Rapport — voice sales agent backend.

Mints short-lived (ephemeral) Azure OpenAI Realtime tokens so the browser can
open a WebRTC voice call without ever seeing the real API key, builds the
agent's system prompt from the campaign fields, and stores the resulting
transcript (with per-utterance timing) for the later sentiment / Overmind step.

Run:  uvicorn app:app --reload --port 8000
Open: http://localhost:8000
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import analysis
from sales_agent import build_instructions, END_CALL_TOOL

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TRANSCRIPT_DIR = BASE_DIR / "transcripts"
TRANSCRIPT_DIR.mkdir(exist_ok=True)

ENDPOINT = os.environ.get("AZURE_REALTIME_ENDPOINT", "").strip()
DEPLOYMENT = os.environ.get("AZURE_REALTIME_DEPLOYMENT", "gpt-realtime-2").strip()
API_KEY = os.environ.get("AZURE_REALTIME_API_KEY", "").strip()
VOICE = os.environ.get("AGENT_VOICE", "marin").strip()
TRANSCRIBE_MODEL = os.environ.get("INPUT_TRANSCRIPTION_MODEL", "whisper-1").strip()


def resource_base(endpoint: str) -> str:
    """Reduce the full endpoint URL down to scheme://host."""
    parts = urlsplit(endpoint)
    if not parts.scheme or not parts.netloc:
        raise RuntimeError(
            "AZURE_REALTIME_ENDPOINT is missing or malformed. Expected something "
            "like https://<resource>.cognitiveservices.azure.com/..."
        )
    return f"{parts.scheme}://{parts.netloc}"


BASE = resource_base(ENDPOINT) if ENDPOINT else ""


# --------------------------------------------------------------------------- #
# Agent prompt
# --------------------------------------------------------------------------- #
class Campaign(BaseModel):
    brand: str = Field(default="")
    product: str = Field(default="")
    pitch: str = Field(default="")        # elevator pitch
    persona: str = Field(default="")      # target persona
    customer: str = Field(default="")     # what we know about this customer
    goal: str = Field(default="")         # the objective for THIS call (set in the UI)


# build_instructions() and END_CALL_TOOL now live in sales_agent.py (imported above)
# so the live voice call and the Overmind-optimized simulation share ONE prompt.
# Optimize the prompt in sales_agent.py and the live agent here improves with it.


# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #
app = FastAPI(title="Rapport voice agent")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    return {
        "ok": bool(BASE and API_KEY),
        "base": BASE,
        "deployment": DEPLOYMENT,
        "voice": VOICE,
        "has_key": bool(API_KEY),
    }


@app.post("/session")
def create_session(campaign: Campaign) -> JSONResponse:
    """
    Mint an ephemeral Realtime token configured for this campaign, and return it
    (plus the WebRTC base URL) to the browser. The real API key never leaves here.
    """
    if not BASE:
        raise HTTPException(500, "AZURE_REALTIME_ENDPOINT is not configured (.env).")
    if not API_KEY:
        raise HTTPException(500, "AZURE_REALTIME_API_KEY is not configured (.env).")

    instructions = build_instructions(campaign.model_dump())

    session_config = {
        "session": {
            "type": "realtime",
            "model": DEPLOYMENT,
            "instructions": instructions,
            "audio": {
                "input": {
                    "transcription": {"model": TRANSCRIBE_MODEL},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 600,
                    },
                },
                "output": {"voice": VOICE},
            },
            "tools": [END_CALL_TOOL],
            "tool_choice": "auto",
        }
    }

    url = f"{BASE}/openai/v1/realtime/client_secrets"
    try:
        resp = requests.post(
            url,
            headers={"api-key": API_KEY, "Content-Type": "application/json"},
            json=session_config,
            timeout=30,
        )
    except requests.RequestException as e:
        raise HTTPException(502, f"Could not reach Azure: {e}")

    if resp.status_code != 200:
        raise HTTPException(
            resp.status_code,
            f"Token request failed ({resp.status_code}): {resp.text[:500]}",
        )

    token = resp.json().get("value", "")
    if not token:
        raise HTTPException(502, f"No ephemeral token in response: {resp.text[:500]}")

    return JSONResponse(
        {
            "token": token,
            # No webrtcfilter: the agent uses an end_call function tool, and those
            # function-call events are only delivered when the filter is off.
            "webrtc_url": f"{BASE}/openai/v1/realtime/calls",
            "deployment": DEPLOYMENT,
            "voice": VOICE,
        }
    )


class TranscriptPayload(BaseModel):
    campaign: Campaign
    started_at: str          # ISO timestamp from the browser
    duration_sec: float = 0
    entries: list[dict] = Field(default_factory=list)
    call_outcome: dict | None = None   # set when the agent ended via end_call: {outcome, summary}
    ended_by: str = "user"             # "agent" (end_call) or "user" (End call button)
    sentiment: list[dict] = Field(default_factory=list)  # [{t, kind, role?, valence, scores}] aligned to tSec


@app.post("/transcript")
def save_transcript(payload: TranscriptPayload) -> dict:
    """Persist the finished call, then run the post-call LLM sentiment analysis."""
    data = payload.model_dump()
    slug = re.sub(r"[^a-z0-9]+", "-", (payload.campaign.brand or "call").lower()).strip("-")
    stem = f"{int(time.time())}-{slug or 'call'}"
    (TRANSCRIPT_DIR / f"{stem}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))

    result = {"saved": f"{stem}.json", "entries": len(payload.entries)}

    # Post-call language sentiment + overall score (best-effort: never fail the save).
    try:
        enriched = analysis.analyze_and_enrich(data)
        (TRANSCRIPT_DIR / f"{stem}-analyzed.json").write_text(
            json.dumps(enriched, indent=2, ensure_ascii=False)
        )
        result["analyzed"] = f"{stem}-analyzed.json"
        result["overall_sentiment_score"] = enriched.get("overall_sentiment_score")
        result["analysis"] = enriched.get("analysis")
    except Exception as e:  # noqa: BLE001 — surface the reason, keep the saved transcript
        result["analysis_error"] = str(e)

    return result


class AnalyzeRequest(BaseModel):
    filename: str | None = None       # a file in transcripts/ (raw, not -analyzed)
    transcript: dict | None = None    # or pass the transcript object directly


@app.post("/analyze")
def analyze_endpoint(req: AnalyzeRequest) -> JSONResponse:
    """Re-run (or run) the post-call analysis on a saved transcript or a posted object."""
    if req.transcript is not None:
        data = req.transcript
        stem = None
    elif req.filename:
        path = TRANSCRIPT_DIR / req.filename
        if not path.is_file():
            raise HTTPException(404, f"No such transcript: {req.filename}")
        data = json.loads(path.read_text())
        stem = path.stem.removesuffix("-analyzed")
    else:
        raise HTTPException(400, "Provide either 'filename' or 'transcript'.")

    try:
        enriched = analysis.analyze_and_enrich(data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Analysis failed: {e}")

    if stem:
        (TRANSCRIPT_DIR / f"{stem}-analyzed.json").write_text(
            json.dumps(enriched, indent=2, ensure_ascii=False)
        )
    return JSONResponse(enriched)


# Static assets (served last so "/" above takes precedence).
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
