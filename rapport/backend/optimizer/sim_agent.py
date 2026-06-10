"""
Rapport — Overmind optimizer entrypoint (adapted from the root prototype's
sales_agent.py to rapport's DB-backed agents).

run(input_data) -> dict simulates a full sales call as text: our agent (the
active agent's system prompt from overmind.db, or input_data["system_prompt"])
versus a simulated prospect (a chat model role-playing the persona seeded with
the objections that tanked real calls). Returns outcome + sentiment + transcript
so Overmind can score it against eval_spec.json / policies.md.

A live mic call can't sit inside an optimization loop — this is its stand-in.
The optimizable artifact is the agent's system prompt; ship a winning prompt
back into the live agent with apply_prompt.py (it flows through the normal
optimization-accept mechanism, so it shows up in the UI like any other).

Register with:  /overmind-register-agent sim_agent.py   (entrypoint sim_agent:run)
"""
from __future__ import annotations

import json
import os
import sqlite3
from urllib.parse import urlsplit

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "..", "overmind.db")

END_CALL_CHAT_TOOL = {
    "type": "function",
    "function": {
        "name": "end_call",
        "description": (
            "End the phone call. Call this only AFTER speaking a closing line, when the "
            "deal is closed, a clear next step is agreed, or the prospect wants to stop."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "outcome": {
                    "type": "string",
                    "enum": ["deal_closed", "next_step_agreed", "declined", "customer_ended", "other"],
                },
                "summary": {"type": "string"},
            },
            "required": ["outcome"],
        },
    },
}


def _load_env():
    """Minimal .env loader (repo root + backend) so this file has no dotenv dep."""
    for p in (os.path.join(HERE, "..", ".env"), os.path.join(HERE, "..", "..", "..", ".env")):
        try:
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        os.environ.setdefault(k.strip(), v.strip())
        except OSError:
            pass


