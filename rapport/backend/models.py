"""SQLAlchemy models — agents, calls, turns, optimizations."""
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, Text

from database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Agent(Base):
    __tablename__ = "agents"

    id = Column(Text, primary_key=True, default=new_id)
    brand_name = Column(Text, nullable=False)
    product = Column(Text, nullable=False)
    elevator_pitch = Column(Text, nullable=False)
    target_persona = Column(Text, nullable=False)
    objections = Column(Text, nullable=False)  # JSON array of strings
    system_prompt = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False, default=now_iso)

    def to_dict(self):
        return {
            "id": self.id,
            "brand_name": self.brand_name,
            "product": self.product,
            "elevator_pitch": self.elevator_pitch,
            "target_persona": self.target_persona,
            "objections": json.loads(self.objections or "[]"),
            "system_prompt": self.system_prompt,
            "created_at": self.created_at,
        }


class Call(Base):
    __tablename__ = "calls"

    id = Column(Text, primary_key=True, default=new_id)
    agent_id = Column(Text, nullable=False)
    prospect_name = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="pending")  # pending | live | ended
    optimized = Column(Integer, nullable=False, default=0)
    started_at = Column(Text)
    ended_at = Column(Text)
    outcome = Column(Text)  # converted | follow_up | lost
    scores_json = Column(Text)
    overmind_trace_id = Column(Text)

    def to_dict(self):
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "prospect_name": self.prospect_name,
            "status": self.status,
            "optimized": bool(self.optimized),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "outcome": self.outcome,
            "scores_json": json.loads(self.scores_json) if self.scores_json else None,
            "overmind_trace_id": self.overmind_trace_id,
        }


class Turn(Base):
    __tablename__ = "turns"

    id = Column(Text, primary_key=True, default=new_id)
    call_id = Column(Text, nullable=False)
    turn_number = Column(Integer, nullable=False)
    speaker = Column(Text, nullable=False)  # agent | prospect
    text = Column(Text, nullable=False)
    sentiment_json = Column(Text)
    timestamp = Column(Text, nullable=False, default=now_iso)

    def to_dict(self):
        return {
            "id": self.id,
            "call_id": self.call_id,
            "turn_number": self.turn_number,
            "speaker": self.speaker,
            "text": self.text,
            "sentiment_json": json.loads(self.sentiment_json) if self.sentiment_json else None,
            "timestamp": self.timestamp,
        }


class Optimization(Base):
    __tablename__ = "optimizations"

    id = Column(Text, primary_key=True, default=new_id)
    agent_id = Column(Text, nullable=False)
    call_id = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="pending")  # pending | accepted | rejected
    diagnosis_json = Column(Text, nullable=False)
    before_behavior = Column(Text, nullable=False)
    after_behavior = Column(Text, nullable=False)
    before_prompt = Column(Text, nullable=False)
    after_prompt = Column(Text, nullable=False)
    engagement_lift = Column(Text)
    created_at = Column(Text, nullable=False, default=now_iso)
    applied_at = Column(Text)

    def to_dict(self):
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "call_id": self.call_id,
            "status": self.status,
            "diagnosis_json": json.loads(self.diagnosis_json or "[]"),
            "before_behavior": self.before_behavior,
            "after_behavior": self.after_behavior,
            "before_prompt": self.before_prompt,
            "after_prompt": self.after_prompt,
            "engagement_lift": self.engagement_lift,
            "created_at": self.created_at,
            "applied_at": self.applied_at,
        }
