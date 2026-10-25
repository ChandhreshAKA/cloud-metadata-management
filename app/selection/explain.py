"""Turn engine numbers into advantages, disadvantages and a plain-English explanation.

Pure text generation from values already computed by the engine; nothing here decides anything.
"""

from __future__ import annotations

from decimal import Decimal

from app.selection.models import (
    METRIC_LABELS,
    METRICS,
    MINIMISED,
    FileRequest,
    RankedCandidate,
    Rejection,
    Weights,
)

_UNIT = {
    "storage_cost": "$",
    "transfer_cost": "$",
    "latency": " ms",
    "reliability": "",
    "security": "",
}


def fmt(value: Decimal) -> str:
    """Plain decimal text without exponent or trailing zeros (0.50 -> 0.5, 100 -> 100)."""
    text = format(value.normalize(), "f")
    return text


def _show(metric: str, value: Decimal) -> str:
    if metric in ("storage_cost", "transfer_cost"):
        return f"${fmt(value)}"
    return f"{fmt(value)}{_UNIT[metric]}"


def compare_metrics(winner: RankedCandidate, other: RankedCandidate) -> tuple[list[str], list[str]]:
    """Per-metric advantages and disadvantages of `winner` relative to `other`."""
    advantages: list[str] = []
    disadvantages: list[str] = []
    for metric in METRICS:
        a, b = winner.raw[metric], other.raw[metric]
        if a == b:
            continue
        better = (a < b) if metric in MINIMISED else (a > b)
        diff = abs(a - b)
        word = (
            ("lower" if metric in MINIMISED else "higher")
            if better
            else ("higher" if metric in MINIMISED else "lower")
        )
        line = (
            f"{METRIC_LABELS[metric]}: {_show(metric, a)} vs {_show(metric, b)} "
            f"({_show(metric, diff)} {word})"
        )
        (advantages if better else disadvantages).append(line)
    return advantages, disadvantages


def describe_weights(weights: Weights) -> str:
    return ", ".join(f"{METRIC_LABELS[m]} {fmt(getattr(weights, m))}" for m in METRICS)


def build_explanation(
    request: FileRequest,
    weights: Weights,
    preset_name: str | None,
    ranked: tuple[RankedCandidate, ...],
    rejected: tuple[Rejection, ...],
) -> str:
    sentences: list[str] = []
    if not ranked:
        sentences.append(
            f"No provider-region can store this {request.security_level}-security file of "
            f"{request.file_size_bytes} bytes in tier {request.tier}."
        )
    else:
        winner = ranked[0]
        basis = f"the {preset_name} weights" if preset_name else "custom weights"
        sentences.append(
            f"Selected {winner.candidate.label} with a composite score of {fmt(winner.composite)} "
            f"using {basis} ({describe_weights(weights)})."
        )
        if len(ranked) == 1:
            sentences.append(
                "It is the only provider-region that passed the hard constraints, so every metric "
                "scores 1.0 by definition and no comparison was needed."
            )
        else:
            runner = ranked[1]
            margin = winner.composite - runner.composite
            sentences.append(
                f"Runner-up: {runner.candidate.label} with {fmt(runner.composite)} "
                f"({fmt(margin)} behind)."
            )
            diffs = {m: winner.contribution[m] - runner.contribution[m] for m in METRICS}
            leads = sorted((m for m in METRICS if diffs[m] > 0), key=lambda m: (-diffs[m], m))
            lags = sorted((m for m in METRICS if diffs[m] < 0), key=lambda m: (diffs[m], m))
            if leads:
                sentences.append(
                    "The lead comes from "
                    + ", ".join(f"{METRIC_LABELS[m]} (+{fmt(diffs[m])})" for m in leads[:3])
                    + "."
                )
            if lags:
                sentences.append(
                    f"{runner.candidate.label} is better on "
                    + ", ".join(f"{METRIC_LABELS[m]} ({fmt(-diffs[m])} of score)" for m in lags[:3])
                    + ", which was not enough to overcome the lead."
                )
            cheapest = min(ranked, key=lambda r: (r.cost.total, r.candidate.provider_id))
            if cheapest is winner:
                sentences.append("It is also the cheapest eligible option.")
            else:
                extra = winner.cost.total - cheapest.cost.total
                sentences.append(
                    f"It is NOT the cheapest eligible option: {cheapest.candidate.label} costs "
                    f"${fmt(cheapest.cost.total)} over {fmt(request.retention_months)} months "
                    f"versus ${fmt(winner.cost.total)} (${fmt(extra)} more for the winner) but "
                    f"ranked #{cheapest.rank} because the other metrics, weighted as above, "
                    "outweighed price."
                )
    for rejection in rejected:
        sentences.append(f"Excluded {rejection.candidate.label}: {'; '.join(rejection.reasons)}.")
    return " ".join(sentences)
