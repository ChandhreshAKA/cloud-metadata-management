"""Selection engine: normalisation, constraints, weights, costs, tie-breaking, purity."""

import random
import re
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.selection.engine import (
    check_constraints,
    estimate_cost,
    normalise,
    select,
)
from app.selection.models import Candidate, FileRequest, TierPricing, Weights
from app.selection.presets import (
    DEFAULT_POLICY,
    PRESETS,
    WeightError,
    resolve_weights,
)

BALANCED = PRESETS["BALANCED"]
TIB = 1024**4


def make(n=1, **overrides) -> Candidate:
    base = dict(
        region_id=n, provider_id=n, provider_code=f"P{n}", provider_name=f"Provider{n}",
        region_code=f"r{n}", provider_active=True, region_active=True, health_status="UP",
        reliability_score=D("9.5"), security_score=D("9.5"), max_object_bytes=5 * TIB,
        egress_usd_per_gb=D("0.09"),
        pricing=TierPricing("HOT", D("0.02"), D("0"), D("0.005"), D("0.0004"), 0),
        latency_ms=D("50"), is_illustrative=True,
    )  # fmt: skip
    base.update(overrides)
    return Candidate(**base)


def request(**overrides) -> FileRequest:
    base = dict(
        file_size_bytes=1024**3, security_level="MEDIUM",
        expected_accesses_per_month=D(1), retention_months=D(12), tier="HOT",
    )  # fmt: skip
    base.update(overrides)
    return FileRequest(**base)


# ---- normalisation ---------------------------------------------------------------------------
def test_minimised_metric_gives_best_value_score_one_and_worst_zero():
    norm, score = normalise({1: D(10), 2: D(20), 3: D(30)}, minimise=True)
    assert norm == {1: D("0.000000"), 2: D("0.500000"), 3: D("1.000000")}
    assert score == {1: D(1), 2: D("0.500000"), 3: D(0)}


def test_maximised_metric_gives_highest_value_score_one():
    norm, score = normalise({1: D(10), 2: D(20), 3: D(30)}, minimise=False)
    assert score == norm == {1: D("0.000000"), 2: D("0.500000"), 3: D("1.000000")}


def test_all_equal_values_do_not_divide_by_zero_and_everyone_scores_one():
    for minimise in (True, False):
        norm, score = normalise({1: D(7), 2: D(7), 3: D(7)}, minimise=minimise)
        assert set(score.values()) == {D(1)}
        assert set(norm.values()) == {D(0) if minimise else D(1)}


def test_single_candidate_scores_one():
    assert normalise({9: D(123)}, minimise=True)[1] == {9: D(1)}


def test_no_values_gives_empty_result():
    assert normalise({}, minimise=True) == ({}, {})


def test_rounding_is_half_even_to_six_decimals():
    # (1 - 0) / 2_000_000 = 0.0000005 exactly -> half-even -> 0.000000 ; 3/2_000_000 = 0.0000015 -> 0.000002
    norm, _ = normalise({1: D(0), 2: D(1), 3: D(3), 4: D(2_000_000)}, minimise=False)
    assert norm[2] == D("0.000000") and norm[3] == D("0.000002")


def test_negative_and_tiny_ranges_are_handled():
    norm, _ = normalise({1: D("-5"), 2: D("5")}, minimise=False)
    assert norm == {1: D(0), 2: D(1)}
    norm, _ = normalise({1: D("0.00000001"), 2: D("0.00000002")}, minimise=False)
    assert norm == {1: D(0), 2: D(1)}


# ---- cost formula ----------------------------------------------------------------------------
def test_cost_formula_by_hand():
    """2 GiB, 6 months, 3 reads/month => 18 reads. Hand arithmetic below."""
    c = make(
        egress_usd_per_gb=D("0.10"),
        pricing=TierPricing("HOT", D("0.05"), D("0.01"), D("0.02"), D("0.004"), 0),
    )
    cost = estimate_cost(request(file_size_bytes=2 * 1024**3, retention_months=D(6),
                                 expected_accesses_per_month=D(3)), c)  # fmt: skip
    assert cost.size_gb == D(2) and cost.reads == D(18) and cost.billed_months == D(6)
    assert cost.storage == D("0.6")  # 2 * 0.05 * 6
    assert cost.request == D("0.000092")  # 0.02/1000*1 + 0.004/1000*18 = 0.00002 + 0.000072
    assert cost.retrieval == D("0.36")  # 2 * 0.01 * 18
    assert cost.egress == D("3.6")  # 2 * 0.10 * 18
    assert cost.transfer == D("3.960092")  # 0.000092 + 0.36 + 3.6
    assert cost.total == D("4.560092")  # 0.6 + 3.960092


