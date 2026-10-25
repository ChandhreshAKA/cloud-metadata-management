"""Scenarios: different weights pick different winners; the cheapest is not always chosen.

Providers here are ILLUSTRATIVE and invented: CheapCo is cheapest, FastCo has the lowest latency,
SecureCo has the best security and reliability.
"""

from decimal import Decimal as D

from app.selection.baselines import cheapest_only, fixed_provider
from app.selection.engine import select
from app.selection.models import Candidate, FileRequest, TierPricing
from app.selection.presets import DEFAULT_POLICY, PRESETS, resolve_weights

TIB = 1024**4


def provider(n, name, storage, egress, latency, reliability, security, **extra):
    base = dict(
        region_id=n, provider_id=n, provider_code=name.upper(), provider_name=name,
        region_code="r", provider_active=True, region_active=True, health_status="UP",
        reliability_score=D(reliability), security_score=D(security), max_object_bytes=5 * TIB,
        egress_usd_per_gb=D(egress),
        pricing=TierPricing("HOT", D(storage), D(0), D("0.005"), D("0.0004"), 0),
        latency_ms=D(latency), is_illustrative=True,
    )  # fmt: skip
    base.update(extra)
    return Candidate(**base)


POOL = [
    provider(1, "CheapCo", "0.010", "0.05", "200", "9.2", "9.3"),
    provider(2, "FastCo", "0.030", "0.10", "10", "9.5", "9.3"),
    provider(3, "SecureCo", "0.025", "0.09", "120", "9.6", "9.9"),
]
REQUEST = FileRequest(1024**3, "MEDIUM", D(1), D(12), "HOT")


def winner(weights, request=REQUEST, pool=POOL):
    return select(request, pool, weights, DEFAULT_POLICY).selected.candidate.provider_name


def test_scenario_1_cost_focus_picks_the_cheap_provider():
    assert winner(PRESETS["COST_SAVER"]) == "CheapCo"


def test_scenario_2_performance_focus_picks_the_fast_provider():
    assert winner(PRESETS["PERFORMANCE"]) == "FastCo"


def test_scenario_3_security_focus_picks_the_secure_provider():
    assert winner(PRESETS["SECURE"]) == "SecureCo"


def test_three_different_weight_sets_give_three_different_winners():
    assert {winner(PRESETS[p]) for p in ("COST_SAVER", "PERFORMANCE", "SECURE")} == {
        "CheapCo", "FastCo", "SecureCo",
    }  # fmt: skip


def test_per_request_override_can_flip_the_winner():
    assert winner(PRESETS["BALANCED"]) == "SecureCo"
    heavy_cost, note = resolve_weights(
        PRESETS["BALANCED"], {"storage_cost": "5", "transfer_cost": "5"}
    )
    assert note is not None  # 5 + 5 + .2 + .25 + .15 is not 1, so it was normalised and said so
    assert winner(heavy_cost) == "CheapCo"


def test_cheapest_provider_is_correctly_not_chosen_when_other_metrics_matter():
    result = select(REQUEST, POOL, PRESETS["BALANCED"], DEFAULT_POLICY)
    cheapest = min(result.ranked, key=lambda r: r.cost.total)
    assert cheapest.candidate.provider_name == "CheapCo"
    assert result.selected.candidate.provider_name == "SecureCo"
    assert cheapest_only(REQUEST, POOL, DEFAULT_POLICY).candidate.provider_name == "CheapCo"
    assert "NOT the cheapest eligible option: CheapCo (r)" in result.explanation


def test_cheapest_provider_is_excluded_outright_when_it_fails_a_hard_constraint():
    high = FileRequest(
        1024**3, "HIGH", D(1), D(12), "HOT"
    )  # needs reliability >= 9.4; CheapCo has 9.2
    result = select(high, POOL, PRESETS["COST_SAVER"], DEFAULT_POLICY)
    assert [r.candidate.provider_name for r in result.rejected] == ["CheapCo"]
    assert "reliability score 9.2 is below the HIGH minimum 9.4" in result.rejected[0].reasons[0]
    assert result.selected.candidate.provider_name != "CheapCo"
    # the cheapest-only baseline respects the same constraints, so it also skips CheapCo
    assert cheapest_only(high, POOL, DEFAULT_POLICY).candidate.provider_name != "CheapCo"


def test_size_fit_is_a_hard_filter_not_a_score():
    small_limit = provider(
        4, "TinyCo", "0.001", "0.01", "5", "9.9", "9.9", max_object_bytes=1024**2
    )
    result = select(REQUEST, [*POOL, small_limit], PRESETS["BALANCED"], DEFAULT_POLICY)
    assert [r.candidate.provider_name for r in result.rejected] == ["TinyCo"]
    assert "exceeds the provider limit" in result.rejected[0].reasons[0]
    assert all(r.candidate.provider_name != "TinyCo" for r in result.ranked)


def test_a_down_provider_is_never_selected_even_if_it_would_win():
    best_but_down = provider(4, "GhostCo", "0.001", "0.01", "5", "9.9", "9.9", health_status="DOWN")
    result = select(REQUEST, [*POOL, best_but_down], PRESETS["BALANCED"], DEFAULT_POLICY)
    assert result.selected.candidate.provider_name == "SecureCo"
    assert "currently DOWN" in result.rejected[0].reasons[0]


def test_fixed_provider_baseline_ignores_better_options_but_obeys_constraints():
    assert (
        fixed_provider(REQUEST, POOL, DEFAULT_POLICY, "CHEAPCO").candidate.provider_name
        == "CheapCo"
    )
    high = FileRequest(1024**3, "HIGH", D(1), D(12), "HOT")
    blocked = fixed_provider(high, POOL, DEFAULT_POLICY, "CHEAPCO")
    assert blocked.candidate is None and "reliability score 9.2" in blocked.note
    assert fixed_provider(REQUEST, POOL, DEFAULT_POLICY, "NOSUCH").note == "unknown provider NOSUCH"


def test_cheapest_only_with_nothing_eligible_reports_it():
    result = cheapest_only(REQUEST, [provider(1, "Down", "0.01", "0.05", "10", "9.5", "9.5", health_status="DOWN")],
                           DEFAULT_POLICY)  # fmt: skip
    assert result.candidate is None and result.note == "no eligible provider-region"


def test_a_cheap_provider_with_cold_tier_minimum_days_can_cost_more_for_short_retention():
    cold = FileRequest(1024**3, "MEDIUM", D(0), D(1), "COLD")
    short = provider(1, "ColdShort", "0.004", "0.09", "50", "9.5", "9.5",
                     pricing=TierPricing("COLD", D("0.004"), D("0.03"), D("0.01"), D("0.01"), 90))  # fmt: skip
    long_term = provider(2, "NoMinimum", "0.006", "0.09", "50", "9.5", "9.5",
                         pricing=TierPricing("COLD", D("0.006"), D("0.03"), D("0.01"), D("0.01"), 0))  # fmt: skip
    result = select(cold, [short, long_term], PRESETS["COST_SAVER"], DEFAULT_POLICY)
    by_name = {r.candidate.provider_name: r for r in result.ranked}
    assert by_name["ColdShort"].cost.storage == D("0.012")  # 1 GiB * 0.004 * 3 billed months
    assert by_name["NoMinimum"].cost.storage == D("0.006")  # 1 GiB * 0.006 * 1 month
    assert result.selected.candidate.provider_name == "NoMinimum"
