"""
The Rapport sales agent — single source of truth for the agent's prompt/strategy,
and the entrypoint Overmind optimizes.

Two things live here:

1. SALES_SYSTEM_PROMPT / build_instructions() — the prompt that drives BOTH the
   live voice call (imported by app.py) and the simulated call below. This is the
   artifact Overmind rewrites: improve it here and the live agent improves too.

2. run(input_data) -> dict — the Overmind entrypoint. Because a live mic call
   can't sit inside an optimization loop, run() plays a *text simulation* of the
   call: our agent (this prompt) versus a simulated customer (an Azure chat model
   role-playing the persona), ending via the same end_call tool. It returns the
   call outcome + transcript so Overmind can score it.

Register with:  /overmind-register-agent sales_agent.py   (entrypoint sales_agent:run)
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import requests

# --------------------------------------------------------------------------- #
# THE OPTIMIZABLE PROMPT  (Overmind rewrites this)
# --------------------------------------------------------------------------- #
SALES_SYSTEM_PROMPT = """\
You are a friendly, sharp outbound sales representative for {brand}.
You are placing a live phone call to a prospective customer. You speak FIRST.

=== YOUR GOAL FOR THIS CALL (what you are optimizing for) ===
PRIMARY OBJECTIVE: {goal}
This is the single thing you are steering every turn toward — get an explicit YES /
commitment to the objective above. If a full commitment truly isn't reachable today,
the acceptable fallback is a concrete, scheduled next step that moves toward it (a
booked time, a confirmed demo, a follow-up on a set date) — but push gently for the
commitment first before settling for the fallback.

=== WHAT YOU SELL ===
Brand: {brand}
Product: {product}
Elevator pitch: {pitch}

=== WHO YOU ARE TALKING TO ===
Target persona: {persona}
What we know about this specific customer: {customer}