def test_minimum_storage_days_are_billed_even_if_retention_is_shorter():
    c = make(pricing=TierPricing("COLD", D("0.01"), D("0"), D("0"), D("0"), 90))
    cost = estimate_cost(request(file_size_bytes=1024**3, retention_months=D(1), tier="COLD"), c)
    assert cost.billed_months == D(3) and cost.storage == D("0.03")  # 90 days = 3 months


def test_zero_size_and_zero_accesses_cost_only_the_upload_request():
    c = make()
    cost = estimate_cost(request(file_size_bytes=0, expected_accesses_per_month=D(0)), c)
    assert cost.storage == 0 and cost.egress == 0 and cost.retrieval == 0
    assert cost.request == D("0.00000500")  # one PUT: 0.005 / 1000


def test_estimating_cost_without_pricing_is_an_error():
    with pytest.raises(ValueError, match="no pricing"):
        estimate_cost(request(), make(pricing=None))


# ---- hard constraints ------------------------------------------------------------------------
def test_a_good_candidate_has_no_rejection_reasons():
    assert check_constraints(request(), make(), DEFAULT_POLICY) == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"provider_active": False}, "provider is disabled"),
        ({"region_active": False}, "region is disabled"),
        ({"health_status": "DOWN"}, "currently DOWN"),
        ({"pricing": None}, "no HOT pricing"),
        ({"latency_ms": None}, "no latency measurement"),
        ({"max_object_bytes": 10}, "exceeds the provider limit 10 bytes"),
        ({"security_score": D("7.9")}, "security score 7.9 is below the MEDIUM minimum 8"),
        ({"reliability_score": D("8.9")}, "reliability score 8.9 is below the MEDIUM minimum 9"),
    ],
)
def test_each_constraint_rejects_with_its_own_reason(overrides, expected):
    reasons = check_constraints(request(), make(**overrides), DEFAULT_POLICY)
    assert len(reasons) == 1 and expected in reasons[0]


def test_all_failing_reasons_are_reported_not_just_the_first():
    bad = make(
        provider_active=False, health_status="DOWN", security_score=D("1"), max_object_bytes=1
    )
    assert len(check_constraints(request(), bad, DEFAULT_POLICY)) == 4


def test_boundaries_are_inclusive():
    exactly = make(max_object_bytes=1024**3, security_score=D("8.0"), reliability_score=D("9.0"))
    assert check_constraints(request(), exactly, DEFAULT_POLICY) == []
    one_byte_over = request(file_size_bytes=1024**3 + 1)
    assert check_constraints(one_byte_over, exactly, DEFAULT_POLICY)


def test_security_requirement_rises_with_the_file_level():
    c = make(security_score=D("9.1"), reliability_score=D("9.5"))
    assert check_constraints(request(security_level="MEDIUM"), c, DEFAULT_POLICY) == []
    high = check_constraints(request(security_level="HIGH"), c, DEFAULT_POLICY)
    assert any("HIGH minimum 9.2" in r for r in high)


def test_rejected_candidates_never_take_part_in_ranking_or_normalisation():
    """A DOWN provider with extreme values must not distort the others' normalisation."""
    ok1, ok2 = make(1, latency_ms=D(40)), make(2, latency_ms=D(60))
    down = make(3, health_status="DOWN", latency_ms=D(1))
    result = select(request(), [ok1, ok2, down], BALANCED, DEFAULT_POLICY)
    assert [r.candidate.region_id for r in result.ranked] == [1, 2]
    assert result.ranked[0].normalised["latency"] == D(0) and result.ranked[1].normalised[
        "latency"
    ] == D(1)
    assert [x.candidate.region_id for x in result.rejected] == [3]


