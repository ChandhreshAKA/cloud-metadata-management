"""POST /selection/compare and /selection/select (FakeDatabase loaded with the Chunk 1 seed numbers).

The seed values are ILLUSTRATIVE. These endpoints are dry runs: nothing is stored.
"""

from dataclasses import replace
from decimal import Decimal as D

import pytest

from app.selection import engine
from app.selection.models import FileRequest
from app.selection.presets import DEFAULT_POLICY, PRESETS
from tests.fakes import _FakeProviders

GIB = 1024**3
BODY = {"file_size_bytes": GIB, "security_level": "MEDIUM", "expected_accesses_per_month": "1",
        "retention_months": "12", "tier": "HOT"}  # fmt: skip


def region_names(entries):
    return [f"{e['provider_code']}/{e['region_code']}" for e in entries]


# ---- who may call ----------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["USER", "ADMIN", "AUDITOR"])
@pytest.mark.parametrize("path", ["/selection/compare", "/selection/select"])
def test_every_role_may_run_a_dry_run(client, as_role, role, path):
    assert client.post(path, json=BODY, headers=as_role(role)).status_code == 200


@pytest.mark.parametrize("path", ["/selection/compare", "/selection/select"])
def test_a_token_is_required(client, seeded, path):
    assert client.post(path, json=BODY).status_code == 401


# ---- compare ---------------------------------------------------------------------------------
def test_compare_lists_every_region_with_hand_checkable_costs(client, as_role):
    data = client.post("/selection/compare", json=BODY, headers=as_role("USER")).json()
    assert region_names(data["candidates"]) == [
        "AWS/ap-south-1", "AWS/us-east-1", "AZURE/Central India", "AZURE/East US",
        "GCP/asia-south1", "GCP/us-central1",
    ]  # fmt: skip
    aws = data["candidates"][0]
    # 1 GiB, 12 months, 12 reads, AWS ap-south-1 HOT (seed): storage 1*0.025*12 = 0.3;
    # request 0.005/1000 + 0.0004/1000*12 = 0.0000098; egress 1*0.09*12 = 1.08
    assert aws["cost"]["storage"] == "0.30000000"
    assert aws["cost"]["request"] == "0.00000980"
    assert aws["cost"]["egress"] == "1.08000000"
    assert aws["cost"]["total"] == "1.38000980"
    assert aws["eligible"] is True and aws["rejection_reasons"] == []
    assert data["dry_run"] is True
    assert any("ILLUSTRATIVE" in w for w in data["warnings"])


def test_compare_reports_the_winner_under_every_preset_and_the_cheapest_baseline(client, as_role):
    data = client.post("/selection/compare", json=BODY, headers=as_role("USER")).json()
    assert set(data["presets"]) == set(PRESETS)
    for entry in data["presets"].values():
        assert entry["winner"] is not None and len(entry["ranking"]) == 6
        assert entry["ranking"][0]["rank"] == 1
    cheapest = data["baselines"]["cheapest_only"]["selected"]
    assert cheapest["provider_code"] in {"AWS", "AZURE", "GCP"}
    assert data["baselines"]["cheapest_only"]["cost"]["total"] is not None


def test_compare_shows_why_a_region_is_ineligible(client, seeded, as_role):
    seeded.state["regions"][2] = replace(seeded.state["regions"][2], health_status="DOWN")
    data = client.post("/selection/compare", json=BODY, headers=as_role("USER")).json()
    down = next(
        c
        for c in data["candidates"]
        if c["region_code"] == "us-east-1" and c["provider_code"] == "AWS"
    )
    assert down["eligible"] is False and "currently DOWN" in down["rejection_reasons"][0]
    assert down["cost"] is not None  # still shown for information


