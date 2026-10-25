"""Baselines for the evaluation chapter: what simpler strategies would have chosen.

Both baselines respect the SAME hard constraints as the engine, so a comparison never "wins" by
breaking a security or size rule. They are pure functions like the engine.

  cheapest_only : the eligible candidate with the lowest total cost for this file
                  (ties: lower provider_id, then lower region_id). Ignores latency,
                  reliability and security beyond the hard minimums.
  fixed_provider: always the named provider (its best-eligible region by lowest provider/region
                  id), whatever the alternatives. If that provider fails a constraint, the
                  baseline reports that it cannot be used and why.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.selection.engine import check_constraints, estimate_cost
from app.selection.models import BaselineResult, Candidate, FileRequest, Policy


def cheapest_only(
    request: FileRequest, candidates: Iterable[Candidate], policy: Policy
) -> BaselineResult:
    request.validate()
    eligible = [c for c in candidates if not check_constraints(request, c, policy)]
    if not eligible:
        return BaselineResult("cheapest_only", None, None, "no eligible provider-region")
    priced = [(estimate_cost(request, c), c) for c in eligible]
    cost, winner = min(priced, key=lambda p: (p[0].total, p[1].provider_id, p[1].region_id))
    return BaselineResult("cheapest_only", winner, cost, "lowest total cost among eligible")


def fixed_provider(
    request: FileRequest, candidates: Iterable[Candidate], policy: Policy, provider_code: str
) -> BaselineResult:
    request.validate()
    own = sorted(
        (c for c in candidates if c.provider_code == provider_code),
        key=lambda c: (c.provider_id, c.region_id),
    )
    if not own:
        return BaselineResult("fixed_provider", None, None, f"unknown provider {provider_code}")
    reasons: list[str] = []
    for candidate in own:
        failed = check_constraints(request, candidate, policy)
        if not failed:
            return BaselineResult(
                "fixed_provider",
                candidate,
                estimate_cost(request, candidate),
                f"always {provider_code}",
            )
        reasons.append(f"{candidate.label}: {'; '.join(failed)}")
    return BaselineResult(
        "fixed_provider",
        None,
        None,
        f"{provider_code} cannot store this file ({' | '.join(reasons)})",
    )