def test_degraded_is_allowed_but_warned_about():
    result = select(request(), [make(1, health_status="DEGRADED")], BALANCED, DEFAULT_POLICY)
    assert result.selected is not None
    assert any("DEGRADED" in w for w in result.warnings)


def test_nothing_eligible_gives_no_selection_and_explains_why():
    result = select(request(), [make(1, health_status="DOWN"), make(2, max_object_bytes=1)],
                    BALANCED, DEFAULT_POLICY)  # fmt: skip
    assert result.selected is None and result.ranked == ()
    assert "No provider-region can store this" in result.explanation
    assert "Excluded Provider1 (r1): provider-region is currently DOWN" in result.explanation
    assert len(result.rejected) == 2


def test_invalid_requests_are_refused():
    for bad in (
        request(file_size_bytes=-1), request(security_level="TOP"), request(tier="FREEZER"),
        request(expected_accesses_per_month=D(-1)), request(retention_months=D(0)),
    ):  # fmt: skip
        with pytest.raises(ValueError):
            select(bad, [make()], BALANCED, DEFAULT_POLICY)


# ---- weights ---------------------------------------------------------------------------------
def test_presets_sum_to_exactly_one():
    for name, weights in PRESETS.items():
        assert sum(weights.as_dict().values()) == D(1), name


def test_presets_match_the_rows_seeded_by_sql():
    """Keeps the Python mirror and sql/seed_data.sql from drifting apart."""
    sql = (Path(__file__).resolve().parents[2] / "sql" / "seed_data.sql").read_text(
        encoding="utf-8"
    )
    rows = re.findall(
        r"VALUES \('(\w+)',\s+'[^']*',\s+([\d.]+), ([\d.]+), ([\d.]+), ([\d.]+), ([\d.]+), '[YN]'\)",
        sql,
    )
    assert {r[0] for r in rows} == set(PRESETS)
    for name, *numbers in rows:
        assert Weights(*(D(n) for n in numbers)) == PRESETS[name], name


def test_valid_weights_pass_unchanged_without_a_note():
    weights, note = resolve_weights(BALANCED)
    assert weights == BALANCED and note is None


def test_override_is_applied_then_normalised_and_the_note_says_so():
    weights, note = resolve_weights(
        BALANCED, {"latency": "0.45"}
    )  # 0.25+0.15+0.45+0.25+0.15 = 1.25
    assert note and "summed to 1.25" in note
    assert sum(weights.as_dict().values()) == D(1)
    assert weights.latency == D("0.45") / D("1.25")


def test_full_override_summing_to_one_needs_no_normalising():
    weights, note = resolve_weights(
        BALANCED,
        {"storage_cost": 1, "transfer_cost": 0, "latency": 0, "reliability": 0, "security": 0},
    )
    assert note is None and weights.storage_cost == 1


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"latency": "-0.1"}, "negative"),
        ({"latency": "abc"}, "not a number"),
        ({"latency": "NaN"}, "finite"),
        ({"latency": "Infinity"}, "finite"),
        ({"colour": "0.2"}, "unknown weight"),
        ({"storage_cost": 0, "transfer_cost": 0, "latency": 0, "reliability": 0, "security": 0},
         "all be zero"),
    ],
)  # fmt: skip
def test_invalid_weights_are_rejected(overrides, message):
    with pytest.raises(WeightError, match=message):
        resolve_weights(BALANCED, overrides)


# ---- tie-breaking ----------------------------------------------------------------------------
LATENCY_ONLY = Weights(D(0), D(0), D(1), D(0), D(0))


def ids(result):
    return [r.candidate.region_id for r in result.ranked]


def test_identical_candidates_tie_on_composite_and_the_lower_provider_id_wins():
    result = select(request(), [make(3), make(1), make(2)], BALANCED, DEFAULT_POLICY)
    assert ids(result) == [1, 2, 3]
    assert len({r.composite for r in result.ranked}) == 1


def test_same_provider_two_regions_tie_goes_to_lower_region_id():
    a = make(7, provider_id=1, region_code="x")
    b = make(5, provider_id=1, region_code="y")
    assert ids(select(request(), [a, b], BALANCED, DEFAULT_POLICY)) == [5, 7]


