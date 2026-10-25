"""The worked example (docs/03-selection-engine.md): engine output must equal the hand calculation.

Every number in EXPECTED was calculated by hand (and cross-checked with exact fractions, independent
of the engine) BEFORE being written here. All prices, scores and latencies are ILLUSTRATIVE.
Rounding rules being tested: money to 8 decimals, normalised values and composites to 6 decimals.
"""

from decimal import Decimal as D
from fractions import Fraction

import pytest

from app.selection.baselines import cheapest_only, fixed_provider
from app.selection.engine import compare_presets, select
from app.selection.example import EXAMPLE_CANDIDATES, EXAMPLE_REQUEST
from app.selection.models import METRICS
from app.selection.presets import DEFAULT_POLICY, PRESETS

# --- step 1: cost for THIS file (10 GiB, 12 months, 48 reads) -----------------------------------
# storage  = 10 * storage_price * 12          egress = 10 * egress_price * 48
# request  = put/1000 * 1 + get/1000 * 48     transfer = request + retrieval(0) + egress
COSTS = {  # region_id: (storage, request, egress, transfer, total)
    1: ("2.76", "0.0000242", "43.2", "43.2000242", "45.9600242"),  # AWS
    2: ("2.4", "0.00002662", "41.76", "41.76002662", "44.16002662"),  # Azure
    3: ("3.12", "0.0000242", "57.6", "57.6000242", "60.7200242"),  # Google Cloud
}

# --- steps 2-3: normalised value n, then score (1-n for cost and latency) ------------------------
NORMALISED = {  # metric: {region_id: n}
    "storage_cost": {1: "0.5", 2: "0", 3: "1"},       # (2.76-2.40)/(3.12-2.40) = 0.36/0.72
    "transfer_cost": {1: "0.090909", 2: "0", 3: "1"},  # 1.43999758 / 15.83999758 = 0.0909090...
    "latency": {1: "0", 2: "1", 3: "0.5"},             # (40-40)/20, (60-40)/20, (50-40)/20
    "reliability": {1: "1", 2: "0.5", 3: "0"},         # (9.5-9.3)/0.2, (9.4-9.3)/0.2, 0
    "security": {1: "0.5", 2: "1", 3: "0"},            # (9.2-9.1)/0.2, (9.3-9.1)/0.2, 0
}  # fmt: skip
SCORES = {
    "storage_cost": {1: "0.5", 2: "1", 3: "0"},
    "transfer_cost": {1: "0.909091", 2: "1", 3: "0"},
    "latency": {1: "1", 2: "0", 3: "0.5"},
    "reliability": {1: "1", 2: "0.5", 3: "0"},
    "security": {1: "0.5", 2: "1", 3: "0"},
}

# --- steps 4-5: weighted sums; exact value, then rounded to 6 decimals ---------------------------
#   AWS BALANCED: .25*.5 + .15*.909091 + .20*1 + .25*1 + .15*.5
#               = .125 + .13636365 + .2 + .25 + .075 = .78636365 -> 0.786364
COMPOSITES = {  # preset: {region_id: (exact, rounded)}
    "BALANCED": {1: ("0.78636365", "0.786364"), 2: ("0.675", "0.675"), 3: ("0.1", "0.1")},
    "COST_SAVER": {1: ("0.7318182", "0.731818"), 2: ("0.8", "0.8"), 3: ("0.05", "0.05")},
    "PERFORMANCE": {1: ("0.8909091", "0.890909"), 2: ("0.4", "0.4"), 3: ("0.25", "0.25")},
    "SECURE": {1: ("0.69545455", "0.695455"), 2: ("0.775", "0.775"), 3: ("0.05", "0.05")},
}
WINNER = {"BALANCED": 1, "COST_SAVER": 2, "PERFORMANCE": 1, "SECURE": 2}


def run(preset="BALANCED"):
    return select(
        EXAMPLE_REQUEST, EXAMPLE_CANDIDATES, PRESETS[preset], DEFAULT_POLICY, preset_name=preset
    )


def by_region(result):
    return {r.candidate.region_id: r for r in result.ranked}


def test_costs_match_the_hand_calculation():
    for region_id, ranked in by_region(run()).items():
        storage, request, egress, transfer, total = COSTS[region_id]
        assert ranked.cost.storage == D(storage)
        assert ranked.cost.request == D(request)
        assert ranked.cost.retrieval == D(0)
        assert ranked.cost.egress == D(egress)
        assert ranked.cost.transfer == D(transfer)
        assert ranked.cost.total == D(total)


