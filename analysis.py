"""
Post-call sentiment analysis.

Takes a saved call (campaign + transcript entries + the real-time face/voice
track) and runs ONE Azure OpenAI chat pass that:
  - scores each turn's customer sentiment (-1..+1) with a short reason,
  - finds the key moments where sentiment shifted and what triggered them,
  - produces a single OVERALL sentiment score for the call.

The result is embedded back into the transcript (per-entry `language_sentiment`
plus a top-level `analysis` block and `overall_sentiment_score`) so we know what
sentiment was detected when — that enriched object is what feeds Overmind.

The face/voice track captured live is passed to the model as nonverbal evidence,
summarised per turn, so the language read is grounded in how the customer actually
looked/sounded at that moment.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import requests

# Reuse the same Azure resource + key as the realtime agent; only the deployment
# (a standard chat model, e.g. gpt-4o) and api-version differ. Read at call time
# so .env (loaded by app.py) is in effect regardless of import order.
def _cfg() -> dict:
    return {
        "endpoint": os.environ.get("AZURE_REALTIME_ENDPOINT", "").strip(),
        "key": os.environ.get("AZURE_REALTIME_API_KEY", "").strip(),
        "deployment": os.environ.get("AZURE_CHAT_DEPLOYMENT", "gpt-4o").strip(),
        "api_version": os.environ.get("AZURE_CHAT_API_VERSION", "2024-10-21").strip(),
    }


def _base(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def _avg(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def nonverbal_per_turn(entries: list[dict], sentiment: list[dict]) -> dict[int, dict]:
    """Average the live face/voice valence inside each turn's [tSec, next tSec) window."""
    out: dict[int, dict] = {}
    for i, e in enumerate(entries):
        start = e.get("tSec", 0)
        end = entries[i + 1].get("tSec", float("inf")) if i + 1 < len(entries) else float("inf")
        window = [s for s in sentiment if start <= s.get("t", -1) < end]
        out[i] = {
            "face": _avg([s["valence"] for s in window if s.get("kind") == "face"]),
            "voice": _avg([s["valence"] for s in window if s.get("kind") == "voice"]),
        }
    return out


SCHEMA_HINT = """\
Return ONLY a JSON object with exactly this shape:
{
  "overall": {
    "score": <float -1..1, the customer's overall sentiment across the whole call>,
    "label": "<negative|slightly_negative|neutral|slightly_positive|positive>",
    "call_health": <int 0-100, how well the call went toward the objective>,
    "outcome": "<one short phrase: e.g. 'declined', 'next step agreed'>",
    "summary": "<2-3 sentences: what happened and why the customer felt how they did>"
  },
  "turns": [
    { "index": <int, matches the turn index given>, "sentiment": <float -1..1>,
      "label": "<negative|neutral|positive>", "reason": "<short, concrete>" }
  ],
  "key_moments": [
    { "tSec": <float>, "agent_index": <int|null, the agent turn that triggered this>,
      "trigger": "<what the agent said/did>", "shift": "<how the customer's sentiment moved>",
      "fix": "<concretely, what the agent should have done instead>" }
  ],
  "what_went_wrong": ["<short bullet>", ...],
  "what_worked": ["<short bullet>", ...]
}
Score EVERY turn (both roles), but weight the OVERALL toward the CUSTOMER's turns.
Use the nonverbal hints as evidence but trust the words when they conflict."""


def build_messages(data: dict) -> list[dict]:
    c = data.get("campaign", {})
    entries = data.get("entries", [])
    nv = nonverbal_per_turn(entries, data.get("sentiment", []))

    lines = []
    for i, e in enumerate(entries):
        n = nv.get(i, {})
        hint = []
        if n.get("face") is not None:
            hint.append(f"face {n['face']:+.2f}")
        if n.get("voice") is not None:
            hint.append(f"voice {n['voice']:+.2f}")
        hint_s = f"  [nonverbal: {', '.join(hint)}]" if hint else ""
        lines.append(f"[{i}] t={e.get('tSec', 0):>5}s {e.get('role', '?'):<5}: {e.get('text', '')}{hint_s}")
    transcript = "\n".join(lines)

    system = (
        "You are a sales-call sentiment analyst. You read a transcript of an outbound "
        "sales call (an AI agent selling to a human customer), plus per-turn nonverbal "
        "cues (facial + vocal valence, -1..+1) captured live, and you assess the "
        "CUSTOMER's sentiment turn by turn, identify exactly where and why it shifted, "
        "and give one overall score. Be precise and concrete — this feeds an automated "
        "step that rewrites the agent to do better."
    )
    user = (
        f"CALL OBJECTIVE: {c.get('goal') or '(close the deal)'}\n"
        f"BRAND: {c.get('brand')}  PRODUCT: {c.get('product')}\n"
        f"PITCH: {c.get('pitch')}\n"
        f"PERSONA: {c.get('persona')}\n"
        f"CUSTOMER: {c.get('customer')}\n"
        f"OUTCOME (as recorded): {data.get('call_outcome')}\n\n"
        f"TRANSCRIPT (index, time, role, text, nonverbal):\n{transcript}\n\n"
        f"{SCHEMA_HINT}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
    return json.loads(t.strip())


def analyze(data: dict, timeout: int = 60) -> dict:
    """Call the Azure chat model and return the parsed analysis dict. Raises on failure."""
    cfg = _cfg()
    base = _base(cfg["endpoint"])
    if not base:
        raise RuntimeError("AZURE_REALTIME_ENDPOINT not configured.")
    if not cfg["key"]:
        raise RuntimeError("AZURE_REALTIME_API_KEY not configured.")

    url = f"{base}/openai/deployments/{cfg['deployment']}/chat/completions?api-version={cfg['api_version']}"
    body = {
        "messages": build_messages(data),
        "response_format": {"type": "json_object"},
        # gpt-5-family deployments require max_completion_tokens (not max_tokens) and
        # only support the default temperature, so we don't send one. Headroom is
        # generous because reasoning tokens count against this budget.
        "max_completion_tokens": 4000,
    }
    resp = requests.post(
        url, headers={"api-key": cfg["key"], "Content-Type": "application/json"}, json=body, timeout=timeout
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Azure chat failed ({resp.status_code}) on deployment '{cfg['deployment']}': {resp.text[:400]}"
        )
    content = resp.json()["choices"][0]["message"]["content"]
    return _parse_json(content)


def enrich(data: dict, analysis: dict) -> dict:
    """Embed the analysis back into the transcript object (returns a new dict)."""
    out = json.loads(json.dumps(data))  # deep copy
    by_index = {t.get("index"): t for t in analysis.get("turns", []) if isinstance(t, dict)}
    for i, e in enumerate(out.get("entries", [])):
        t = by_index.get(i)
        if t:
            e["language_sentiment"] = {
                "score": t.get("sentiment"),
                "label": t.get("label"),
                "reason": t.get("reason"),
            }
    out["analysis"] = {
        "overall": analysis.get("overall", {}),
        "key_moments": analysis.get("key_moments", []),
        "what_went_wrong": analysis.get("what_went_wrong", []),
        "what_worked": analysis.get("what_worked", []),
    }
    out["overall_sentiment_score"] = (analysis.get("overall") or {}).get("score")
    return out


def analyze_and_enrich(data: dict) -> dict:
    return enrich(data, analyze(data))