def test_tie_break_order_security_then_reliability_then_cost_then_ids():
    # latency is the only weighted metric and is equal everywhere, so composites tie exactly.
    # In every pair the candidate that should win is also the MORE EXPENSIVE one and has the
    # HIGHER provider id, so a wrong order (cost first, or id first) would pick the other.
    sec_hi = make(
        2, security_score=D("9.9"), reliability_score=D("9.1"), egress_usd_per_gb=D("0.20")
    )
    sec_lo = make(
        1, security_score=D("9.2"), reliability_score=D("9.9"), egress_usd_per_gb=D("0.05")
    )
    assert ids(select(request(), [sec_lo, sec_hi], LATENCY_ONLY, DEFAULT_POLICY)) == [2, 1]

    rel_hi = make(
        4, security_score=D("9.5"), reliability_score=D("9.9"), egress_usd_per_gb=D("0.20")
    )
    rel_lo = make(
        3, security_score=D("9.5"), reliability_score=D("9.4"), egress_usd_per_gb=D("0.05")
    )
    assert ids(select(request(), [rel_lo, rel_hi], LATENCY_ONLY, DEFAULT_POLICY)) == [4, 3]

    cheap = make(6, egress_usd_per_gb=D("0.05"))
    dear = make(5, egress_usd_per_gb=D("0.20"))
    assert ids(select(request(), [dear, cheap], LATENCY_ONLY, DEFAULT_POLICY)) == [6, 5]


def test_results_do_not_depend_on_the_order_candidates_are_supplied():
    pool = [
        make(1, latency_ms=D(40), egress_usd_per_gb=D("0.09")),
        make(2, latency_ms=D(60), egress_usd_per_gb=D("0.05")),
        make(3, latency_ms=D(50), security_score=D("9.9")),
        make(4, latency_ms=D(50)),
    ]
    expected = ids(select(request(), pool, BALANCED, DEFAULT_POLICY))
    rng = random.Random(1234)
    for _ in range(25):
        shuffled = pool[:]
        rng.shuffle(shuffled)
        assert ids(select(request(), shuffled, BALANCED, DEFAULT_POLICY)) == expected


# ---- purity ----------------------------------------------------------------------------------
def test_select_is_pure_same_input_same_output_and_inputs_untouched():
    pool = [make(1), make(2, latency_ms=D(80))]
    snapshot = list(pool)
    first = select(request(), pool, BALANCED, DEFAULT_POLICY)
    second = select(request(), pool, BALANCED, DEFAULT_POLICY)
    assert first == second and pool == snapshot


def test_single_eligible_candidate_scores_every_metric_one():
    result = select(request(), [make()], BALANCED, DEFAULT_POLICY)
    only = result.ranked[0]
    assert set(only.score.values()) == {D(1)} and only.composite == D("1.000000")
    assert result.advantages == () and "only provider-region" in result.explanation


def test_composite_is_always_between_zero_and_one():
    rng = random.Random(7)
    for _ in range(40):
        pool = [
            make(i, latency_ms=D(rng.randint(10, 300)), reliability_score=D(rng.randint(90, 99)) / 10,
                 security_score=D(rng.randint(92, 99)) / 10, egress_usd_per_gb=D(rng.randint(1, 20)) / 100)
            for i in range(1, 6)
        ]  # fmt: skip
        for weights in PRESETS.values():
            for ranked in select(request(), pool, weights, DEFAULT_POLICY).ranked:
                assert D(0) <= ranked.composite <= D(1)


def test_explanation_calls_out_a_non_cheapest_winner_with_numbers():
    fast_dear = make(1, latency_ms=D(10), egress_usd_per_gb=D("0.12"))
    slow_cheap = make(2, latency_ms=D(200), egress_usd_per_gb=D("0.05"))
    result = select(
        request(),
        [fast_dear, slow_cheap],
        replace(BALANCED),
        DEFAULT_POLICY,
        preset_name="BALANCED",
    )
    assert result.selected.candidate.region_id == 1
    assert "NOT the cheapest eligible option: Provider2 (r2)" in result.explanation
    assert "the BALANCED weights" in result.explanation