def test_normalised_values_and_scores_match_the_hand_calculation():
    for region_id, ranked in by_region(run()).items():
        for metric in METRICS:
            assert ranked.normalised[metric] == D(NORMALISED[metric][region_id]), (
                region_id,
                metric,
            )
            assert ranked.score[metric] == D(SCORES[metric][region_id]), (region_id, metric)


@pytest.mark.parametrize("preset", list(PRESETS))
def test_composites_match_the_hand_calculation_exactly(preset):
    result = run(preset)
    for region_id, ranked in by_region(result).items():
        exact, rounded = COMPOSITES[preset][region_id]
        assert sum(ranked.contribution.values()) == D(exact)
        assert ranked.composite == D(rounded)
    assert result.selected.candidate.region_id == WINNER[preset]


def test_balanced_ranking_is_aws_then_azure_then_google():
    assert [r.candidate.provider_code for r in run().ranked] == ["AWS", "AZURE", "GCP"]


def test_cheapest_provider_is_correctly_not_chosen_under_balanced_weights():
    result = run("BALANCED")
    cheapest = min(result.ranked, key=lambda r: r.cost.total)
    assert cheapest.candidate.provider_code == "AZURE"  # $44.16002662 beats $45.9600242
    assert result.selected.candidate.provider_code == "AWS"
    assert result.selected.cost.total > cheapest.cost.total
    assert "It is NOT the cheapest eligible option: Azure (Central India)" in result.explanation


def test_advantages_and_disadvantages_of_the_winner_versus_the_runner_up():
    result = run("BALANCED")
    assert result.advantages == (
        "latency: 40 ms vs 60 ms (20 ms lower)",
        "reliability: 9.5 vs 9.4 (0.1 higher)",
    )
    assert result.disadvantages == (
        "storage cost: $2.76 vs $2.4 ($0.36 higher)",
        "transfer and request cost: $43.2000242 vs $41.76002662 ($1.43999758 higher)",
        "security: 9.2 vs 9.3 (0.1 lower)",
    )


def test_plain_english_explanation_for_the_worked_example():
    text = run("BALANCED").explanation
    assert text.startswith(
        "Selected AWS (ap-south-1) with a composite score of 0.786364 using the BALANCED weights"
    )
    assert "Runner-up: Azure (Central India) with 0.675 (0.111364 behind)." in text


def test_changing_the_weights_changes_the_winner():
    winners = {p: r.selected.candidate.provider_code for p, r in compare_presets(
        EXAMPLE_REQUEST, EXAMPLE_CANDIDATES, PRESETS, DEFAULT_POLICY).items()}  # fmt: skip
    assert winners == {
        "BALANCED": "AWS",
        "COST_SAVER": "AZURE",
        "PERFORMANCE": "AWS",
        "SECURE": "AZURE",
    }


def test_baselines_on_the_worked_example():
    cheapest = cheapest_only(EXAMPLE_REQUEST, EXAMPLE_CANDIDATES, DEFAULT_POLICY)
    assert cheapest.candidate.provider_code == "AZURE" and cheapest.cost.total == D("44.16002662")
    fixed = fixed_provider(EXAMPLE_REQUEST, EXAMPLE_CANDIDATES, DEFAULT_POLICY, "GCP")
    assert fixed.candidate.provider_code == "GCP" and fixed.cost.total == D("60.7200242")
    # The engine's BALANCED choice differs from both baselines' logic: it is neither the cheapest
    # nor a fixed pick, and it is the best composite under the stated weights.
    assert run().selected.candidate.provider_code == "AWS"


def test_independent_exact_fraction_check_of_the_winner_and_scores():
    """Recompute the BALANCED composite for AWS with exact fractions, not Decimal, as a cross-check."""
    f = Fraction
    storage = {1: f(276, 100), 2: f(24, 10), 3: f(312, 100)}
    transfer = {1: f(432000242, 10**7), 2: f(4176002662, 10**8), 3: f(576000242, 10**7)}

    def n(v, k):
        return (v[k] - min(v.values())) / (max(v.values()) - min(v.values()))

    s_storage = 1 - n(storage, 1)
    s_transfer = 1 - round(n(transfer, 1) * 10**6) / f(10**6)
    exact = (
        f(25, 100) * s_storage
        + f(15, 100) * s_transfer
        + f(20, 100) * 1
        + f(25, 100) * 1
        + f(15, 100) * f(1, 2)
    )
    assert float(exact) == pytest.approx(0.78636365, abs=1e-9)
    assert run().ranked[0].composite == D("0.786364")
