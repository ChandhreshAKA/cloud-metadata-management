"""Provider registry endpoints (FakeDatabase): who may do what, validation, audit, atomicity."""

import pytest

from app.db.errors import DatabaseError

NEW_PROVIDER = {"provider_code": "OCI", "provider_name": "Oracle Cloud", "max_object_bytes": 10**12}
NEW_REGION = {
    "region_code": "ap-hyderabad-1", "bucket_name": "oci-hyd", "reliability_score": "9.40",
    "security_score": "9.50", "egress_usd_per_gb": "0.0085",
}  # fmt: skip
PRICE = {
    "native_class_name": "STANDARD", "storage_usd_per_gb_month": "0.0255",
    "retrieval_usd_per_gb": "0", "put_usd_per_1k": "0.0050", "get_usd_per_1k": "0.0004",
}  # fmt: skip

WRITES = [
    ("post", "/providers", NEW_PROVIDER),
    ("patch", "/providers/1", {"is_active": False}),
    ("post", "/providers/1/regions", NEW_REGION),
    ("patch", "/regions/1", {"health_status": "DOWN"}),
    ("put", "/regions/1/pricing/HOT", PRICE),
]


# ---- reads: every role ------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["USER", "ADMIN", "AUDITOR"])
def test_every_role_can_read_the_registry(client, as_role, role):
    response = client.get("/providers", headers=as_role(role))
    assert response.status_code == 200
    providers = response.json()
    assert [p["provider_code"] for p in providers] == ["AWS", "AZURE", "GCP"]
    aws = providers[0]
    assert aws["max_object_bytes"] == 5 * 1024**4 and aws["is_active"] is True
    assert [r["region_code"] for r in aws["regions"]] == ["ap-south-1", "us-east-1"]
    region = aws["regions"][0]
    assert region["health_status"] == "UP" and region["is_illustrative"] is True
    assert [p["tier_code"] for p in region["pricing"]] == ["ARCHIVE", "COLD", "COOL", "HOT"]


def test_reading_requires_a_token(client, seeded):
    assert client.get("/providers").status_code == 401


# ---- writes: ADMIN only -----------------------------------------------------------------------
@pytest.mark.parametrize(("method", "path", "body"), WRITES)
@pytest.mark.parametrize("role", ["USER", "AUDITOR"])
def test_non_admins_cannot_write_and_the_denial_is_audited(
    client, seeded, as_role, method, path, body, role
):
    headers = as_role(role)
    before = (
        dict(seeded.state["providers"]),
        dict(seeded.state["regions"]),
        dict(seeded.state["pricing"]),
    )
    response = getattr(client, method)(path, json=body, headers=headers)
    assert response.status_code == 403
    assert (
        dict(seeded.state["providers"]),
        dict(seeded.state["regions"]),
        dict(seeded.state["pricing"]),
    ) == before
    [event] = seeded.audit_of("ACCESS_DENIED")
    assert event["details"]["required"] == "provider:manage"


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
def test_writes_need_a_token(client, seeded, method, path, body):
    assert getattr(client, method)(path, json=body).status_code == 401


# ---- providers --------------------------------------------------------------------------------
def test_admin_registers_a_provider_and_it_is_audited(client, seeded, as_role):
    response = client.post("/providers", json=NEW_PROVIDER, headers=as_role("ADMIN"))
    assert response.status_code == 201
    assert response.json()["provider_code"] == "OCI" and response.json()["is_active"] is True
    [event] = seeded.audit_of("CONFIG_CHANGED")
    assert event["details"]["action"] == "registered" and event["target_type"] == "CONFIG"
    assert event["actor_user_id"] is not None and event["outcome"] == "SUCCESS"


def test_duplicate_provider_code_and_name_are_clean_409s(client, seeded, as_role):
    headers = as_role("ADMIN")
    dup_code = client.post(
        "/providers", json={**NEW_PROVIDER, "provider_code": "AWS"}, headers=headers
    )
    dup_name = client.post(
        "/providers", json={**NEW_PROVIDER, "provider_name": "Azure"}, headers=headers
    )
    assert dup_code.status_code == dup_name.status_code == 409
    assert dup_code.json() == {"detail": "A provider with that code already exists"}
    assert dup_name.json() == {"detail": "A provider with that name already exists"}
    assert len(seeded.state["providers"]) == 3


