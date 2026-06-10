"""
Turn real saved calls into an Overmind dataset.

Each transcript in ../transcripts/*.json becomes one scenario: the campaign brief
plus a `customer_profile` that makes the simulated customer replay that call's hard
moments (mined from analysis.py output if present, else from the raw sentiment
track). `expected_output.outcome` is the objective we want the optimized agent to
hit — so Overmind optimizes toward succeeding on the calls that were hardest.

Run:  python overmind/build_dataset.py
Out:  overmind/dataset.json   (+ a human-readable overmind/scenarios_meta.json)
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS = ROOT / "transcripts"
OUT = Path(__file__).resolve().parent / "dataset.json"
META = Path(__file__).resolve().parent / "scenarios_meta.json"


def desired_outcome(goal: str) -> str:
    g = (goal or "").lower()
    if any(k in g for k in ("demo", "appointment", "meeting", "trial", "follow")):
        return "next_step_agreed"
    return "deal_closed"


def customer_profile(t: dict) -> str:
    """Describe how the simulated customer should behave, from what really happened."""
    analysis = t.get("analysis") or {}
    wrong = analysis.get("what_went_wrong") or []
    moments = analysis.get("key_moments") or []
    overall = (analysis.get("overall") or {})

    bits: list[str] = []
    # Disposition from the overall sentiment of the real call.
    score = overall.get("score")
    if score is None:
        vals = [s.get("valence", 0) for s in t.get("sentiment", [])]
        score = min(vals) if vals else 0
    bits.append("Skeptical and hard to win over." if score < -0.2 else "Cautious but reachable.")

    # Replay the specific objections that tanked the real call.
    triggers = [m.get("trigger") for m in moments if m.get("trigger")][:2]
    if triggers:
        bits.append("Push back around: " + "; ".join(triggers) + ".")
    elif wrong:
        bits.append("Raise concerns like: " + "; ".join(wrong[:2]) + ".")
    else:
        # Fall back to the customer's most negative line.
        users = [e for e in t.get("entries", []) if e.get("role") == "user" and e.get("text")]
        ls = [e for e in users if isinstance(e.get("language_sentiment"), dict)]
        worst = min(ls, key=lambda e: e["language_sentiment"].get("score", 0), default=None) \
            or (users[0] if users else None)
        if worst:
            bits.append(f'Object early with something like: "{worst["text"]}"')

    bits.append("Only commit if the rep genuinely earns it.")
    return " ".join(bits)


def main() -> None:
    files = sorted(glob.glob(str(TRANSCRIPTS / "*.json")))
    records, meta = [], []
    for f in files:
        t = json.loads(Path(f).read_text())
        c = t.get("campaign", {})
        if not (c.get("brand") or c.get("product") or c.get("goal")):
            continue  # skip empty/aborted calls
        want = desired_outcome(c.get("goal", ""))
        records.append({
            "input": {
                "goal": c.get("goal", ""),
                "brand": c.get("brand", ""),
                "product": c.get("product", ""),
                "pitch": c.get("pitch", ""),
                "persona": c.get("persona", ""),
                "customer": c.get("customer", ""),
                "customer_profile": customer_profile(t),
                "max_turns": 10,
            },
            # The ideal: close the deal and leave the customer feeling positive.
            # The eval gives next_step_agreed half credit on the deal component, so a
            # demo-objective call isn't punished for "only" booking the demo.
            "expected_output": {
                "deal_closed": True,
                "deal_score": 1.0,
                "final_sentiment": 1.0,
                "overall_sentiment": 1.0,
                "_acceptable_outcome": want,
            },
        })
        meta.append({
            "source": Path(f).name,
            "actual_outcome": (t.get("call_outcome") or {}).get("outcome"),
            "overall_sentiment": (t.get("analysis", {}).get("overall") or {}).get("score"),
            "target_outcome": want,
        })

    if not records:
        print("No usable transcripts found in", TRANSCRIPTS)
        return
    OUT.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    META.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    print(f"Wrote {len(records)} scenarios -> {OUT}")
    for m in meta:
        print(f"  {m['source']}: target={m['target_outcome']} "
              f"(actual was {m['actual_outcome']}, sentiment {m['overall_sentiment']})")


if __name__ == "__main__":
    main()