def _chat(messages, tools=None, response_format=None, max_tokens=600):
    """One chat completion against OpenAI (OPENAI_API_KEY) or Azure (AZURE_* envs)."""
    _load_env()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {key}"}
        model = os.environ.get("CHAT_MODEL", "gpt-4o")
        body = {"model": model, "messages": messages, "max_tokens": max_tokens}
    else:
        endpoint = os.environ.get("AZURE_REALTIME_ENDPOINT", "").strip()
        akey = os.environ.get("AZURE_REALTIME_API_KEY", "").strip()
        if not endpoint or not akey:
            raise RuntimeError("no OPENAI_API_KEY or AZURE_* credentials configured")
        p = urlsplit(endpoint)
        dep = os.environ.get("AZURE_CHAT_DEPLOYMENT", "gpt-4o")
        ver = os.environ.get("AZURE_CHAT_API_VERSION", "2024-10-21")
        url = f"{p.scheme}://{p.netloc}/openai/deployments/{dep}/chat/completions?api-version={ver}"
        headers = {"api-key": akey}
        body = {"messages": messages, "max_completion_tokens": max_tokens}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if response_format:
        body["response_format"] = response_format
    resp = requests.post(url, headers={**headers, "Content-Type": "application/json"}, json=body, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"chat failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]


def _active_prompt() -> str:
    """Newest agent's live system prompt from rapport's DB."""
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            "SELECT system_prompt FROM agents ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    if not row:
        raise RuntimeError("no agent in overmind.db — create one in the UI first")
    return row[0]


def _customer_prompt(sc: dict) -> str:
    profile = (sc.get("customer_profile") or "").strip() or (
        "Cautious but reachable. Raise one realistic objection before considering anything."
    )
    return (
        "You are role-playing a prospective customer receiving an outbound sales call. "
        f"You match this persona: {sc.get('persona') or 'a busy homeowner'}. "
        f"Context about you: {sc.get('customer') or 'no prior relationship with the brand'}.\n"
        f"YOUR DISPOSITION ON THIS CALL: {profile}\n"
        "Behave like a real human on a cold call: be a little guarded, interrupt the pitch "
        "with realistic objections, and only agree to buy or to a concrete next step if the "
        "rep genuinely earns it — otherwise stay noncommittal or decline. Keep replies short "
        "and conversational (1-3 sentences). Never break character or mention you are an AI."
    )


def _score_sentiment(sc: dict, transcript: list[dict]) -> dict:
    lines = [f"[{i}] {t['role']}: {t['text']}" for i, t in enumerate(transcript)]
    try:
        msg = _chat(
            [
                {
                    "role": "system",
                    "content": "You are a sentiment analyst for sales calls. Score the CUSTOMER's "
                    "sentiment only (role 'user'). Be precise.",
                },
                {
                    "role": "user",
                    "content": "\n".join(lines)
                    + '\n\nReturn ONLY JSON: {"overall": <float -1..1>, "final": <float -1..1, '
                    'sentiment by the END of the call>}',
                },
            ],
            response_format={"type": "json_object"},
            max_tokens=200,
        )
        data = json.loads(msg.get("content") or "{}")
        clamp = lambda x: max(-1.0, min(1.0, float(x)))  # noqa: E731
        return {
            "overall": clamp(data["overall"]) if data.get("overall") is not None else None,
            "final": clamp(data["final"]) if data.get("final") is not None else None,
        }
    except Exception:
        return {"overall": None, "final": None}


def run(input_data: dict) -> dict:
    """Overmind entrypoint: simulate one call for one scenario."""
    sc = input_data or {}
    max_turns = int(sc.get("max_turns", 10))
    system_prompt = sc.get("system_prompt") or _active_prompt()
    prospect = sc.get("prospect_name", "James")

    agent_msgs = [
        {"role": "system", "content": system_prompt},
        {
            "role": "system",
            "content": f"The call is starting now. The prospect's name is {prospect}. "
            "Begin with the intro stage — greet them and speak first.",
        },
    ]
    customer_sys = _customer_prompt(sc)
    transcript: list[dict] = []
    outcome, summary = None, ""

    for _ in range(max_turns):
        am = _chat(agent_msgs, tools=[END_CALL_CHAT_TOOL])
        text = (am.get("content") or "").strip()
        if text:
            transcript.append({"role": "agent", "text": text})
            agent_msgs.append({"role": "assistant", "content": text})
        for tc in am.get("tool_calls") or []:
            if tc.get("function", {}).get("name") == "end_call":
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                outcome, summary = args.get("outcome", "other"), args.get("summary", "")
        if outcome:
            break

        customer_view = [
            {"role": "user" if t["role"] == "agent" else "assistant", "content": t["text"]}
            for t in transcript
        ]
        cm = _chat([{"role": "system", "content": customer_sys}] + customer_view)
        ctext = (cm.get("content") or "").strip()
        transcript.append({"role": "user", "text": ctext})
        agent_msgs.append({"role": "user", "content": ctext})

    outcome = outcome or "incomplete"
    deal_score = 1.0 if outcome == "deal_closed" else (0.5 if outcome == "next_step_agreed" else 0.0)
    sent = _score_sentiment(sc, transcript)

    return {
        "response": {
            "outcome": outcome,
            "objective_met": outcome in ("deal_closed", "next_step_agreed"),
            "deal_score": deal_score,
            "final_sentiment": sent["final"],
            "overall_sentiment": sent["overall"],
            "summary": summary,
            "turns": sum(1 for t in transcript if t["role"] == "agent"),
            "transcript": transcript,
        }
    }


if __name__ == "__main__":
    demo = {
        "prospect_name": "James",
        "persona": "UK homeowner, 35-60, rising energy bills",
        "customer": "Got two quotes already; worried about upfront cost",
        "customer_profile": 'Skeptical; will say "I need to think about it" before committing.',
        "max_turns": 8,
    }
    print(json.dumps(run(demo)["response"], indent=2))
