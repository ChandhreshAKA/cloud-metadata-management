"""INTEGRATION TESTS (chunk 3): provider registry and selection against a real Oracle database.

Expectations about the seeded providers come from sql/seed_data.sql (illustrative values). If you
changed those rows by hand, the hand-checked numbers below will differ. See
tests/integration/conftest.py for how to run these and what they leave behind.
"""

from __future__ import annotations

import uuid
from decimal import Decimal as D

import pytest

from app.db.errors import CheckViolationError, UniqueViolationError
from app.selection import engine
from app.selection.models import FileRequest
from app.selection.presets import PRESETS
from tests.integration.helpers import register_and_login, scalar

pytestmark = pytest.mark.integration

GIB = 1024**3
BODY = {
    "file_size_bytes": GIB,
    "security_level": "MEDIUM",
    "expected_accesses_per_month": "1",
    "retention_months": "12",
    "tier": "HOT",
}
SEEDED = {"AWS", "AZURE", "GCP"}


@pytest.fixture(scope="module")
def admin(client, database):
    return register_and_login(client, role="ADMIN", database=database)[1]


@pytest.fixture(scope="module")
def user(client, database):
    return register_and_login(client, database=database)[1]


@pytest.fixture(scope="module")
def itest_region(client, admin):
    """A provider and region registered through the API (removed when the test run ends)."""
    code = f"ITEST{uuid.uuid4().hex[:8].upper()}"
    provider = client.post(
        "/providers",
        json={"provider_code": code, "provider_name": f"Itest {code}", "max_object_bytes": 10**12},
        headers=admin,
    )
    assert provider.status_code == 201, provider.text
    region = client.post(
        f"/providers/{provider.json()['provider_id']}/regions",
        json={
            "region_code": "itest-1", "bucket_name": f"bucket-{code.lower()}",
            "reliability_score": "9.80", "security_score": "9.90", "egress_usd_per_gb": "0.0100",
        },
        headers=admin,
    )  # fmt: skip
    assert region.status_code == 201, region.text
    return {"code": code, "provider_id": provider.json()["provider_id"], **region.json()}


def _find(candidates, provider_code, region_code):
    return next(
        c for c in candidates if c.provider_code == provider_code and c.region_code == region_code
    )


# ---- the read model, straight from Oracle ----------------------------------------------------
def test_candidates_come_back_as_exact_decimals_not_floats(database):
    with database.transaction() as session:
        candidates = session.providers.candidates("HOT")
    aws = _find(candidates, "AWS", "ap-south-1")
    assert isinstance(aws.reliability_score, D) and aws.reliability_score == D("9.50")
    assert aws.security_score == D("9.20") and aws.egress_usd_per_gb == D("0.09")
    assert aws.pricing.storage_usd_per_gb_month == D("0.025")
    assert isinstance(aws.pricing.put_usd_per_1k, D) and aws.max_object_bytes == 5 * 1024**4
    assert aws.latency_ms == D("40")  # one seeded GET probe of 40 ms
    assert aws.provider_active and aws.region_active and aws.health_status == "UP"
    seeded = [c for c in candidates if c.provider_code in SEEDED]
    assert len(seeded) == 6 and all(c.pricing and c.latency_ms for c in seeded)


def test_an_unpriced_tier_is_reported_as_no_pricing_not_an_error(database):
    with database.transaction() as session:
        cold = session.providers.candidates("COLD")
    assert all(c.pricing is not None for c in cold if c.provider_code in SEEDED)
    assert _find(cold, "AWS", "ap-south-1").pricing.min_storage_days == 90


def test_weight_profiles_and_policy_come_from_the_database(database):
    with database.transaction() as session:
        profiles, default = session.providers.weight_profiles()
        policy = session.providers.policy()
    assert default == "BALANCED"
    for name, weights in PRESETS.items():
        assert profiles[name] == weights
    assert policy.min_security["HIGH"] == D("9.2")
    assert policy.min_reliability["HIGH"] == D("9.4")  # default, or sql/seed_selection_config.sql


# ---- reading the registry --------------------------------------------------------------------
def test_every_role_can_list_the_registry(client, user):
    providers = client.get("/providers", headers=user).json()
    by_code = {p["provider_code"]: p for p in providers}
    assert SEEDED <= set(by_code)
    aws = by_code["AWS"]
    assert [r["region_code"] for r in aws["regions"]] == ["ap-south-1", "us-east-1"]
    assert {p["tier_code"] for p in aws["regions"][0]["pricing"]} == {
        "HOT",
        "COOL",
        "COLD",
        "ARCHIVE",
    }
    assert D(aws["regions"][0]["reliability_score"]) == D("9.50")


