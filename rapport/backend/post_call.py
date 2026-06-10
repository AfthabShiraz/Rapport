"""Post-call evaluation: scores -> Overmind evaluate -> diagnosis -> optimization.

Runs as a background task after POST /calls/:id/end. The diagnosis LLM call is
retried once, then a deterministic diagnosis built from the real call data is
used — the Report screen never hangs."""
import asyncio
import json
import logging
import statistics
from datetime import datetime

from database import SessionLocal
from models import Agent, Call, Optimization, Turn
from prompts import inject_override
from services import overmind_client
from services.openai_client import generate_diagnosis
from call_loop import OBJECTION_KEYWORDS, REFRAME_KEYWORDS, contains_any, root_spans

log = logging.getLogger("post_call")


def _rel_ts(iso_ts, started_at):
    try:
        d = (datetime.fromisoformat(iso_ts) - datetime.fromisoformat(started_at)).total_seconds()
        d = max(0, int(d))
        return f"{d // 60:02d}:{d % 60:02d}"
    except Exception:
        return "00:00"


def _compute_scores(call, turns, db):
    prospect = [t for t in turns if t.speaker == "prospect"]
    engs = []
    for t in prospect:
        if t.sentiment_json:
            try:
                engs.append(float(json.loads(t.sentiment_json)["engagement"]))
            except (KeyError, ValueError, json.JSONDecodeError):
                pass

    avg_eng = round(statistics.fmean(engs), 1) if engs else 0.0
    delta = round(engs[-1] - engs[0], 1) if len(engs) >= 2 else 0.0

    objection_handled = False
    for i, t in enumerate(turns):
        if t.speaker == "prospect" and contains_any(t.text, OBJECTION_KEYWORDS):
            if any(
                u.speaker == "agent" and contains_any(u.text, REFRAME_KEYWORDS)
                for u in turns[i + 1 :]
            ):
                objection_handled = True
            break

    recent = (
        db.query(Call)
        .filter(Call.agent_id == call.agent_id, Call.status == "ended", Call.id != call.id)
        .order_by(Call.ended_at.desc())
        .limit(5)
        .all()
    )
    recent_avgs = []
    for c in recent:
        if c.scores_json:
            try:
                recent_avgs.append(float(json.loads(c.scores_json)["avg_engagement"]))
            except (KeyError, ValueError, json.JSONDecodeError):
                pass
    vs_recent = round(avg_eng - statistics.fmean(recent_avgs), 1) if recent_avgs else 0.0

    return {
        "objection_handled": objection_handled,
        "avg_engagement": avg_eng,
        "sentiment_delta": delta,
        "outcome": call.outcome,
        "engagement_vs_recent": vs_recent,
    }


def _history_summary(call, db):
    prior = (
        db.query(Call)
        .filter(Call.agent_id == call.agent_id, Call.status == "ended", Call.id != call.id)
        .order_by(Call.ended_at.desc())
        .limit(10)
        .all()
    )
    lines = []
    for c in prior:
        turns = db.query(Turn).filter(Turn.call_id == c.id).all()
        objs = sorted(
            {
                k
                for t in turns
                if t.speaker == "prospect"
                for k in OBJECTION_KEYWORDS
                if k in t.text.lower()
            }
        )
        lines.append(
            f"- outcome={c.outcome or 'unknown'}; objections heard: {', '.join(objs) or 'none'}"
        )
    return "\n".join(lines) if lines else "(no previous calls)"


def _deterministic_diagnosis(call, turns, scores, sentiment_history):
    """Fallback built from real call data when the LLM is unavailable."""
    objection = next(
        (
            t
            for t in turns
            if t.speaker == "prospect" and contains_any(t.text, OBJECTION_KEYWORDS)
        ),
        None,
    )
    obj_ts = _rel_ts(objection.timestamp, call.started_at) if objection and call.started_at else "00:00"
    obj_text = objection.text if objection else "I need to think about it"

    first_warn = next(
        (s for s in sentiment_history if s.get("posture") == "closed" or not s.get("eye_contact")),
        None,
    )
    diagnosis = []
    if first_warn:
        diagnosis.append(
            {
                "title": "Missed objection window",
                "detail": f"Prospect signalled hesitation from {first_warn['t']} "
                f"({first_warn.get('posture', 'closed')} posture, engagement "
                f"{first_warn.get('engagement', '?')}/10) before voicing the objection. "
                "Intervention window unused.",
                "timestamp": first_warn["t"],
            }
        )
    diagnosis.append(
        {
            "title": 'Generic deflection on "' + obj_text[:40] + '"',
            "detail": "Agent released the prospect passively — no scarcity, social proof, "
            "or next-step anchoring after the objection.",
            "timestamp": obj_ts,
        }
    )
    diagnosis.append(
        {
            "title": "No reframe attempted",
            "detail": "This objection set converts better with a local installation-urgency "
            "reframe; the agent didn't try it.",
            "timestamp": obj_ts,
        }
    )
    return {
        "diagnosis": diagnosis[:3],
        "before_behavior": "When the prospect defers ('"
        + obj_text[:60]
        + "'), the agent accepts it and ends the call politely with no counter.",
        "after_behavior": "When the prospect defers or says they need to think about it, do NOT "
        "release the call. Acknowledge the concern in one sentence, then reframe with social "
        "proof (e.g. '3 neighbours on your street got quotes last month') and scarcity "
        "(installation slots are filling up), and offer a low-friction next step: a "
        "no-obligation survey booking this week. Always anchor a concrete next action.",
    }


