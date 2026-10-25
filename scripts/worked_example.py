"""Print the worked example of docs/03-selection-engine.md with every intermediate number.

    .venv\\Scripts\\python.exe scripts\\worked_example.py

No database and no network: it runs the pure engine on the illustrative data in
app/selection/example.py. ALL PRICES, SCORES AND LATENCIES ARE ILLUSTRATIVE.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.selection.baselines import cheapest_only, fixed_provider  # noqa: E402
from app.selection.engine import COST_FORMULA, TIE_BREAK_ORDER, select  # noqa: E402
from app.selection.example import EXAMPLE_CANDIDATES, EXAMPLE_REQUEST  # noqa: E402
from app.selection.explain import fmt  # noqa: E402
from app.selection.models import METRIC_LABELS, METRICS  # noqa: E402
from app.selection.presets import DEFAULT_POLICY, PRESETS  # noqa: E402


def table(header: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    line = "  ".join(h.ljust(w) for h, w in zip(header, widths, strict=True))
    print(line)
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(row, widths, strict=True)))
    print()


def main() -> None:
    r = EXAMPLE_REQUEST
    print("WORKED EXAMPLE - every price, score and latency below is ILLUSTRATIVE (made up)\n")
    print(
        f"File: {r.file_size_bytes} bytes (10 GiB), {r.security_level} security, tier {r.tier}, "
        f"kept {fmt(r.retention_months)} months, {fmt(r.expected_accesses_per_month)} reads a month\n"
    )
    print("Cost formulas:")
    for line in COST_FORMULA:
        print("  " + line)
    print()

    result = select(
        r, EXAMPLE_CANDIDATES, PRESETS["BALANCED"], DEFAULT_POLICY, preset_name="BALANCED"
    )
    by_id = {x.candidate.region_id: x for x in result.ranked}
    order = [by_id[c.region_id] for c in EXAMPLE_CANDIDATES]

    print("STEP 1  Inputs per provider")
    table(
        ["provider", "storage $/GB-mo", "put $/1k", "get $/1k", "egress $/GB", "latency ms", "reliab.", "security"],
        [[c.provider_name, fmt(c.pricing.storage_usd_per_gb_month), fmt(c.pricing.put_usd_per_1k),
          fmt(c.pricing.get_usd_per_1k), fmt(c.egress_usd_per_gb), fmt(c.latency_ms),
          fmt(c.reliability_score), fmt(c.security_score)] for c in EXAMPLE_CANDIDATES],
    )  # fmt: skip

    print(
        "STEP 2  Hard constraints (all pass: nothing is DOWN or disabled, size fits, MEDIUM minimums met)"
    )
    print(f"        eligible: {len(result.ranked)}  rejected: {len(result.rejected)}\n")

    print(
        f"STEP 3  Cost for this file (reads = 4 x 12 = {fmt(order[0].cost.reads)}, size_gb = {fmt(order[0].cost.size_gb)})"
    )
    table(
        ["provider", "storage", "request", "retrieval", "egress", "transfer", "total"],
        [[x.candidate.provider_name, *(fmt(v) for v in (x.cost.storage, x.cost.request, x.cost.retrieval,
          x.cost.egress, x.cost.transfer, x.cost.total))] for x in order],
    )  # fmt: skip

    print("STEP 4  Min-max normalisation n = (x - min) / (max - min), 6 decimals; then score")
    print("        (cost and latency: score = 1 - n; reliability and security: score = n)")
    for metric in METRICS:
        print(f"  {METRIC_LABELS[metric]}")
        table(
            ["provider", "raw", "n", "score"],
            [[x.candidate.provider_name, fmt(x.raw[metric]), fmt(x.normalised[metric]), fmt(x.score[metric])]
             for x in order],
        )  # fmt: skip

    for name, weights in PRESETS.items():
        res = select(r, EXAMPLE_CANDIDATES, weights, DEFAULT_POLICY, preset_name=name)
        by = {x.candidate.region_id: x for x in res.ranked}
        print(f"STEP 5  Weighted sum, preset {name}: " + ", ".join(
            f"{m} {fmt(getattr(weights, m))}" for m in METRICS))  # fmt: skip
        table(
            ["provider", *(m for m in METRICS), "exact sum", "composite", "rank"],
            [[c.provider_name, *(fmt(by[c.region_id].contribution[m]) for m in METRICS),
              fmt(sum(by[c.region_id].contribution.values())), fmt(by[c.region_id].composite),
              str(by[c.region_id].rank)] for c in EXAMPLE_CANDIDATES],
        )  # fmt: skip
        print(f"        winner under {name}: {res.selected.candidate.label}\n")

    print("STEP 6  Tie-break order:", "; ".join(TIE_BREAK_ORDER), "\n")
    print("RESULT (BALANCED)")
    print(result.explanation, "\n")
    print("Advantages of the winner over the runner-up:")
    for line in result.advantages:
        print("  +", line)
    print("Disadvantages:")
    for line in result.disadvantages:
        print("  -", line)

    cheap = cheapest_only(r, EXAMPLE_CANDIDATES, DEFAULT_POLICY)
    fixed = fixed_provider(r, EXAMPLE_CANDIDATES, DEFAULT_POLICY, "GCP")
    print("\nBASELINES")
    print(f"  cheapest_only : {cheap.candidate.label}, total ${fmt(cheap.cost.total)}")
    print(f"  fixed_provider: {fixed.candidate.label}, total ${fmt(fixed.cost.total)}")


if __name__ == "__main__":
    main()
