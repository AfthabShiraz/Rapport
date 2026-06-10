# Overmind optimizer integration (rapport)

Wires rapport into the [Overmind optimizer](https://github.com/overmind-core/overmind)
— the offline loop that rewrites the agent's prompt against a dataset + policy +
eval spec. (The live app separately uses the Overmind *tracing* SDK; this is the
other half of the product.)

| Overmind needs | rapport provides |
| --- | --- |
| `run(input_data) -> dict` entrypoint | `sim_agent.py` — simulates a call: the live agent prompt (from `overmind.db`) vs a simulated prospect seeded with real objections |
| Dataset of cases | `build_dataset.py` — rebuilds scenarios from real calls in `overmind.db`; lost calls become the hard test cases |
| Policy | `policies.md` |
| Eval spec | `eval_spec.json` — 0.60 deal + 0.25 end-sentiment + 0.15 overall-sentiment − policy penalties |

## Each optimization round

```bash
# 1. Rebuild the dataset from the latest real calls
python build_dataset.py

# 2-4: Overmind agent skills (run inside Claude Code / Cursor, not plain shell)
/overmind-register-agent sim_agent.py          # entrypoint: sim_agent:run
/overmind-generate-spec-and-dataset rapport-sales
/overmind-optimize-agent rapport-sales
```

## Shipping the winning prompt to live calls

```bash
# creates a normal optimization row (pending) — accept it in the Report UI
python apply_prompt.py experiments/best_prompt.txt "Scarcity + social-proof reframe on deferral objections"

# or apply immediately
python apply_prompt.py experiments/best_prompt.txt "..." --accept
```

This flows through the product's standard optimization mechanism, so the change
shows up in iteration history and Analytics like any in-app optimization, and
the next live call uses it.
