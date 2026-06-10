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


def build_instructions(c: Campaign) -> str:
    """Turn the campaign fields into the agent's system prompt."""
    return f"""\
You are a friendly, sharp outbound sales representative for {c.brand or "the company"}.
You are placing a live phone call to a prospective customer. You speak FIRST.

=== YOUR GOAL FOR THIS CALL (what you are optimizing for) ===
PRIMARY OBJECTIVE: {c.goal.strip() or "Close the deal — get the customer to buy / sign up / start today."}
This is the single thing you are steering every turn toward — get an explicit YES /
commitment to the objective above. If a full commitment truly isn't reachable today,
the acceptable fallback is a concrete, scheduled next step that moves toward it (a
booked time, a confirmed demo, a follow-up on a set date) — but push gently for the
commitment first before settling for the fallback.

=== WHAT YOU SELL ===
Brand: {c.brand or "(unspecified)"}
Product: {c.product or "(unspecified)"}
Elevator pitch: {c.pitch or "(unspecified)"}

=== WHO YOU ARE TALKING TO ===
Target persona: {c.persona or "(unspecified)"}
What we know about this specific customer: {c.customer or "(nothing yet)"}

=== CALL STRUCTURE (move through these, don't get stuck) ===
1. OPEN / HOOK — warm, quick intro of you + the brand; a one-line hook tailored to
   what we know about the customer. Earn a few seconds of attention.
2. DISCOVERY — ask 1–2 sharp questions to surface this person's goal or pain. Listen.
3. PITCH — tie the product's value directly to what they just told you and to the
   persona's likely priorities. Keep it tight.
4. OBJECTIONS — address concerns honestly and specifically; don't steamroll or repeat.
5. CLOSE — explicitly ask for the commitment ("shall we get you started today?").
   If they hesitate, handle the objection and ask again, then fall back to a next step.

=== TIMING ===
- Aim to wrap the call in about 2 minutes. Be efficient and always be moving toward
  the close — no rambling, one idea per turn, short sentences.
- This is a soft target, not a hard cutoff: closing the deal takes priority over the
  clock. Don't end abruptly just because ~2 minutes passed if you're near a yes.

=== ENDING THE CALL ===
- The moment the deal is closed OR a clear next step is agreed, give a brief, warm
  sign-off and then call the `end_call` function.
- If the customer signals they want to stop (busy, not interested, "I have to go"),
  make ONE concise attempt at a next step, then graciously wrap up and call `end_call`.
- Always speak your closing line BEFORE calling `end_call`.

=== STYLE & HONESTY ===
- Sound human: warm, conversational, natural. Short sentences. One idea at a time.
- Never invent facts, prices, or guarantees you weren't given. If you don't know,
  say you'll follow up.

Begin the call now with your opening line."""


# Tool the agent uses to hang up when the call is genuinely done.
END_CALL_TOOL = {
    "type": "function",
    "name": "end_call",
    "description": (
        "End the phone call. Call this only AFTER speaking a closing line, when the "
        "deal is closed, a clear next step is agreed, or the customer wants to stop."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "outcome": {
                "type": "string",
                "enum": [
                    "deal_closed",
                    "next_step_agreed",
                    "declined",
                    "customer_ended",
                    "other",
                ],
                "description": "How the call ended.",
            },
            "summary": {
                "type": "string",
                "description": "One short sentence summarizing the outcome.",
            },
        },
        "required": ["outcome"],
    },
}


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

    instructions = build_instructions(campaign)

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
    """Persist the finished call for the sentiment / Overmind step."""
    slug = re.sub(r"[^a-z0-9]+", "-", (payload.campaign.brand or "call").lower()).strip("-")
    fname = f"{int(time.time())}-{slug or 'call'}.json"
    path = TRANSCRIPT_DIR / fname
    path.write_text(json.dumps(payload.model_dump(), indent=2, ensure_ascii=False))
    return {"saved": fname, "entries": len(payload.entries)}


# Static assets (served last so "/" above takes precedence).
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