async def run_post_call_evaluation(call_id: str):
    try:
        await _run(call_id)
    except Exception:
        log.exception("post-call evaluation failed for %s", call_id)


async def _run(call_id: str):
    db = SessionLocal()
    try:
        call = db.get(Call, call_id)
        if not call:
            return
        agent = db.get(Agent, call.agent_id)
        turns = (
            db.query(Turn).filter(Turn.call_id == call_id).order_by(Turn.turn_number).all()
        )

        # 1-2. scores -> persist -> overmind evaluate -> end root span
        scores = _compute_scores(call, turns, db)
        call.scores_json = json.dumps(scores)
        db.commit()

        engs = [
            json.loads(t.sentiment_json)["engagement"]
            for t in turns
            if t.speaker == "prospect" and t.sentiment_json
        ]
        overmind_client.evaluate(
            call.overmind_trace_id,
            {
                "objection_handled": 1 if scores["objection_handled"] else 0,
                "prospect_engagement_end": engs[-1] if engs else 0,
                "call_outcome": 1 if call.outcome == "converted" else 0,
                "sentiment_delta": scores["sentiment_delta"],
            },
        )
        span = root_spans.pop(call_id, None)
        if span is not None:
            overmind_client.end_span(span)

        # 3. diagnosis (LLM, retry once, deterministic fallback)
        from call_loop import sessions

        session = sessions.get(call_id)
        sentiment_history = session.sentiment_history if session else []
        transcript = "\n".join(
            f"[{_rel_ts(t.timestamp, call.started_at or t.timestamp)}] "
            f"{'Agent' if t.speaker == 'agent' else call.prospect_name}: {t.text}"
            for t in turns
        )
        payload = (
            f"TRANSCRIPT:\n{transcript or '(empty)'}\n\n"
            f"SENTIMENT TIMELINE:\n{json.dumps(sentiment_history) or '[]'}\n\n"
            f"SCORES:\n{json.dumps(scores)}\n\n"
            f"PRIOR CALL HISTORY (this agent):\n{_history_summary(call, db)}"
        )
        result = await generate_diagnosis(payload)
        if result is None:
            result = await generate_diagnosis(payload)
        if result is None:
            result = _deterministic_diagnosis(call, turns, scores, sentiment_history)

        # 4. insert optimization
        opt = Optimization(
            agent_id=call.agent_id,
            call_id=call_id,
            status="pending",
            diagnosis_json=json.dumps(result["diagnosis"]),
            before_behavior=result["before_behavior"],
            after_behavior=result["after_behavior"],
            before_prompt=agent.system_prompt,
            after_prompt=inject_override(agent.system_prompt, result["after_behavior"]),
        )
        db.add(opt)
        db.commit()
        log.info("optimization %s created for call %s", opt.id, call_id)

        # 5. engagement lift for previously-accepted optimizations
        _update_lifts(call.agent_id, db)
    finally:
        db.close()


def _update_lifts(agent_id: str, db):
    accepted = (
        db.query(Optimization)
        .filter(Optimization.agent_id == agent_id, Optimization.status == "accepted")
        .all()
    )
    calls = (
        db.query(Call)
        .filter(Call.agent_id == agent_id, Call.status == "ended")
        .order_by(Call.started_at)
        .all()
    )

    def avg_eng(call_list):
        vals = []
        for c in call_list:
            if c.scores_json:
                try:
                    vals.append(float(json.loads(c.scores_json)["avg_engagement"]))
                except (KeyError, ValueError, json.JSONDecodeError):
                    pass
        return statistics.fmean(vals) if vals else None

    for opt in accepted:
        if not opt.applied_at:
            continue
        before = avg_eng([c for c in calls if (c.started_at or "") < opt.applied_at])
        after = avg_eng([c for c in calls if (c.started_at or "") >= opt.applied_at])
        if before is not None and after is not None:
            opt.engagement_lift = f"{after - before:+.1f} engagement"
    db.commit()
