"""Convert engine results to JSON-ready dicts. Decimals become strings so no digit is lost."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.selection.engine import COST_FORMULA, TIE_BREAK_ORDER
from app.selection.models import (
    METRICS,
    BaselineResult,
    Candidate,
    CostBreakdown,
    FileRequest,
    RankedCandidate,
    Rejection,
    SelectionResult,
    Weights,
)


def dec(value: Decimal) -> str:
    """Exact text, keeping the rounding precision (2.76000000 stays 8 decimals)."""
    return format(value, "f")


def request_dict(request: FileRequest) -> dict[str, Any]:
    return {
        "file_size_bytes": request.file_size_bytes,
        "security_level": request.security_level,
        "expected_accesses_per_month": dec(request.expected_accesses_per_month),
        "retention_months": dec(request.retention_months),
        "tier": request.tier,
    }


def weights_dict(weights: Weights) -> dict[str, str]:
    return {m: dec(getattr(weights, m)) for m in METRICS}


def candidate_dict(candidate: Candidate) -> dict[str, Any]:
    return {
        "region_id": candidate.region_id,
        "provider_id": candidate.provider_id,
        "provider_code": candidate.provider_code,
        "provider_name": candidate.provider_name,
        "region_code": candidate.region_code,
        "health_status": candidate.health_status,
        "illustrative": candidate.is_illustrative,
    }


def cost_dict(cost: CostBreakdown) -> dict[str, str]:
    return {
        "size_gb": dec(cost.size_gb.normalize()),
        "billed_months": dec(cost.billed_months.normalize()),
        "reads": dec(cost.reads.normalize()),
        "storage": dec(cost.storage),
        "request": dec(cost.request),
        "retrieval": dec(cost.retrieval),
        "egress": dec(cost.egress),
        "transfer": dec(cost.transfer),
        "total": dec(cost.total),
    }


def ranked_dict(item: RankedCandidate) -> dict[str, Any]:
    return {
        **candidate_dict(item.candidate),
        "rank": item.rank,
        "composite": dec(item.composite),
        "raw": {m: dec(item.raw[m]) for m in METRICS},
        "normalised": {m: dec(item.normalised[m]) for m in METRICS},
        "score": {m: dec(item.score[m]) for m in METRICS},
        "contribution": {m: dec(item.contribution[m]) for m in METRICS},
        "cost": cost_dict(item.cost),
    }


def rejection_dict(rejection: Rejection) -> dict[str, Any]:
    return {**candidate_dict(rejection.candidate), "reasons": list(rejection.reasons)}


def baseline_dict(baseline: BaselineResult) -> dict[str, Any]:
    return {
        "name": baseline.name,
        "note": baseline.note,
        "selected": candidate_dict(baseline.candidate) if baseline.candidate else None,
        "cost": cost_dict(baseline.cost) if baseline.cost else None,
    }


def result_dict(result: SelectionResult, preset: str | None) -> dict[str, Any]:
    return {
        "request": request_dict(result.request),
        "preset": preset,
        "weights": weights_dict(result.weights),
        "weights_note": result.weights_note,
        "selected": ranked_dict(result.selected) if result.selected else None,
        "ranking": [ranked_dict(r) for r in result.ranked],
        "rejected": [rejection_dict(r) for r in result.rejected],
        "advantages_vs_runner_up": list(result.advantages),
        "disadvantages_vs_runner_up": list(result.disadvantages),
        "explanation": result.explanation,
        "warnings": list(result.warnings),
        "cost_formula": list(COST_FORMULA),
        "tie_break_order": list(TIE_BREAK_ORDER),
    }
