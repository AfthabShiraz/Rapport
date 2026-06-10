"""Seeded analytics dataset — returned when fewer than 5 real calls exist."""
import random

OPT_INDICES = [8, 19, 33]


def seeded_analytics():
    rnd = random.Random(42)
    n = 50
    per_eng = []
    for i in range(n):
        base = 2.6 + 4.4 * (i / (n - 1)) ** 1.1
        bump = 0.4 * sum(1 for j in OPT_INDICES if i >= j)
        per_eng.append(round(min(9.3, max(1.5, base + bump + rnd.uniform(-0.5, 0.5))), 1))

    converted_at = {6, 12, 16, 20, 23, 26, 29, 31, 34, 36, 38, 40, 42, 44, 45, 47, 49}
    per_conv = [i in converted_at for i in range(n)]

    return {
        "total_calls": n,
        "conversion_rate": round(sum(per_conv) / n, 2),
        "avg_engagement": round(sum(per_eng) / n, 1),
        "avg_duration_seconds": 252,
        "objection_handle_rate": 0.78,
        "per_call_engagement": per_eng,
        "per_call_converted": per_conv,
        "optimization_call_indices": OPT_INDICES,
        "optimizations_applied": len(OPT_INDICES),
        "seeded": True,
    }
