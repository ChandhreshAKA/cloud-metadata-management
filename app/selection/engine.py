"""The provider selection engine: a pure function.

    select(request, candidates, weights, policy) -> SelectionResult

It reads nothing and writes nothing: no database, no files, no clock, no randomness. The same
inputs always give the same output, which is what makes it testable by hand and reusable by
upload (chunk 4), replica placement (chunk 5) and savings estimates (chunk 6).

PROCESS
  1. Hard constraints (before any ranking). A candidate is rejected if any of these fail, and
     EVERY failing reason is recorded:
       provider enabled, region enabled, not DOWN, priced for the requested tier, has a latency
       measurement, file size <= provider limit, security score >= minimum for the file's
       security level, reliability score >= minimum for that level.
     Size fit is a hard filter, never a scored criterion.
  2. Cost for THIS file, per candidate (money rounded to 8 decimals, ROUND_HALF_EVEN):
       size_gb        = file_size_bytes / 2^30
       billed_months  = max(retention_months, min_storage_days / 30)
       reads          = expected_accesses_per_month * retention_months
       storage_cost   = size_gb * storage_usd_per_gb_month * billed_months
       request_cost   = put_usd_per_1k / 1000 * 1  +  get_usd_per_1k / 1000 * reads
       retrieval_cost = size_gb * retrieval_usd_per_gb * reads
       egress_cost    = size_gb * egress_usd_per_gb * reads
       transfer_cost  = request_cost + retrieval_cost + egress_cost
       total_cost     = storage_cost + transfer_cost
  3. Five metrics: storage_cost, transfer_cost, latency (minimised); reliability, security
     (maximised). Min-max normalise each across the ELIGIBLE candidates only:
       n = (x - min) / (max - min), rounded to 6 decimals (ROUND_HALF_EVEN).
     ZERO RANGE (max == min: all equal, or a single candidate): there is nothing to compare, so
     every candidate gets score 1.0 on that metric (n is reported as 0 for minimised metrics and
     1 for maximised ones, so that score = 1 - n or n still holds). No division by zero occurs.
  4. Direction: score = 1 - n for minimised metrics, score = n for maximised. Higher is better.
  5. composite = sum(weight * score), rounded to 6 decimals. Weights are validated (>= 0) and
     must sum to 1; app.selection.presets.resolve_weights normalises and reports it if not.
  6. Ranking uses the ROUNDED composite, then ties are broken in this fixed order:
       higher security score, higher reliability score, lower total cost,
       lower provider_id, lower region_id.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from app.selection import explain
from app.selection.models import (
    GIB,
    METRICS,
    MINIMISED,
    Candidate,
    CostBreakdown,
    FileRequest,
    Policy,
    RankedCandidate,
    Rejection,
    SelectionResult,
    Weights,
)

MONEY_QUANTUM = Decimal("0.00000001")  # 8 decimals
SCORE_QUANTUM = Decimal("0.000001")  # 6 decimals
_PRECISION = 40
COST_FORMULA = (
    "size_gb = file_size_bytes / 2^30",
    "billed_months = max(retention_months, min_storage_days / 30)",
    "reads = expected_accesses_per_month * retention_months",
    "storage_cost = size_gb * storage_usd_per_gb_month * billed_months",
    "request_cost = put_usd_per_1k / 1000 * 1 + get_usd_per_1k / 1000 * reads",
    "retrieval_cost = size_gb * retrieval_usd_per_gb * reads",
    "egress_cost = size_gb * egress_usd_per_gb * reads",
    "transfer_cost = request_cost + retrieval_cost + egress_cost",
    "total_cost = storage_cost + transfer_cost",
    "money is rounded to 8 decimals; normalised values and composites to 6 (half-even)",
)
TIE_BREAK_ORDER = (
    "higher composite (rounded to 6 decimals)",
    "higher security score",
    "higher reliability score",
    "lower total cost",
    "lower provider_id",
    "lower region_id",
)


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)


def estimate_cost(request: FileRequest, candidate: Candidate) -> CostBreakdown:
    """Cost of storing and serving THIS file at `candidate` (formulas in the module docstring)."""
    pricing = candidate.pricing
    if pricing is None:
        raise ValueError(f"{candidate.label} has no pricing for tier {request.tier}")
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        size_gb = Decimal(request.file_size_bytes) / GIB
        billed_months = max(request.retention_months, Decimal(pricing.min_storage_days) / 30)
        reads = request.expected_accesses_per_month * request.retention_months
        storage = size_gb * pricing.storage_usd_per_gb_month * billed_months
        requests = pricing.put_usd_per_1k / 1000 * 1 + pricing.get_usd_per_1k / 1000 * reads
        retrieval = size_gb * pricing.retrieval_usd_per_gb * reads
        egress = size_gb * candidate.egress_usd_per_gb * reads
        return CostBreakdown(
            size_gb=size_gb,
            billed_months=billed_months,
            reads=reads,
            storage=_money(storage),
            request=_money(requests),
            retrieval=_money(retrieval),
            egress=_money(egress),
        )


def check_constraints(request: FileRequest, candidate: Candidate, policy: Policy) -> list[str]:
    """Every hard constraint the candidate fails (empty list = eligible)."""
    reasons: list[str] = []
    if not candidate.provider_active:
        reasons.append("provider is disabled")
    if not candidate.region_active:
        reasons.append("region is disabled")
    if candidate.health_status == "DOWN":
        reasons.append("provider-region is currently DOWN")
    if candidate.pricing is None:
        reasons.append(f"no {request.tier} pricing is registered")
    if candidate.latency_ms is None:
        reasons.append("no latency measurement is available")
    if request.file_size_bytes > candidate.max_object_bytes:
        reasons.append(
            f"file size {request.file_size_bytes} bytes exceeds the provider limit "
            f"{candidate.max_object_bytes} bytes"
        )
    min_security = policy.min_security[request.security_level]
    if candidate.security_score < min_security:
        reasons.append(
            f"security score {explain.fmt(candidate.security_score)} is below the "
            f"{request.security_level} minimum {explain.fmt(min_security)}"
        )
    min_reliability = policy.min_reliability[request.security_level]
    if candidate.reliability_score < min_reliability:
        reasons.append(
            f"reliability score {explain.fmt(candidate.reliability_score)} is below the "
            f"{request.security_level} minimum {explain.fmt(min_reliability)}"
        )
    return reasons


def normalise(
    values: Mapping[int, Decimal], *, minimise: bool
) -> tuple[dict[int, Decimal], dict[int, Decimal]]:
    """Min-max normalise. Returns (normalised, score); score is higher-is-better.

    Zero range gives every key score 1 (see module docstring), never a division by zero.
    """
    if not values:
        return {}, {}
    low, high = min(values.values()), max(values.values())
    if high == low:
        best = Decimal(0) if minimise else Decimal(1)
        return {k: best for k in values}, {k: Decimal(1) for k in values}
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        norm = {
            k: ((v - low) / (high - low)).quantize(SCORE_QUANTUM, rounding=ROUND_HALF_EVEN)
            for k, v in values.items()
        }
    score = {k: (Decimal(1) - n) if minimise else n for k, n in norm.items()}
    return norm, score


def _sort_key(item: tuple[Candidate, CostBreakdown, Decimal]):
    candidate, cost, composite = item
    return (
        -composite,
        -candidate.security_score,
        -candidate.reliability_score,
        cost.total,
        candidate.provider_id,
        candidate.region_id,
    )


def select(
    request: FileRequest,
    candidates: Iterable[Candidate],
    weights: Weights,
    policy: Policy,
    *,
    weights_note: str | None = None,
    preset_name: str | None = None,
) -> SelectionResult:
    request.validate()
    ordered = sorted(candidates, key=lambda c: (c.provider_id, c.region_id))

    eligible: list[Candidate] = []
    rejected: list[Rejection] = []
    for candidate in ordered:
        reasons = check_constraints(request, candidate, policy)
        if reasons:
            rejected.append(Rejection(candidate, tuple(reasons)))
        else:
            eligible.append(candidate)

    costs = {c.region_id: estimate_cost(request, c) for c in eligible}
    raw: dict[int, dict[str, Decimal]] = {
        c.region_id: {
            "storage_cost": costs[c.region_id].storage,
            "transfer_cost": costs[c.region_id].transfer,
            "latency": c.latency_ms,  # type: ignore[dict-item]  (None was rejected above)
            "reliability": c.reliability_score,
            "security": c.security_score,
        }
        for c in eligible
    }
    norm: dict[str, dict[int, Decimal]] = {}
    score: dict[str, dict[int, Decimal]] = {}
    for metric in METRICS:
        norm[metric], score[metric] = normalise(
            {rid: values[metric] for rid, values in raw.items()}, minimise=metric in MINIMISED
        )

    weight_of = weights.as_dict()
    scored: list[tuple[Candidate, CostBreakdown, Decimal]] = []
    contributions: dict[int, dict[str, Decimal]] = {}
    for c in eligible:
        parts = {m: weight_of[m] * score[m][c.region_id] for m in METRICS}
        contributions[c.region_id] = parts
        with localcontext() as ctx:
            ctx.prec = _PRECISION
            total = sum(parts.values(), Decimal(0))
        scored.append((c, costs[c.region_id], total.quantize(SCORE_QUANTUM, ROUND_HALF_EVEN)))
    scored.sort(key=_sort_key)

    ranked = tuple(
        RankedCandidate(
            rank=position,
            candidate=c,
            cost=cost,
            raw=raw[c.region_id],
            normalised={m: norm[m][c.region_id] for m in METRICS},
            score={m: score[m][c.region_id] for m in METRICS},
            contribution=contributions[c.region_id],
            composite=composite,
        )
        for position, (c, cost, composite) in enumerate(scored, start=1)
    )

    advantages: tuple[str, ...] = ()
    disadvantages: tuple[str, ...] = ()
    if len(ranked) >= 2:
        adv, dis = explain.compare_metrics(ranked[0], ranked[1])
        advantages, disadvantages = tuple(adv), tuple(dis)

    warnings: list[str] = []
    if any(c.is_illustrative for c in eligible):
        warnings.append(
            "Prices, scores and latencies are ILLUSTRATIVE simulation values, not vendor data."
        )
    if weights_note:
        warnings.append(weights_note)
    if ranked and ranked[0].candidate.health_status == "DEGRADED":
        warnings.append("The selected provider-region is currently DEGRADED.")

    return SelectionResult(
        request=request,
        weights=weights,
        weights_note=weights_note,
        ranked=ranked,
        rejected=tuple(rejected),
        selected=ranked[0] if ranked else None,
        advantages=advantages,
        disadvantages=disadvantages,
        explanation=explain.build_explanation(
            request, weights, preset_name, ranked, tuple(rejected)
        ),
        warnings=tuple(warnings),
    )


def compare_presets(
    request: FileRequest,
    candidates: Iterable[Candidate],
    presets: Mapping[str, Weights],
    policy: Policy,
) -> dict[str, SelectionResult]:
    """Run the same request under every preset (used to show that weights change the winner)."""
    pool = list(candidates)
    return {
        name: select(request, pool, weights, policy, preset_name=name)
        for name, weights in presets.items()
    }