# ---- select ----------------------------------------------------------------------------------
def test_select_matches_the_engine_run_directly_on_the_same_data(client, seeded, as_role):
    data = client.post("/selection/select", json=BODY, headers=as_role("USER")).json()
    direct = engine.select(
        FileRequest(GIB, "MEDIUM", D(1), D(12), "HOT"),
        _FakeProviders(seeded.state).candidates("HOT"),
        PRESETS["BALANCED"], DEFAULT_POLICY, preset_name="BALANCED",
    )  # fmt: skip
    assert data["selected"]["composite"] == format(direct.selected.composite, "f")
    assert [f"{r['provider_code']}/{r['region_code']}" for r in data["ranking"]] == [
        f"{r.candidate.provider_code}/{r.candidate.region_code}" for r in direct.ranked
    ]
    assert data["explanation"] == direct.explanation


def test_select_returns_the_full_explanation_payload(client, as_role):
    data = client.post("/selection/select", json=BODY, headers=as_role("USER")).json()
    assert data["preset"] == "BALANCED" and data["dry_run"] is True
    assert set(data["weights"]) == {
        "storage_cost",
        "transfer_cost",
        "latency",
        "reliability",
        "security",
    }
    top = data["selected"]
    assert set(top) >= {"raw", "normalised", "score", "contribution", "composite", "cost", "rank"}
    assert top["rank"] == 1 and data["explanation"].startswith("Selected ")
    assert data["cost_formula"][0] == "size_gb = file_size_bytes / 2^30"
    assert data["tie_break_order"][1] == "higher security score"
    assert data["advantages_vs_runner_up"] or data["disadvantages_vs_runner_up"]


def test_high_security_excludes_regions_below_the_minimum_with_reasons(client, as_role):
    data = client.post(
        "/selection/select", json={**BODY, "security_level": "HIGH"}, headers=as_role("USER")
    ).json()
    rejected = {f"{r['provider_code']}/{r['region_code']}": r["reasons"] for r in data["rejected"]}
    assert set(rejected) == {"GCP/asia-south1", "GCP/us-central1"}
    assert "security score 9.1 is below the HIGH minimum 9.2" in rejected["GCP/asia-south1"][0]
    assert all(r["provider_code"] != "GCP" for r in data["ranking"])
    assert "Excluded Google Cloud (asia-south1)" in data["explanation"]


def test_an_oversize_file_only_fits_the_provider_with_a_big_enough_limit(client, as_role):
    body = {**BODY, "file_size_bytes": 6 * 1024**4}
    data = client.post("/selection/select", json=body, headers=as_role("USER")).json()
    assert {r["provider_code"] for r in data["ranking"]} == {"AZURE"}
    assert all("exceeds the provider limit" in r["reasons"][0] for r in data["rejected"])
    assert len(data["rejected"]) == 4


def test_nothing_eligible_returns_selected_null_and_the_reasons(client, seeded, as_role):
    for rid in list(seeded.state["regions"]):
        region = seeded.state["regions"][rid]
        seeded.state["regions"][rid] = replace(region, is_active=False)
    data = client.post("/selection/select", json=BODY, headers=as_role("USER")).json()
    assert data["selected"] is None and data["ranking"] == [] and len(data["rejected"]) == 6
    assert data["explanation"].startswith("No provider-region can store this")


def test_different_presets_through_the_api_can_choose_different_providers(client, as_role):
    headers = as_role("USER")
    winners = {}
    for preset in PRESETS:
        data = client.post(
            "/selection/select", json={**BODY, "preset": preset}, headers=headers
        ).json()
        winners[preset] = (data["selected"]["provider_code"], data["selected"]["region_code"])
    assert len(set(winners.values())) >= 2, winners
    # latency-heavy weights must pick an Indian region (about 40 ms) over a US one (about 220 ms)
    assert winners["PERFORMANCE"][1] in {"ap-south-1", "Central India", "asia-south1"}


def test_preset_name_is_case_insensitive_and_unknown_presets_are_422(client, as_role):
    headers = as_role("USER")
    assert (
        client.post("/selection/select", json={**BODY, "preset": "secure"}, headers=headers).json()[
            "preset"
        ]
        == "SECURE"
    )
    bad = client.post("/selection/select", json={**BODY, "preset": "NOPE"}, headers=headers)
    assert bad.status_code == 422 and "unknown preset 'NOPE'" in bad.json()["detail"]