@pytest.mark.parametrize(
    "bad",
    [
        {**NEW_PROVIDER, "provider_code": "lower"},
        {**NEW_PROVIDER, "provider_code": "X"},
        {**NEW_PROVIDER, "provider_name": ""},
        {**NEW_PROVIDER, "max_object_bytes": 0},
        {**NEW_PROVIDER, "max_object_bytes": -5},
        {"provider_code": "OCI"},
    ],
)
def test_invalid_provider_input_is_422(client, seeded, as_role, bad):
    assert client.post("/providers", json=bad, headers=as_role("ADMIN")).status_code == 422


def test_disabling_a_provider_is_a_status_change_event(client, seeded, as_role):
    response = client.patch("/providers/1", json={"is_active": False}, headers=as_role("ADMIN"))
    assert response.status_code == 200 and response.json()["is_active"] is False
    [event] = seeded.audit_of("PROVIDER_STATUS_CHANGED")
    assert event["details"] == {"entity": "provider", "changes": {"is_active": [True, False]}}
    assert seeded.audit_of("CONFIG_CHANGED") == []


def test_rename_and_disable_together_write_both_kinds_of_event(client, seeded, as_role):
    client.patch("/providers/2", json={"provider_name": "Microsoft Azure", "is_active": False},
                 headers=as_role("ADMIN"))  # fmt: skip
    assert len(seeded.audit_of("PROVIDER_STATUS_CHANGED")) == 1
    [config] = seeded.audit_of("CONFIG_CHANGED")
    assert config["details"]["changes"] == {"provider_name": ["Azure", "Microsoft Azure"]}


def test_a_patch_that_changes_nothing_writes_no_audit_event(client, seeded, as_role):
    client.patch("/providers/1", json={"is_active": True}, headers=as_role("ADMIN"))
    assert (
        seeded.audit_of("PROVIDER_STATUS_CHANGED") == [] and seeded.audit_of("CONFIG_CHANGED") == []
    )


def test_patch_needs_a_field_and_a_known_provider(client, seeded, as_role):
    headers = as_role("ADMIN")
    assert client.patch("/providers/1", json={}, headers=headers).status_code == 422
    assert (
        client.patch("/providers/1", json={"is_active": None}, headers=headers).status_code == 422
    )
    missing = client.patch("/providers/99", json={"is_active": False}, headers=headers)
    assert missing.status_code == 404 and missing.json() == {"detail": "provider 99 not found"}


# ---- regions ----------------------------------------------------------------------------------
def test_admin_registers_a_region_with_default_up_status(client, seeded, as_role):
    response = client.post("/providers/1/regions", json=NEW_REGION, headers=as_role("ADMIN"))
    assert response.status_code == 201
    body = response.json()
    assert body["health_status"] == "UP" and body["is_active"] is True
    assert body["reliability_score"] == "9.40" and body["is_illustrative"] is True
    assert seeded.audit_of("CONFIG_CHANGED")[0]["target_type"] == "PROVIDER_REGION"


def test_region_registration_errors(client, seeded, as_role):
    headers = as_role("ADMIN")
    assert client.post("/providers/99/regions", json=NEW_REGION, headers=headers).status_code == 404
    dup = client.post(
        "/providers/1/regions", json={**NEW_REGION, "region_code": "ap-south-1"}, headers=headers
    )
    assert dup.status_code == 409 and "already has a region" in dup.json()["detail"]
    for bad in ({"reliability_score": "11"}, {"security_score": "-1"}, {"egress_usd_per_gb": "-0.1"},
                {"reliability_score": "9.123"}, {"region_code": ""}):  # fmt: skip
        assert (
            client.post(
                "/providers/1/regions", json={**NEW_REGION, **bad}, headers=headers
            ).status_code
            == 422
        )


