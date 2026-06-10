"""Rapport — Overmind Sales Agent backend.

Run: uvicorn main:app --reload --port 8000
"""
import asyncio
import json
import logging
import os
import statistics
from datetime import datetime

from dotenv import load_dotenv

# backend/.env first, then repo-root .env as fallback (team keys may live there)
_HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_HERE, ".env"))
load_dotenv(os.path.join(_HERE, "..", "..", ".env"))

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from database import SessionLocal, init_db
from models import Agent, Call, Optimization, Turn, now_iso
from prompts import build_system_prompt, extract_overrides
from seed import seeded_analytics
from services import overmind_client
from services.openai_client import classify_outcome
from call_loop import CallSession, root_spans, sessions
from post_call import run_post_call_evaluation

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

app = FastAPI(title="Rapport — Overmind Sales Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()
    overmind_client.init()


@app.get("/health")
def health():
    from services.realtime import RealtimeSession
    from services.openai_client import client as chat_client

    return {
        "ok": True,
        "realtime_configured": RealtimeSession.available(),
        "chat_configured": chat_client is not None,
    }


# --------------------------------------------------------------------- agents


class AgentIn(BaseModel):
    brand_name: str
    product: str
    elevator_pitch: str
    target_persona: str
    objections: list[str] = Field(default_factory=list)


class AgentPatch(BaseModel):
    brand_name: str | None = None
    product: str | None = None
    elevator_pitch: str | None = None
    target_persona: str | None = None
    objections: list[str] | None = None
    system_prompt: str | None = None


@app.post("/agents", status_code=201)
def create_agent(body: AgentIn):
    db = SessionLocal()
    try:
        agent = Agent(
            brand_name=body.brand_name,
            product=body.product,
            elevator_pitch=body.elevator_pitch,
            target_persona=body.target_persona,
            objections=json.dumps(body.objections),
            system_prompt=build_system_prompt(
                body.brand_name,
                body.product,
                body.elevator_pitch,
                body.target_persona,
                body.objections,
            ),
        )
        db.add(agent)
        db.commit()
        return agent.to_dict()
    finally:
        db.close()


@app.get("/agents")
def list_agents():
    db = SessionLocal()
    try:
        rows = db.query(Agent).order_by(Agent.created_at.desc()).all()
        return [a.to_dict() for a in rows]
    finally:
        db.close()


@app.get("/agents/{agent_id}")
def get_agent(agent_id: str):
    db = SessionLocal()
    try:
        agent = db.get(Agent, agent_id)
        if not agent:
            raise HTTPException(404, "agent not found")
        return agent.to_dict()
    finally:
        db.close()


@app.patch("/agents/{agent_id}")
def patch_agent(agent_id: str, body: AgentPatch):
    db = SessionLocal()
    try:
        agent = db.get(Agent, agent_id)
        if not agent:
            raise HTTPException(404, "agent not found")

        fields_changed = False
        for f in ("brand_name", "product", "elevator_pitch", "target_persona"):
            v = getattr(body, f)
            if v is not None:
                setattr(agent, f, v)
                fields_changed = True
        if body.objections is not None:
            agent.objections = json.dumps(body.objections)
            fields_changed = True

        if body.system_prompt is not None:
            agent.system_prompt = body.system_prompt
        elif fields_changed:
            # regenerate the base prompt, preserving accepted override blocks
            overrides = extract_overrides(agent.system_prompt)
            base = build_system_prompt(
                agent.brand_name,
                agent.product,
                agent.elevator_pitch,
                agent.target_persona,
                json.loads(agent.objections),
            )
            agent.system_prompt = f"{base}\n\n{overrides}".strip() if overrides else base

        db.commit()
        return agent.to_dict()
    finally:
        db.close()


# ---------------------------------------------------------------------- calls


class CallIn(BaseModel):
    agent_id: str
    prospect_name: str


class CallEnd(BaseModel):
    outcome: str | None = None


@app.post("/calls", status_code=201)
def create_call(body: CallIn):
    db = SessionLocal()
    try:
        if not db.get(Agent, body.agent_id):
            raise HTTPException(404, "agent not found")
        has_accepted = (
            db.query(Optimization)
            .filter(Optimization.agent_id == body.agent_id, Optimization.status == "accepted")
            .count()
            > 0
        )
        call = Call(
            agent_id=body.agent_id,
            prospect_name=body.prospect_name,
            status="pending",
            optimized=1 if has_accepted else 0,
        )
        db.add(call)
        db.commit()
        return call.to_dict()
    finally:
        db.close()


@app.get("/calls")
def list_calls(agentId: str):
    db = SessionLocal()
    try:
        rows = (
            db.query(Call)
            .filter(Call.agent_id == agentId)
            .order_by(Call.started_at.desc())
            .all()
        )
        return [c.to_dict() for c in rows]
    finally:
        db.close()


@app.get("/calls/{call_id}")
def get_call(call_id: str):
    db = SessionLocal()
    try:
        call = db.get(Call, call_id)
        if not call:
            raise HTTPException(404, "call not found")
        turns = (
            db.query(Turn).filter(Turn.call_id == call_id).order_by(Turn.turn_number).all()
        )
        return {"call": call.to_dict(), "turns": [t.to_dict() for t in turns]}
    finally:
        db.close()


@app.post("/calls/{call_id}/start")
async def start_call(call_id: str):
    db = SessionLocal()
    try:
        call = db.get(Call, call_id)
        if not call:
            raise HTTPException(404, "call not found")
        if call.status == "pending":
            call.status = "live"
            call.started_at = now_iso()
            span, trace_id = overmind_client.start_call_span(call.agent_id)
            root_spans[call_id] = span
            call.overmind_trace_id = trace_id
            db.commit()
        result = call.to_dict()
    finally:
        db.close()

    session = sessions.get(call_id)
    if session:
        await session.emit({"type": "call_started", "started_at": result["started_at"]})
        await session.maybe_greet()
    return result


@app.post("/calls/{call_id}/end")
async def end_call(call_id: str, body: CallEnd | None = None):
    db = SessionLocal()
    try:
        call = db.get(Call, call_id)
        if not call:
            raise HTTPException(404, "call not found")
        if call.status != "ended":
            call.status = "ended"
            call.ended_at = now_iso()
            outcome = body.outcome if body else None
            if outcome in ("converted", "follow_up", "lost"):
                call.outcome = outcome
            else:
                turns = (
                    db.query(Turn)
                    .filter(Turn.call_id == call_id)
                    .order_by(Turn.turn_number)
                    .all()
                )
                transcript = "\n".join(f"{t.speaker}: {t.text}" for t in turns)
                call.outcome = await classify_outcome(transcript)
            db.commit()
            asyncio.create_task(run_post_call_evaluation(call_id))
        return call.to_dict()
    finally:
        db.close()


@app.get("/calls/{call_id}/optimization")
def call_optimization(call_id: str):
    db = SessionLocal()
    try:
        opt = (
            db.query(Optimization)
            .filter(Optimization.call_id == call_id)
            .order_by(Optimization.created_at.desc())
            .first()
        )
        if not opt:
            return Response(status_code=204)
        return opt.to_dict()
    finally:
        db.close()


# -------------------------------------------------------------- optimizations


@app.get("/optimizations")
def list_optimizations(agentId: str):
    db = SessionLocal()
    try:
        rows = (
            db.query(Optimization)
            .filter(Optimization.agent_id == agentId)
            .order_by(Optimization.created_at.desc())
            .all()
        )
        return [o.to_dict() for o in rows]
    finally:
        db.close()


@app.get("/optimizations/{opt_id}")
def get_optimization(opt_id: str):
    db = SessionLocal()
    try:
        opt = db.get(Optimization, opt_id)
        if not opt:
            raise HTTPException(404, "optimization not found")
        return opt.to_dict()
    finally:
        db.close()


@app.post("/optimizations/{opt_id}/accept")
def accept_optimization(opt_id: str):
    db = SessionLocal()
    try:
        opt = db.get(Optimization, opt_id)
        if not opt:
            raise HTTPException(404, "optimization not found")
        opt.status = "accepted"
        opt.applied_at = now_iso()
        agent = db.get(Agent, opt.agent_id)
        if agent:
            agent.system_prompt = opt.after_prompt
        db.commit()
        return opt.to_dict()
    finally:
        db.close()


@app.post("/optimizations/{opt_id}/reject")
def reject_optimization(opt_id: str):
    db = SessionLocal()
    try:
        opt = db.get(Optimization, opt_id)
        if not opt:
            raise HTTPException(404, "optimization not found")
        opt.status = "rejected"
        db.commit()
        return opt.to_dict()
    finally:
        db.close()


# ------------------------------------------------------------------ analytics


@app.get("/analytics/{agent_id}")
def analytics(agent_id: str):
    db = SessionLocal()
    try:
        calls = (
            db.query(Call)
            .filter(Call.agent_id == agent_id, Call.status == "ended")
            .order_by(Call.started_at)
            .all()
        )
        if len(calls) < 5:
            return seeded_analytics()

        per_eng, per_conv, durations = [], [], []
        handled, with_scores = 0, 0
        for c in calls:
            scores = json.loads(c.scores_json) if c.scores_json else {}
            per_eng.append(scores.get("avg_engagement", 0))
            per_conv.append(c.outcome == "converted")
            if scores:
                with_scores += 1
                if scores.get("objection_handled"):
                    handled += 1
            if c.started_at and c.ended_at:
                durations.append(
                    (
                        datetime.fromisoformat(c.ended_at)
                        - datetime.fromisoformat(c.started_at)
                    ).total_seconds()
                )

        call_index = {c.id: i for i, c in enumerate(calls)}
        accepted = (
            db.query(Optimization)
            .filter(Optimization.agent_id == agent_id, Optimization.status == "accepted")
            .all()
        )
        opt_indices = sorted({call_index[o.call_id] for o in accepted if o.call_id in call_index})

        return {
            "total_calls": len(calls),
            "conversion_rate": round(sum(per_conv) / len(calls), 2),
            "avg_engagement": round(statistics.fmean(per_eng), 1) if per_eng else 0,
            "avg_duration_seconds": int(statistics.fmean(durations)) if durations else 0,
            "objection_handle_rate": round(handled / with_scores, 2) if with_scores else 0,
            "per_call_engagement": per_eng,
            "per_call_converted": per_conv,
            "optimization_call_indices": opt_indices,
            "optimizations_applied": len(accepted),
            "seeded": False,
        }
    finally:
        db.close()


# ------------------------------------------------------------------ websocket


@app.websocket("/ws/calls/{call_id}")
async def ws_call(ws: WebSocket, call_id: str):
    await ws.accept()
    db = SessionLocal()
    try:
        call = db.get(Call, call_id)
        agent = db.get(Agent, call.agent_id) if call else None
    finally:
        db.close()
    if not call or not agent or call.status == "ended":
        await ws.close(code=4404)
        return

    # a refresh replaces any stale session for the same call
    old = sessions.get(call_id)
    if old:
        old.closing = True
        try:
            await old.ws.close()
        except Exception:
            pass

    session = CallSession(ws, call, agent)
    sessions[call_id] = session
    await session.run()