# ---- selection against Oracle data -----------------------------------------------------------
def test_compare_costs_match_the_hand_calculation_on_seeded_prices(client, user):
    data = client.post("/selection/compare", json=BODY, headers=user).json()
    aws = next(
        c
        for c in data["candidates"]
        if c["provider_code"] == "AWS" and c["region_code"] == "ap-south-1"
    )
    # 1 GiB, 12 months, 12 reads: storage 0.025*12, request 0.005/1000 + 0.0004/1000*12, egress 0.09*12
    assert aws["cost"]["storage"] == "0.30000000"
    assert aws["cost"]["request"] == "0.00000980"
    assert aws["cost"]["egress"] == "1.08000000"
    assert aws["cost"]["total"] == "1.38000980"


def test_select_through_the_api_equals_the_engine_run_on_the_same_oracle_data(
    client, database, user
):
    api = client.post("/selection/select", json=BODY, headers=user).json()
    with database.transaction() as session:
        candidates = session.providers.candidates("HOT")
        policy = session.providers.policy()
    direct = engine.select(
        FileRequest(GIB, "MEDIUM", D(1), D(12), "HOT"), candidates, PRESETS["BALANCED"], policy,
        preset_name="BALANCED",
    )  # fmt: skip
    assert api["selected"]["composite"] == format(direct.selected.composite, "f")
    assert [(r["provider_code"], r["region_code"]) for r in api["ranking"]] == [
        (r.candidate.provider_code, r.candidate.region_code) for r in direct.ranked
    ]
    assert api["explanation"] == direct.explanation


def test_high_security_file_excludes_google_cloud_with_reasons(client, user):
    data = client.post(
        "/selection/select", json={**BODY, "security_level": "HIGH"}, headers=user
    ).json()
    rejected = {
        (r["provider_code"], r["region_code"]): r["reasons"]
        for r in data["rejected"]
        if r["provider_code"] in SEEDED
    }
    assert set(rejected) == {("GCP", "asia-south1"), ("GCP", "us-central1")}
    assert "security score 9.1 is below the HIGH minimum 9.2" in rejected[("GCP", "asia-south1")][0]


def test_oversize_file_is_filtered_by_provider_limit(client, user):
    data = client.post(
        "/selection/select", json={**BODY, "file_size_bytes": 6 * 1024**4}, headers=user
    ).json()
    assert {r["provider_code"] for r in data["ranking"] if r["provider_code"] in SEEDED} == {
        "AZURE"
    }


def test_override_weights_are_normalised_and_reported(client, user):
    data = client.post(
        "/selection/select", json={**BODY, "weights": {"latency": "0.5"}}, headers=user
    ).json()
    assert data["weights_note"] and "summed to 1.3" in data["weights_note"]


def test_dry_runs_write_nothing_to_oracle(client, database, user):
    audit_before = scalar(database, "SELECT COUNT(*) FROM audit_events")
    decisions_before = scalar(database, "SELECT COUNT(*) FROM selection_decisions")
    for path in ("/selection/compare", "/selection/select"):
        assert client.post(path, json=BODY, headers=user).status_code == 200
    assert scalar(database, "SELECT COUNT(*) FROM audit_events") == audit_before
    assert scalar(database, "SELECT COUNT(*) FROM selection_decisions") == decisions_before


# ---- admin-only writes -----------------------------------------------------------------------
def test_a_normal_user_cannot_register_a_provider_and_the_denial_is_audited(client, database, user):
    code = f"ITEST{uuid.uuid4().hex[:8].upper()}"
    response = client.post(
        "/providers",
        json={"provider_code": code, "provider_name": code, "max_object_bytes": 1000},
        headers=user,
    )
    assert response.status_code == 403
    assert (
        scalar(
            database, "SELECT COUNT(*) FROM cloud_providers WHERE provider_code = :c", {"c": code}
        )
        == 0
    )
    user_id = client.get("/auth/me", headers=user).json()["user_id"]
    assert (
        scalar(
            database,
            "SELECT COUNT(*) FROM audit_events WHERE actor_user_id = :id AND event_type = 'ACCESS_DENIED' "
            "AND details_json LIKE '%provider:manage%'",
            {"id": user_id},
        )
        >= 1
    )


