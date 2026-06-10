# Overmind integration

This wires Rapport into [Overmind](https://github.com/overmind-core/overmind), an
agent optimizer that repeatedly runs an agent against a dataset + policy + eval
spec and rewrites it (prompt, tool descriptions, logic) to score higher.

## The mapping

| Overmind needs | Rapport provides |
| --- | --- |
| An agent `run(input_data: dict) -> dict` | [`sales_agent.py`](../sales_agent.py) — simulates a full sales call (our prompt vs. a simulated customer) and returns the outcome. |
| The optimizable artifact | `SALES_SYSTEM_PROMPT` in `sales_agent.py` — **the same prompt the live voice call uses** (`app.py` imports it), so an optimized prompt ships to real calls. |
| A dataset of cases | [`build_dataset.py`](build_dataset.py) turns real saved calls (with `analysis.py` sentiment) into scenarios; hard real calls become the test cases. |
| A policy | [`policies.md`](policies.md) — the sales policy (honesty, structure, no pushiness). |
| An eval spec | [`eval_spec.json`](eval_spec.json) — score = objective hit + LLM-judged call quality. |

Why a simulation: a live mic call can't sit inside an optimization loop, so `run()`
plays the call as text (Azure chat on both sides). The customer is seeded with the
*actual* objections that tanked the real call — so Overmind optimizes against the
moments our sentiment analysis flagged as failures.

## One-time setup

```bash
pipx install overmind            # or: uv tool install overmind
cd /Users/alisa/Documents/hackathon/Rapport
overmind init                    # set LLM provider keys + models for the optimizer
```

`run()` uses the same Azure resource as the app via `.env`
(`AZURE_CHAT_DEPLOYMENT` etc.), so make sure those are set.

## Each optimization round

```bash
# 1. Rebuild the dataset from the latest real calls (after running them through analysis.py)
python overmind/build_dataset.py

# 2. Register the agent entrypoint (validates the run() contract)
/overmind-register-agent sales_agent.py        # entrypoint: sales_agent:run

# 3. Generate / refine the spec + dataset (uses our seed policies.md + eval_spec.json + dataset.json)
/overmind-generate-spec-and-dataset rapport-sales

# 4. Optimize
/overmind-optimize-agent rapport-sales
```

Output lands in `experiments/`: `best_agent.py` (improved prompt/logic),
`report.md` (diffs + score history), `results.tsv`, `traces/`.

> Steps 2–4 are Overmind **agent skills** — run them inside Claude Code / Cursor,
> not as plain shell commands.

## Shipping the improved prompt to live calls

Open `experiments/best_agent.py`, copy the improved `SALES_SYSTEM_PROMPT` (and any
`end_call` description changes) back into [`sales_agent.py`](../sales_agent.py).
Because `app.py` imports from there, the next live voice call uses the optimized
agent. Re-run real calls → `analysis.py` → `build_dataset.py` to close the loop.

## The full loop

```
live call (app.py + sales_agent prompt)
   -> transcripts/*.json (+ live face/voice/text sentiment)
   -> analysis.py  (per-turn sentiment, key_moments, what_went_wrong/fix, outcome)
   -> build_dataset.py  (hard scenarios + target outcomes)
   -> Overmind optimize  (rewrites SALES_SYSTEM_PROMPT against policy + eval)
   -> paste best prompt back into sales_agent.py  -> better live calls
```
