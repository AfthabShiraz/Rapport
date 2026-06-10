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


def _fuse_valence(face, voice, engagement):
    """Same fusion as the live session: face/voice valence + vision engagement."""
    parts = [v for v in (face, voice) if v is not None]
    if engagement is not None:
        parts.append(engagement / 10 * 2 - 1)  # 0..10 -> -1..1
    return round(statistics.fmean(parts), 2) if parts else None


def _segments_from_turns(turns, started_at):
    """Reconstruct per-agent-line segments + the prospect's MEASURED reaction
    straight from persisted turns, so the diagnosis has the face/voice/body-language
    signal even when the live session is already gone (the common case)."""
    segments = []
    last_agent = None
    for t in turns:
        if t.speaker == "agent":
            last_agent = t
        elif t.speaker == "prospect" and last_agent is not None and t.sentiment_json:
            try:
                s = json.loads(t.sentiment_json)
            except json.JSONDecodeError:
                continue
            face, voice, eng = s.get("face_valence"), s.get("voice_valence"), s.get("engagement")
            segments.append(
                {
                    "t": _rel_ts(t.timestamp, started_at or t.timestamp),
                    "agent_line": last_agent.text,
                    "prospect_response": t.text,
                    "face_reaction": face,
                    "voice_reaction": voice,
                    "engagement": eng,
                    "emotion": s.get("emotion"),
                    "posture": s.get("posture"),
                    "eye_contact": s.get("eye_contact"),
                    "objection_risk": s.get("objection_risk"),
                    "fused_valence": _fuse_valence(face, voice, eng),
                }
            )
    return segments


def _timeline_from_turns(turns, started_at):
    """Vision/affect timeline rebuilt from persisted prospect-turn sentiment."""
    out = []
    for t in turns:
        if t.speaker == "prospect" and t.sentiment_json:
            try:
                s = json.loads(t.sentiment_json)
            except json.JSONDecodeError:
                continue
            out.append({"t": _rel_ts(t.timestamp, started_at or t.timestamp), **s})
    return out


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


def _deterministic_diagnosis(call, turns, scores, sentiment_history, script_feedback=None):
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

    # the script lines that drew the most negative / most positive MEASURED reaction
    graded = [s for s in (script_feedback or []) if s.get("fused_valence") is not None]
    worst = min(graded, key=lambda s: s["fused_valence"]) if graded else None
    best = max(graded, key=lambda s: s["fused_valence"]) if graded else None
    if worst and worst["fused_valence"] <= 0:
        diagnosis.append(
            {
                "title": "Weakest script moment (measured reaction)",
                "detail": f'The line "{worst["agent_line"][:50]}" drew the prospect\'s most '
                f"negative measured reaction (face {worst['face_reaction']}, voice "
                f"{worst['voice_reaction']}, fused {worst['fused_valence']}) in the "
                f"{worst['stage']} stage. This part of the script underperforms and needs reworking.",
                "timestamp": worst["t"],
            }
        )

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
    after = (
        "When the prospect defers or says they need to think about it, do NOT "
        "release the call. Acknowledge the concern in one sentence, then reframe with social "
        "proof (e.g. '3 neighbours on your street got quotes last month') and scarcity "
        "(installation slots are filling up), and offer a low-friction next step: a "
        "no-obligation survey booking this week. Always anchor a concrete next action."
    )
    # sentiment-driven steering: lean into what lifted the prospect, drop what cooled them
    if best and best["fused_valence"] > 0:
        after += (
            f" Lead with the kind of point you made at {best['t']} (\"{best['agent_line'][:50]}\") "
            "— it measurably lifted the prospect; bring topics like this up earlier and more often."
        )
    if worst and worst["fused_valence"] <= 0:
        after += (
            f" Steer away from how you handled {worst['t']} (\"{worst['agent_line'][:50]}\") "
            "— it measurably cooled the prospect; rework or drop that line."
        )
    return {
        "diagnosis": diagnosis[:3],
        "before_behavior": "When the prospect defers ('"
        + obj_text[:60]
        + "'), the agent accepts it and ends the call politely with no counter.",
        "after_behavior": after,
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

        # the live session holds the measured multimodal signals: the vision
        # timeline plus per-agent-line facial/vocal reactions (script_feedback)
        from call_loop import sessions

        session = sessions.get(call_id)
        # prefer the live session's window-averaged signals; otherwise rebuild from
        # the DB (the session is usually already gone by the time this task runs)
        sentiment_history = (session.sentiment_history if session else None) or _timeline_from_turns(
            turns, call.started_at
        )
        script_feedback = (session.script_feedback if session else None) or _segments_from_turns(
            turns, call.started_at
        )
        fused_vals = [s["fused_valence"] for s in script_feedback if s.get("fused_valence") is not None]
        face_vals = [s["face_reaction"] for s in script_feedback if s.get("face_reaction") is not None]
        voice_vals = [s["voice_reaction"] for s in script_feedback if s.get("voice_reaction") is not None]

        # 1-2. scores -> persist -> overmind evaluate -> end root span
        scores = _compute_scores(call, turns, db)
        scores["avg_valence"] = round(statistics.fmean(fused_vals), 2) if fused_vals else 0.0
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
                # measured-from-face/voice affect, averaged across the script
                "avg_prospect_valence": round(statistics.fmean(fused_vals), 2) if fused_vals else 0,
                "avg_face_valence": round(statistics.fmean(face_vals), 2) if face_vals else 0,
                "avg_voice_valence": round(statistics.fmean(voice_vals), 2) if voice_vals else 0,
            },
        )
        span = root_spans.pop(call_id, None)
        if span is not None:
            overmind_client.end_span(span)

        # 3. diagnosis (LLM, retry once, deterministic fallback)
        transcript = "\n".join(
            f"[{_rel_ts(t.timestamp, call.started_at or t.timestamp)}] "
            f"{'Agent' if t.speaker == 'agent' else call.prospect_name}: {t.text}"
            for t in turns
        )
        payload = (
            f"TRANSCRIPT:\n{transcript or '(empty)'}\n\n"
            f"SENTIMENT TIMELINE (vision):\n{json.dumps(sentiment_history) or '[]'}\n\n"
            "SCRIPT SEGMENT PERFORMANCE — each agent line paired with the prospect's "
            "MEASURED facial (face_reaction) and vocal (voice_reaction) valence in [-1,1] "
            f"and a fused_valence; this is how each part of the script actually landed:\n"
            f"{json.dumps(script_feedback) or '[]'}\n\n"
            f"SCORES:\n{json.dumps(scores)}\n\n"
            f"PRIOR CALL HISTORY (this agent):\n{_history_summary(call, db)}"
        )
        result = await generate_diagnosis(payload)
        if result is None:
            result = await generate_diagnosis(payload)
        if result is None:
            result = _deterministic_diagnosis(call, turns, scores, sentiment_history, script_feedback)

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
