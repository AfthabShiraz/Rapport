"""
Ship an Overmind-optimized prompt back into the live agent — through the
product's normal optimization flow, so it appears in the Report/Analytics UI
and is applied with the same Accept mechanism as any in-app optimization.

Usage:
    python apply_prompt.py path/to/best_prompt.txt "short description of the change"

Creates a PENDING optimization row for the newest agent (before = current live
prompt, after = the optimized prompt). Accept it from the UI, or pass --accept
to apply immediately.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "..", "overmind.db")


def main():
    args = [a for a in sys.argv[1:] if a != "--accept"]
    accept = "--accept" in sys.argv
    if not args:
        raise SystemExit(__doc__)
    prompt_path = args[0]
    description = args[1] if len(args) > 1 else "Overmind-optimized prompt (offline run)"
    with open(prompt_path) as f:
        new_prompt = f.read().strip()

    now = datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        agent = con.execute("SELECT * FROM agents ORDER BY created_at DESC LIMIT 1").fetchone()
        if not agent:
            raise SystemExit("no agent in overmind.db")
        # the triggering call: the most recent ended one (or the agent id as placeholder)
        call = con.execute(
            "SELECT id FROM calls WHERE agent_id=? AND status='ended' ORDER BY ended_at DESC LIMIT 1",
            (agent["id"],),
        ).fetchone()
        opt_id = str(uuid.uuid4())
        diagnosis = [
            {
                "title": "Overmind optimizer result",
                "detail": description
                + " — produced by the offline optimization loop (sim_agent.py vs the eval spec), "
                "trained on scenarios rebuilt from this agent's real calls.",
                "timestamp": "",
            }
        ]
        con.execute(
            "INSERT INTO optimizations (id, agent_id, call_id, status, diagnosis_json, "
            "before_behavior, after_behavior, before_prompt, after_prompt, created_at, applied_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                opt_id,
                agent["id"],
                call["id"] if call else agent["id"],
                "accepted" if accept else "pending",
                json.dumps(diagnosis),
                "Current live prompt (see diff in UI)",
                description,
                agent["system_prompt"],
                new_prompt,
                now,
                now if accept else None,
            ),
        )
        if accept:
            con.execute(
                "UPDATE agents SET system_prompt=? WHERE id=?", (new_prompt, agent["id"])
            )
        con.commit()
        print(f"optimization {opt_id} created ({'accepted & live' if accept else 'pending — accept it in the UI'})")
    finally:
        con.close()


if __name__ == "__main__":
    main()