def test_new_region_needs_pricing_and_a_probe_to_become_eligible_and_disabling_removes_it(
    client, database, admin, user, itest_region
):
    region_id, provider_id = itest_region["region_id"], itest_region["provider_id"]
    first = client.post("/selection/select", json=BODY, headers=user).json()
    why = " ".join(next(r["reasons"] for r in first["rejected"] if r["region_id"] == region_id))
    assert "no HOT pricing is registered" in why and "no latency measurement" in why

    price = {
        "native_class_name": "STANDARD", "storage_usd_per_gb_month": "0.0100",
        "retrieval_usd_per_gb": "0", "put_usd_per_1k": "0.0050", "get_usd_per_1k": "0.0004",
    }  # fmt: skip
    assert (
        client.put(f"/regions/{region_id}/pricing/HOT", json=price, headers=admin).status_code
        == 200
    )
    with database.transaction() as session:  # a simulated probe, like the seeded ones
        session.connection.cursor().execute(
            "INSERT INTO performance_measurements (region_id, operation, latency_ms, success, source) "
            "VALUES (:r, 'GET', 5, 'Y', 'SIMULATED')",
            {"r": region_id},
        )
    ranked = client.post("/selection/select", json=BODY, headers=user).json()["ranking"]
    assert region_id in {r["region_id"] for r in ranked}

    assert (
        client.patch(
            f"/providers/{provider_id}", json={"is_active": False}, headers=admin
        ).status_code
        == 200
    )
    after = client.post("/selection/select", json=BODY, headers=user).json()
    assert region_id not in {r["region_id"] for r in after["ranking"]}
    assert any(
        r["region_id"] == region_id and "provider is disabled" in r["reasons"]
        for r in after["rejected"]
    )
    client.patch(f"/providers/{provider_id}", json={"is_active": True}, headers=admin)


def test_pricing_upsert_updates_in_place_instead_of_duplicating(
    client, database, admin, itest_region
):
    region_id = itest_region["region_id"]
    price = {
        "native_class_name": "STANDARD_IA", "storage_usd_per_gb_month": "0.0123",
        "retrieval_usd_per_gb": "0", "put_usd_per_1k": "0.0050", "get_usd_per_1k": "0.0004",
    }  # fmt: skip
    for _ in range(2):
        assert (
            client.put(f"/regions/{region_id}/pricing/COOL", json=price, headers=admin).status_code
            == 200
        )
    binds = {"r": region_id}
    assert (
        scalar(
            database,
            "SELECT COUNT(*) FROM provider_pricing WHERE region_id = :r AND tier_code = 'COOL'",
            binds,
        )
        == 1
    )
    assert scalar(
        database,
        "SELECT storage_usd_per_gb_month FROM provider_pricing WHERE region_id = :r AND tier_code = 'COOL'",
        binds,
    ) == D("0.0123")


def test_marking_a_region_down_updates_status_time_and_writes_a_status_event(
    client, database, admin, itest_region
):
    region_id = itest_region["region_id"]
    sql = "SELECT status_changed_at FROM provider_regions WHERE region_id = :r"
    before = scalar(database, sql, {"r": region_id})
    assert (
        client.patch(
            f"/regions/{region_id}", json={"health_status": "DOWN"}, headers=admin
        ).status_code
        == 200
    )
    assert scalar(database, sql, {"r": region_id}) > before
    events = scalar(
        database,
        "SELECT COUNT(*) FROM audit_events WHERE event_type = 'PROVIDER_STATUS_CHANGED' "
        "AND target_type = 'PROVIDER_REGION' AND target_id = :r",
        {"r": region_id},
    )
    assert events == 1
    client.patch(f"/regions/{region_id}", json={"health_status": "UP"}, headers=admin)


def test_oracle_constraints_on_registry_writes_surface_as_domain_errors(database, itest_region):
    with pytest.raises(CheckViolationError) as score, database.transaction() as session:
        session.providers.create_region(itest_region["provider_id"], "bad-score", "bad-score-bucket",
                                        D("11"), D("9"), D("0.01"), True)  # fmt: skip
    assert score.value.constraint == "CK_REGIONS_RELIABILITY"
    with pytest.raises(UniqueViolationError) as dup, database.transaction() as session:
        session.providers.create_region(itest_region["provider_id"], "itest-1", "other-bucket",
                                        D("9"), D("9"), D("0.01"), True)  # fmt: skip
    assert dup.value.constraint == "UQ_REGIONS_PROVIDER_CODE"


def test_duplicate_provider_code_is_a_clean_409(client, admin, itest_region):
    response = client.post(
        "/providers",
        json={
            "provider_code": itest_region["code"],
            "provider_name": "Another",
            "max_object_bytes": 1000,
        },
        headers=admin,
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "A provider with that code already exists"}
