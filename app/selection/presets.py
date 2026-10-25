"""Weight presets and weight validation.

PRESETS mirrors the rows inserted by sql/seed_data.sql into weight_profiles (the database is the
source of truth at run time; a unit test keeps this copy and the SQL in agreement). The
engine itself never reads the database: callers pass a Weights object in.

Names: COST_SAVER = cost-focused, PERFORMANCE = performance-focused, SECURE = security-focused.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from app.selection.models import METRICS, Policy, Weights

DEFAULT_PRESET = "BALANCED"

PRESETS: dict[str, Weights] = {
    "BALANCED": Weights(
        Decimal("0.25"), Decimal("0.15"), Decimal("0.20"), Decimal("0.25"), Decimal("0.15")
    ),
    "COST_SAVER": Weights(
        Decimal("0.40"), Decimal("0.20"), Decimal("0.10"), Decimal("0.20"), Decimal("0.10")
    ),
    "PERFORMANCE": Weights(
        Decimal("0.10"), Decimal("0.10"), Decimal("0.50"), Decimal("0.20"), Decimal("0.10")
    ),
    "SECURE": Weights(
        Decimal("0.10"), Decimal("0.05"), Decimal("0.10"), Decimal("0.25"), Decimal("0.50")
    ),
}

# Used when system_config has no override. security.min_score.* values equal those seeded by
# sql/seed_data.sql; the reliability minimums are chosen for the illustrative seed scores
# (sql/seed_selection_config.sql stores them so they can be changed without a code edit).
DEFAULT_POLICY = Policy(
    min_security={"LOW": Decimal("0"), "MEDIUM": Decimal("8.0"), "HIGH": Decimal("9.2")},
    min_reliability={"LOW": Decimal("0"), "MEDIUM": Decimal("9.0"), "HIGH": Decimal("9.4")},
)

_TOLERANCE = Decimal("0.0000001")


class WeightError(ValueError):
    """Weights are invalid (negative, not a number, unknown metric, or all zero)."""


def _to_decimal(metric: str, value: object) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise WeightError(f"weight for {metric} is not a number") from exc
    if not number.is_finite():
        raise WeightError(f"weight for {metric} is not a finite number")
    if number < 0:
        raise WeightError(f"weight for {metric} is negative")
    return number


def resolve_weights(
    base: Weights, overrides: Mapping[str, object] | None = None
) -> tuple[Weights, str | None]:
    """Apply per-request overrides on top of `base`, validate, and normalise if needed.

    * Unknown metric names, negative or non-numeric values raise WeightError.
    * The result must sum to 1. If it does not (for example you override one weight and leave
      the rest), the weights are DIVIDED BY THEIR SUM and the returned note says so.
    * If the weights add up to 0 they cannot be normalised: WeightError.
    """
    merged = base.as_dict()
    for metric, raw in (overrides or {}).items():
        if metric not in METRICS:
            raise WeightError(f"unknown weight '{metric}' (expected one of {', '.join(METRICS)})")
        merged[metric] = _to_decimal(metric, raw)
    for metric in METRICS:  # also validates the base weights
        merged[metric] = _to_decimal(metric, merged[metric])

    total = sum(merged.values(), Decimal(0))
    if total == 0:
        raise WeightError("weights must not all be zero")
    note = None
    if abs(total - 1) > _TOLERANCE:
        merged = {m: v / total for m, v in merged.items()}
        shown = format(total.normalize(), "f")
        note = f"weights summed to {shown}, so each was divided by {shown} to sum to 1"
    return Weights(**{m: merged[m] for m in METRICS}), note