def test_per_request_weight_override_is_normalised_and_says_so(client, as_role):
    data = client.post(
        "/selection/select", json={**BODY, "weights": {"latency": "0.5"}}, headers=as_role("USER")
    ).json()
    assert data["weights_note"] and "summed to 1.3" in data["weights_note"]
    assert data["weights_note"] in data["warnings"]
    assert sum(D(v) for v in data["weights"].values()) == D(1)
    assert "with overrides" in data["explanation"]


@pytest.mark.parametrize(
    "weights",
    [{"latency": "-1"}, {"latency": "abc"}, {"colour": "0.5"},
     {"storage_cost": "0", "transfer_cost": "0", "latency": "0", "reliability": "0", "security": "0"}],
)  # fmt: skip
def test_invalid_weights_are_422(client, as_role, weights):
    assert (
        client.post(
            "/selection/select", json={**BODY, "weights": weights}, headers=as_role("USER")
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "bad",
    [{"file_size_bytes": -1}, {"security_level": "TOP"}, {"tier": "FREEZER"},
     {"expected_accesses_per_month": "-1"}, {"retention_months": "0"}, {"file_size_bytes": "big"}],
)  # fmt: skip
def test_invalid_file_descriptions_are_422(client, as_role, bad):
    assert (
        client.post("/selection/select", json={**BODY, **bad}, headers=as_role("USER")).status_code
        == 422
    )


def test_cold_tier_uses_cold_prices_and_minimum_billing_days(client, as_role):
    body = {**BODY, "tier": "COLD", "retention_months": "1", "expected_accesses_per_month": "0"}
    data = client.post("/selection/compare", json=body, headers=as_role("USER")).json()
    aws = data["candidates"][0]
    # COLD min 90 days => 3 billed months; 1 GiB * 0.005 * 3 = 0.015
    assert aws["cost"]["billed_months"] == "3" and aws["cost"]["storage"] == "0.01500000"


def test_fixed_provider_baseline_is_reported_on_request(client, as_role):
    headers = as_role("USER")
    data = client.post(
        "/selection/select", json={**BODY, "fixed_provider_code": "gcp"}, headers=headers
    ).json()
    assert data["baselines"]["fixed_provider"]["selected"]["provider_code"] == "GCP"
    high = client.post(
        "/selection/select",
        json={**BODY, "security_level": "HIGH", "fixed_provider_code": "GCP"},
        headers=headers,
    ).json()
    assert high["baselines"]["fixed_provider"]["selected"] is None
    assert "cannot store this file" in high["baselines"]["fixed_provider"]["note"]


def test_dry_runs_change_nothing(client, seeded, as_role):
    headers = as_role("USER")
    providers, regions, pricing = (
        dict(seeded.state["providers"]),
        dict(seeded.state["regions"]),
        dict(seeded.state["pricing"]),
    )
    audit_before = len(seeded.audit)
    for path in ("/selection/compare", "/selection/select"):
        client.post(path, json=BODY, headers=headers)
    assert (seeded.state["providers"], seeded.state["regions"], seeded.state["pricing"]) == (
        providers,
        regions,
        pricing,
    )
    assert len(seeded.audit) == audit_before


def test_disabling_a_provider_in_the_registry_removes_it_from_selection(client, seeded, as_role):
    admin, user = as_role("ADMIN"), as_role("USER")
    client.patch("/providers/1", json={"is_active": False}, headers=admin)
    data = client.post("/selection/select", json=BODY, headers=user).json()
    assert all(r["provider_code"] != "AWS" for r in data["ranking"])
    assert {r["provider_code"] for r in data["rejected"]} == {"AWS"}
    assert all("provider is disabled" in r["reasons"] for r in data["rejected"])
