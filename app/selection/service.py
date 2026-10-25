"""Glue between the database and the pure engine (the only impure part of module 4).

It loads candidates, weight presets and the constraint policy in one read-only transaction,
then calls the pure functions. Nothing is written: both operations here are dry runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.db.base import Database
from app.selection import baselines, engine, serialize
from app.selection.models import Candidate, FileRequest, Policy, Weights
from app.selection.presets import DEFAULT_PRESET, PRESETS, resolve_weights


class UnknownPresetError(ValueError):
    """The requested preset does not exist."""


class InvalidSelectionRequest(ValueError):
    """The file description is not valid (size, level, tier, accesses, retention)."""


@dataclass(frozen=True)
class SelectionInputs:
    request: FileRequest
    candidates: list[Candidate]
    presets: dict[str, Weights]
    default_preset: str
    policy: Policy


def load_inputs(database: Database, request: FileRequest) -> SelectionInputs:
    try:
        request.validate()
    except ValueError as exc:
        raise InvalidSelectionRequest(str(exc)) from exc
    with database.transaction() as session:
        candidates = session.providers.candidates(request.tier)
        profiles, default = session.providers.weight_profiles()
        policy = session.providers.policy()
    presets = profiles or dict(PRESETS)
    default_name = (
        default
        if default in presets
        else (DEFAULT_PRESET if DEFAULT_PRESET in presets else next(iter(presets)))
    )
    return SelectionInputs(request, candidates, presets, default_name, policy)


def _pick_weights(
    inputs: SelectionInputs, preset: str | None, overrides: Mapping[str, object] | None
) -> tuple[str, Weights, str | None]:
    name = (preset or inputs.default_preset).upper()
    if name not in inputs.presets:
        raise UnknownPresetError(
            f"unknown preset '{preset}' (available: {', '.join(sorted(inputs.presets))})"
        )
    weights, note = resolve_weights(inputs.presets[name], overrides)
    return name, weights, note


def select_provider(
    database: Database,
    request: FileRequest,
    *,
    preset: str | None = None,
    overrides: Mapping[str, object] | None = None,
    fixed_provider_code: str | None = None,
) -> dict[str, Any]:
    """Dry-run selection: rank, explain, and report what the two baselines would have chosen."""
    inputs = load_inputs(database, request)
    name, weights, note = _pick_weights(inputs, preset, overrides)
    result = engine.select(
        request,
        inputs.candidates,
        weights,
        inputs.policy,
        weights_note=note,
        preset_name=name if not overrides else f"{name} (with overrides)",
    )
    output = serialize.result_dict(result, name)
    output["baselines"] = {
        "cheapest_only": serialize.baseline_dict(
            baselines.cheapest_only(request, inputs.candidates, inputs.policy)
        ),
    }
    if fixed_provider_code:
        output["baselines"]["fixed_provider"] = serialize.baseline_dict(
            baselines.fixed_provider(
                request, inputs.candidates, inputs.policy, fixed_provider_code.upper()
            )
        )
    output["dry_run"] = True
    return output


def compare_providers(
    database: Database, request: FileRequest, *, fixed_provider_code: str | None = None
) -> dict[str, Any]:
    """Side-by-side: every provider-region's raw numbers and eligibility, plus the winner under
    each preset, so a reader can see how the weights change the outcome."""
    inputs = load_inputs(database, request)
    results = engine.compare_presets(request, inputs.candidates, inputs.presets, inputs.policy)

    reasons = {
        c.region_id: engine.check_constraints(request, c, inputs.policy) for c in inputs.candidates
    }
    rows = []
    for c in inputs.candidates:
        priced = c.pricing is not None
        rows.append(
            {
                **serialize.candidate_dict(c),
                "eligible": not reasons[c.region_id],
                "rejection_reasons": reasons[c.region_id],
                "reliability_score": serialize.dec(c.reliability_score),
                "security_score": serialize.dec(c.security_score),
                "latency_ms": serialize.dec(c.latency_ms) if c.latency_ms is not None else None,
                "max_object_bytes": c.max_object_bytes,
                "egress_usd_per_gb": serialize.dec(c.egress_usd_per_gb),
                "cost": serialize.cost_dict(engine.estimate_cost(request, c)) if priced else None,
            }
        )
    output: dict[str, Any] = {
        "request": serialize.request_dict(request),
        "candidates": rows,
        "presets": {
            name: {
                "weights": serialize.weights_dict(inputs.presets[name]),
                "winner": serialize.candidate_dict(r.selected.candidate) if r.selected else None,
                "ranking": [
                    {
                        "rank": item.rank,
                        "provider_code": item.candidate.provider_code,
                        "region_code": item.candidate.region_code,
                        "composite": serialize.dec(item.composite),
                    }
                    for item in r.ranked
                ],
            }
            for name, r in results.items()
        },
        "baselines": {
            "cheapest_only": serialize.baseline_dict(
                baselines.cheapest_only(request, inputs.candidates, inputs.policy)
            ),
        },
        "warnings": ["Prices, scores and latencies are ILLUSTRATIVE simulation values."]
        if any(c.is_illustrative for c in inputs.candidates)
        else [],
        "dry_run": True,
    }
    if fixed_provider_code:
        output["baselines"]["fixed_provider"] = serialize.baseline_dict(
            baselines.fixed_provider(
                request, inputs.candidates, inputs.policy, fixed_provider_code.upper()
            )
        )
    return output


def request_from_body(
    file_size_bytes: int,
    security_level: str,
    expected_accesses_per_month: Decimal,
    retention_months: Decimal,
    tier: str,
) -> FileRequest:
    return FileRequest(
        file_size_bytes=file_size_bytes,
        security_level=security_level,
        expected_accesses_per_month=expected_accesses_per_month,
        retention_months=retention_months,
        tier=tier,
    )