=== CALL STRUCTURE (move through these, don't get stuck) ===
1. OPEN / HOOK — warm, quick intro of you + the brand; a one-line hook tailored to
   what we know about the customer. Earn a few seconds of attention.
2. DISCOVERY — ask 1-2 sharp questions to surface this person's goal or pain. Listen.
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
  sign-off and then call the end_call function.
- If the customer signals they want to stop (busy, not interested, "I have to go"),
  make ONE concise attempt at a next step, then graciously wrap up and call end_call.
- Always speak your closing line BEFORE calling end_call.

=== STYLE & HONESTY ===
- Sound human: warm, conversational, natural. Short sentences. One idea at a time.
- Never invent facts, prices, or guarantees you weren't given. If you don't know,
  say you'll follow up.

Begin the call now with your opening line."""


def build_instructions(c: dict) -> str:
    """Fill the prompt template from a campaign dict (used live AND in simulation)."""
    c = c or {}
    goal = (c.get("goal") or "").strip()
    return SALES_SYSTEM_PROMPT.format(
        brand=(c.get("brand") or "the company").strip() or "the company",
        goal=goal or "Close the deal — get the customer to buy / sign up / start today.",
        product=(c.get("product") or "(unspecified)").strip() or "(unspecified)",
        pitch=(c.get("pitch") or "(unspecified)").strip() or "(unspecified)",
        persona=(c.get("persona") or "(unspecified)").strip() or "(unspecified)",
        customer=(c.get("customer") or "(nothing yet)").strip() or "(nothing yet)",
    )


# end_call tool — realtime shape (imported by app.py) + a chat-API shape (used here).
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
                "enum": ["deal_closed", "next_step_agreed", "declined", "customer_ended", "other"],
                "description": "How the call ended.",
            },
            "summary": {"type": "string", "description": "One short sentence summarizing the outcome."},
        },
        "required": ["outcome"],
    },
}

_END_CALL_CHAT_TOOL = {
    "type": "function",
    "function": {
        "name": "end_call",
        "description": END_CALL_TOOL["description"],
        "parameters": END_CALL_TOOL["parameters"],
    },
}


# --------------------------------------------------------------------------- #
# Simulation harness (Azure chat) — only used by run(), never by the live call.
# --------------------------------------------------------------------------- #
def _chat(messages: list[dict], tools: list | None = None, tool_choice: str | None = None,
          response_format: dict | None = None, max_tokens: int = 600, timeout: int = 60) -> dict:
    """One Azure chat completion. Mirrors analysis.py's config (no temperature; uses
    max_completion_tokens so gpt-5-family deployments work too). Returns the message."""
    endpoint = os.environ.get("AZURE_REALTIME_ENDPOINT", "").strip()
    key = os.environ.get("AZURE_REALTIME_API_KEY", "").strip()
    deployment = os.environ.get("AZURE_CHAT_DEPLOYMENT", "gpt-4o").strip()
    api_version = os.environ.get("AZURE_CHAT_API_VERSION", "2024-10-21").strip()
    parts = urlsplit(endpoint)
    base = f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""
    if not base or not key:
        raise RuntimeError("AZURE_REALTIME_ENDPOINT / AZURE_REALTIME_API_KEY not configured.")

    url = f"{base}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
    body: dict = {"messages": messages, "max_completion_tokens": max_tokens}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice or "auto"
    if response_format:
        body["response_format"] = response_format
    resp = requests.post(url, headers={"api-key": key, "Content-Type": "application/json"},
                         json=body, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"Azure chat failed ({resp.status_code}) on '{deployment}': {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]


def _score_sentiment(campaign: dict, transcript: list[dict]) -> dict:
    """
    Text-sentiment pass over the simulated transcript — the simulation's analog of the
    live multimodal sentiment. Returns the CUSTOMER's sentiment as:
      overall  : -1..+1 across the whole call (weighted toward customer turns)
      final    : -1..+1 at the END of the call (the customer's mood as it closes)
      turns    : per customer-turn scores
    """
    lines = [f"[{i}] {t['role']}: {t['text']}" for i, t in enumerate(transcript)]
    system = (
        "You are a sentiment analyst for sales calls. Read the transcript of an AI sales "
        "rep (role 'agent') talking to a human customer (role 'user') and score the "
        "CUSTOMER's sentiment only. Be precise."
    )
    user = (
        f"OBJECTIVE: {campaign.get('goal') or 'close the deal'}\n\n"
        + "\n".join(lines)
        + '\n\nReturn ONLY JSON: {"overall": <float -1..1, customer sentiment across the '
        'whole call>, "final": <float -1..1, the customer\'s sentiment by the END of the '
        'call>, "turns": [{"index": <int of a user turn>, "score": <float -1..1>}]}'
    )
    try:
        msg = _chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"}, max_tokens=1500,
        )
        data = json.loads(msg.get("content") or "{}")
    except (RuntimeError, json.JSONDecodeError):
        return {"overall": None, "final": None, "turns": []}
    clamp = lambda x: max(-1.0, min(1.0, float(x)))  # noqa: E731
    overall = clamp(data["overall"]) if data.get("overall") is not None else None
    final = clamp(data["final"]) if data.get("final") is not None else None
    return {"overall": overall, "final": final, "turns": data.get("turns", [])}


def _customer_prompt(sc: dict) -> str:
    """System prompt for the simulated customer — replays a real call's difficulty."""
    profile = (sc.get("customer_profile") or "").strip() or \
        "Cautious but reachable. Raise one realistic objection before considering anything."
    return (
        "You are role-playing a prospective customer receiving an outbound sales call. "
        f"You match this persona: {sc.get('persona') or 'a busy professional'}. "
        f"Context about you: {sc.get('customer') or 'no prior relationship with the brand'}.\n"
        f"YOUR DISPOSITION ON THIS CALL: {profile}\n"
        "Behave like a real human on a cold call: be a little guarded, interrupt the pitch "
        "with realistic objections, and only agree to buy or to a concrete next step if the "
        "rep genuinely earns it — otherwise stay noncommittal or decline. Keep replies short "
        "and conversational (1-3 sentences). Never break character or mention you are an AI."
    )


def _customer_view(transcript: list[dict]) -> list[dict]:
    """Re-cast the transcript from the customer's POV (the rep's lines are 'user')."""
    return [{"role": "user" if t["role"] == "agent" else "assistant", "content": t["text"]}
            for t in transcript]


def run(input_data: dict) -> dict:
    """
    Overmind entrypoint. Simulates a full sales call for one scenario and reports
    the outcome. `input_data` is a campaign/scenario dict (same fields the UI sends,
    plus an optional `customer_profile` and `max_turns`).
    """
    sc = input_data or {}
    max_turns = int(sc.get("max_turns", 10))
    agent_msgs = [{"role": "system", "content": build_instructions(sc)}]
    customer_sys = _customer_prompt(sc)
    transcript: list[dict] = []
    outcome, summary = None, ""

    for _ in range(max_turns):
        # Agent's turn (may decide to hang up via the end_call tool).
        am = _chat(agent_msgs, tools=[_END_CALL_CHAT_TOOL], tool_choice="auto")
        text = (am.get("content") or "").strip()
        if text:
            transcript.append({"role": "agent", "text": text})
            agent_msgs.append({"role": "assistant", "content": text})
        for tc in (am.get("tool_calls") or []):
            if tc.get("function", {}).get("name") == "end_call":
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                outcome, summary = args.get("outcome", "other"), args.get("summary", "")
        if outcome:
            break

        # Customer's turn.
        cm = _chat([{"role": "system", "content": customer_sys}] + _customer_view(transcript))
        ctext = (cm.get("content") or "").strip()
        transcript.append({"role": "user", "text": ctext})
        agent_msgs.append({"role": "user", "content": ctext})

    outcome = outcome or "incomplete"
    objective_met = outcome in ("deal_closed", "next_step_agreed")

    # Deal component: full credit for a close, half for a booked next step, none else.
    deal_score = 1.0 if outcome == "deal_closed" else (0.5 if outcome == "next_step_agreed" else 0.0)

    # Measure the customer's sentiment (overall + at end of call) on the simulated call.
    sent = _score_sentiment(sc, transcript)

    return {
        "response": {
            "outcome": outcome,
            "deal_closed": outcome == "deal_closed",
            "objective_met": objective_met,
            "deal_score": deal_score,                 # 1.0 / 0.5 / 0.0  (weight 0.60)
            "final_sentiment": sent["final"],         # -1..1, end of call (weight 0.25)
            "overall_sentiment": sent["overall"],     # -1..1, whole call (weight 0.15)
            "summary": summary,
            "turns": sum(1 for t in transcript if t["role"] == "agent"),
            "sentiment_turns": sent["turns"],
            "transcript": transcript,
        }
    }


if __name__ == "__main__":
    # Quick manual smoke test (hits Azure chat — costs tokens).
    demo = {
        "goal": "Book a product demo",
        "brand": "Acme Analytics",
        "product": "Realtime ops dashboards",
        "pitch": "See every order, driver and delay on one live screen.",
        "persona": "Head of Operations at a mid-size logistics firm",
        "customer": "Skeptical of new tools; burned by a slow rollout last year",
        "customer_profile": "Skeptical and time-poor; objects that they already have dashboards.",
        "max_turns": 8,
    }
    print(json.dumps(run(demo)["response"], indent=2))