def test_marking_a_region_down_is_audited_as_a_status_change(client, seeded, as_role):
    response = client.patch("/regions/1", json={"health_status": "DOWN"}, headers=as_role("ADMIN"))
    assert response.status_code == 200 and response.json()["health_status"] == "DOWN"
    [event] = seeded.audit_of("PROVIDER_STATUS_CHANGED")
    assert event["target_type"] == "PROVIDER_REGION" and event["target_id"] == 1
    assert event["details"]["changes"] == {"health_status": ["UP", "DOWN"]}


def test_changing_scores_is_a_config_change_recording_old_and_new_values(client, seeded, as_role):
    client.patch(
        "/regions/1",
        json={"reliability_score": "9.10", "security_score": "9.40"},
        headers=as_role("ADMIN"),
    )
    [event] = seeded.audit_of("CONFIG_CHANGED")
    assert event["details"]["changes"] == {
        "reliability_score": ["9.50", "9.10"], "security_score": ["9.20", "9.40"]
    }  # fmt: skip


def test_region_patch_validation(client, seeded, as_role):
    headers = as_role("ADMIN")
    assert (
        client.patch("/regions/1", json={"health_status": "SLEEPING"}, headers=headers).status_code
        == 422
    )
    assert (
        client.patch("/regions/1", json={"reliability_score": "10.5"}, headers=headers).status_code
        == 422
    )
    assert (
        client.patch("/regions/99", json={"is_active": False}, headers=headers).status_code == 404
    )


# ---- pricing ----------------------------------------------------------------------------------
def test_admin_replaces_a_price_and_the_audit_event_lists_what_changed(client, seeded, as_role):
    response = client.put("/regions/1/pricing/HOT", json=PRICE, headers=as_role("ADMIN"))
    assert response.status_code == 200
    stored = seeded.state["pricing"][(1, "HOT")]
    assert str(stored.storage_usd_per_gb_month) == "0.0255"
    [event] = seeded.audit_of("CONFIG_CHANGED")
    assert event["details"]["changes"] == {"storage_usd_per_gb_month": ["0.025000", "0.0255"]}


def test_admin_creates_a_new_tier_price(client, seeded, as_role):
    del seeded.state["pricing"][(1, "COOL")]
    client.put(
        "/regions/1/pricing/COOL",
        json={**PRICE, "native_class_name": "STANDARD_IA"},
        headers=as_role("ADMIN"),
    )
    assert (1, "COOL") in seeded.state["pricing"]
    assert seeded.audit_of("CONFIG_CHANGED")[0]["details"]["action"] == "created"


def test_pricing_errors(client, seeded, as_role):
    headers = as_role("ADMIN")
    assert client.put("/regions/1/pricing/FREEZER", json=PRICE, headers=headers).status_code == 422
    assert client.put("/regions/99/pricing/HOT", json=PRICE, headers=headers).status_code == 404
    assert (
        client.put(
            "/regions/1/pricing/HOT", json={**PRICE, "put_usd_per_1k": "-1"}, headers=headers
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/regions/1/pricing/HOT",
            json={**PRICE, "storage_usd_per_gb_month": "abc"},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/regions/1/pricing/HOT", json={"native_class_name": "X"}, headers=headers
        ).status_code
        == 422
    )


# ---- atomicity --------------------------------------------------------------------------------
def test_a_change_and_its_audit_event_succeed_or_fail_together(client, seeded, as_role):
    headers = as_role("ADMIN")
    seeded.fail_audit_with = DatabaseError("audit unavailable")
    response = client.patch("/providers/1", json={"is_active": False}, headers=headers)
    assert response.status_code == 500
    assert seeded.state["providers"][1].is_active is True  # rolled back with the failed audit
    assert client.post("/providers", json=NEW_PROVIDER, headers=headers).status_code == 500
    assert len(seeded.state["providers"]) == 3


def test_reusing_a_class_name_for_another_tier_is_a_clean_409(client, seeded, as_role):
    """Schema rule uq_pricing_native: one native class name per region (seen on real Oracle)."""
    response = client.put(
        "/regions/1/pricing/COOL", json={**PRICE, "native_class_name": "STANDARD"},
        headers=as_role("ADMIN"),
    )  # fmt: skip
    assert response.status_code == 409
    assert response.json() == {
        "detail": "That region already has a price row using that class name"
    }
