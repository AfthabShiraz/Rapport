"""GPT chat + vision. Works against standard OpenAI (OPENAI_API_KEY) or the
team's Azure resource via its v1 surface (AZURE_REALTIME_* + AZURE_CHAT_DEPLOYMENT).
Every function degrades gracefully — a failure returns None / a heuristic,
never an exception into the call loop."""
import json
import logging
import os
from urllib.parse import urlsplit

from openai import AsyncOpenAI

log = logging.getLogger("openai_client")

VISION_PROMPT = """Analyze this video frame of a person on a sales call.
Return ONLY valid JSON:
{ "engagement": <int 0-10>, "trust_signal": <float 0-1>,
  "emotion": <"neutral"|"interested"|"skeptical"|"frustrated"|"positive">,
  "eye_contact": <bool>, "posture": <"open"|"closed"|"leaning_in"|"leaning_back">,
  "objection_risk": <float 0-1> }"""

DEFAULT_SENTIMENT = {
    "engagement": 5,
    "trust_signal": 0.5,
    "emotion": "neutral",
    "eye_contact": True,
    "posture": "open",
    "objection_risk": 0.2,
}

EMOTIONS = {"neutral", "interested", "skeptical", "frustrated", "positive"}
POSTURES = {"open", "closed", "leaning_in", "leaning_back"}


def _make_client():
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if key:
        return AsyncOpenAI(api_key=key), os.getenv("CHAT_MODEL", "gpt-4o")

    az_key = os.getenv("AZURE_REALTIME_API_KEY", "").strip()
    az_ep = os.getenv("AZURE_REALTIME_ENDPOINT", "").strip()
    if az_key and az_ep and "PUT_YOUR" not in az_key:
        p = urlsplit(az_ep)
        base = f"{p.scheme}://{p.netloc}/openai/v1/"
        model = os.getenv("AZURE_CHAT_DEPLOYMENT", "gpt-4o")
        return (
            AsyncOpenAI(api_key=az_key, base_url=base, default_headers={"api-key": az_key}),
            model,
        )
    return None, None


client, CHAT_MODEL = _make_client()
if client is None:
    log.warning("no chat-capable credentials — vision/diagnosis fall back to heuristics")


def _clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


async def analyze_frame(jpeg_b64: str):
    """One camera frame -> sentiment dict, or None on any failure."""
    if client is None:
        return None
    try:
        r = await client.chat.completions.create(
            model=os.getenv("VISION_MODEL", CHAT_MODEL),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{jpeg_b64}",
                                "detail": "low",
                            },
                        },
                    ],
                }
            ],
            response_format={"type": "json_object"},
            max_tokens=120,
            timeout=8,
        )
        d = json.loads(r.choices[0].message.content)
        return {
            "engagement": int(_clamp(d.get("engagement"), 0, 10, 5)),
            "trust_signal": round(_clamp(d.get("trust_signal"), 0, 1, 0.5), 2),
            "emotion": d.get("emotion") if d.get("emotion") in EMOTIONS else "neutral",
            "eye_contact": bool(d.get("eye_contact", True)),
            "posture": d.get("posture") if d.get("posture") in POSTURES else "open",
            "objection_risk": round(_clamp(d.get("objection_risk"), 0, 1, 0.2), 2),
        }
    except Exception as e:
        log.warning("frame analysis failed: %s", e)
        return None


async def classify_outcome(transcript: str) -> str:
    """converted | follow_up | lost — LLM first, keyword heuristic fallback."""
    if client is not None:
        try:
            r = await client.chat.completions.create(
                model=CHAT_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "Classify this sales call transcript. Return JSON only: "
                        '{"outcome": "converted"|"follow_up"|"lost"}. converted = prospect '
                        "agreed to buy/book/sign up; follow_up = a concrete next step was "
                        "agreed; lost = neither.",
                    },
                    {"role": "user", "content": transcript[:8000]},
                ],
                response_format={"type": "json_object"},
                max_tokens=20,
                timeout=10,
            )
            o = json.loads(r.choices[0].message.content).get("outcome")
            if o in ("converted", "follow_up", "lost"):
                return o
        except Exception as e:
            log.warning("outcome classifier failed: %s", e)

    t = transcript.lower()
    if any(k in t for k in ("sign me up", "let's do it", "book it", "i'm in", "sounds good, let")):
        return "converted"
    if any(k in t for k in ("book", "survey", "follow up", "next week", "this week", "schedule")):
        return "follow_up"
    return "lost"


async def generate_diagnosis(payload: str):
    """Post-call failure diagnosis. Returns the parsed dict or None (caller
    retries once, then uses the deterministic fallback)."""
    if client is None:
        return None
    try:
        r = await client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are a sales call analyst. Given the transcript, sentiment "
                    "timeline, scores, and the agent's prior call history, identify the 1-3 "
                    "most impactful failures. Cite timestamps. Reference cross-call patterns "
                    "when the history supports them. before_behavior is a short snippet of how "
                    "the agent currently behaves; after_behavior is a concrete, prompt-ready "
                    "instruction for how it should behave instead (specific reframes, social "
                    "proof, scarcity, next-step anchoring). Return JSON only: "
                    '{ "diagnosis": [{"title": str, "detail": str, "timestamp": str}], '
                    '"before_behavior": str, "after_behavior": str }',
                },
                {"role": "user", "content": payload[:14000]},
            ],
            response_format={"type": "json_object"},
            max_tokens=700,
            timeout=30,
        )
        d = json.loads(r.choices[0].message.content)
        diagnosis = d.get("diagnosis")
        if (
            isinstance(diagnosis, list)
            and 1 <= len(diagnosis) <= 3
            and d.get("before_behavior")
            and d.get("after_behavior")
        ):
            return d
        log.warning("diagnosis JSON malformed: %s", str(d)[:200])
        return None
    except Exception as e:
        log.warning("diagnosis generation failed: %s", e)
        return None
