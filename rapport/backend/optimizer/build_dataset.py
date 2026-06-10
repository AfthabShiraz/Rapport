"""
Build the Overmind optimization dataset from rapport's real calls (overmind.db).

Each ENDED call becomes a scenario: the simulated customer is seeded with the
actual objections the prospect raised on that call, and hard calls (lost ones)
are the test cases. Output: dataset.json next to this file.

Run after every few real calls:  python build_dataset.py
"""
from __future__ import annotations

import json
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "..", "overmind.db")
OUT = os.path.join(HERE, "dataset.json")

OBJECTION_KEYWORDS = [
    "think about it", "not sure", "maybe", "too expensive", "need some time",
    "get back to you", "speak to my wife", "speak to my husband", "already got quotes",
    "need to think", "talk it over", "bit much", "can't afford",
]


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        agent = con.execute("SELECT * FROM agents ORDER BY created_at DESC LIMIT 1").fetchone()
        if not agent:
            raise SystemExit("no agent in overmind.db — create one in the UI first")
        calls = con.execute(
            "SELECT * FROM calls WHERE agent_id=? AND status='ended' ORDER BY started_at",
            (agent["id"],),
        ).fetchall()

        cases = []
        for c in calls:
            turns = con.execute(
                "SELECT speaker, text FROM turns WHERE call_id=? ORDER BY turn_number",
                (c["id"],),
            ).fetchall()
            objections = sorted(
                {
                    k
                    for t in turns
                    if t["speaker"] == "prospect"
                    for k in OBJECTION_KEYWORDS
                    if k in t["text"].lower()
                }
            )
            profile = (
                "Replays a real call that was "
                + (c["outcome"] or "unresolved")
                + ". You WILL raise these objections before considering anything: "
                + (", ".join(f'"{o}"' for o in objections) or '"I need to think about it"')
                + ". Only commit if the rep genuinely handles them with specifics."
            )
            cases.append(
                {
                    "input": {
                        "prospect_name": c["prospect_name"],
                        "persona": agent["target_persona"],
                        "customer": f"A prospect of {agent['brand_name']} ({agent['product']})",
                        "customer_profile": profile,
                        "max_turns": 10,
                    },
                    "expected_output": {
                        "objective_met": True,
                        "note": "the agent should close or anchor a concrete next step",
                    },
                    "meta": {
                        "source_call_id": c["id"],
                        "real_outcome": c["outcome"],
                        "objections": objections,
                    },
                }
            )
    finally:
        con.close()

    with open(OUT, "w") as f:
        json.dump({"cases": cases}, f, indent=2)
    print(f"wrote {len(cases)} scenario(s) from real calls -> {OUT}")


if __name__ == "__main__":
    main()
