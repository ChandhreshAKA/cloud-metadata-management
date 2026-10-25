"""Registration, login and current-user through the real FastAPI app with a FakeDatabase.

These prove application logic. They do not prove Oracle compatibility (see tests/integration).
"""

from datetime import UTC, datetime, timedelta

from app.auth.passwords import verify_password
from app.auth.tokens import create_access_token
from tests.conftest import PASSWORD

NEW_USER = {
    "full_name": "Ada Lovelace",
    "email": "ada@example.com",
    "password": "a-fine-password-1",
}


# ---- register -------------------------------------------------------------------------------
def test_register_creates_a_user_role_with_a_hashed_password(client, db):
    response = client.post("/auth/register", json=NEW_USER)
    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "USER" and body["email"] == "ada@example.com"
    stored = db.state["users"][body["user_id"]]
    assert stored.password_hash != NEW_USER["password"]
    assert verify_password(stored.password_hash, NEW_USER["password"])
    assert "password" not in response.text


def test_register_cannot_choose_a_role(client, db):
    response = client.post("/auth/register", json={**NEW_USER, "role": "ADMIN"})
    assert response.status_code == 201
    assert db.state["users"][response.json()["user_id"]].role == "USER"


def test_register_normalises_email_case_and_whitespace(client):
    response = client.post("/auth/register", json={**NEW_USER, "email": "  Ada@Example.COM "})
    assert response.json()["email"] == "ada@example.com"


def test_register_writes_a_success_audit_event(client, db):
    user_id = client.post("/auth/register", json=NEW_USER).json()["user_id"]
    [event] = db.audit_of("USER_REGISTERED")
    assert event["outcome"] == "SUCCESS" and event["actor_user_id"] == user_id


def test_duplicate_email_is_rejected_cleanly_with_409(client, db):
    assert client.post("/auth/register", json=NEW_USER).status_code == 201
    again = client.post("/auth/register", json={**NEW_USER, "email": "ADA@example.com"})
    assert again.status_code == 409
    assert again.json() == {"detail": "That e-mail address is already registered"}
    assert "ORA-" not in again.text and "UQ_USERS" not in again.text
    assert len(db.state["users"]) == 1
    failures = [e for e in db.audit_of("USER_REGISTERED") if e["outcome"] == "FAILURE"]
    assert len(failures) == 1 and failures[0]["details"]["reason"] == "duplicate_email"


def test_register_validation_errors_do_not_echo_the_password(client):
    response = client.post("/auth/register", json={**NEW_USER, "password": "short"})
    assert response.status_code == 422
    assert "short" not in response.text.replace("too short", "").replace("String should have", "")
    assert response.json()["detail"] == "Invalid request"


def test_register_rejects_bad_email_blank_name_and_overlong_values(client):
    cases = [
        {**NEW_USER, "email": "not-an-email"},
        {**NEW_USER, "email": "a@b"},
        {**NEW_USER, "full_name": "   "},
        {**NEW_USER, "full_name": "n" * 101},
        {**NEW_USER, "password": "p" * 129},
        {"email": "x@example.com"},
    ]
    for payload in cases:
        assert client.post("/auth/register", json=payload).status_code == 422, payload


# ---- login ----------------------------------------------------------------------------------
def test_login_success_returns_a_bearer_token_and_audits_it(client, db, make_user):
    user_id, email = make_user()
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 1800
    assert body["access_token"].count(".") == 2
    assert db.state["last_login"][user_id] == 1
    [event] = db.audit_of("LOGIN_OK")
    assert event["actor_user_id"] == user_id and event["outcome"] == "SUCCESS"


def test_wrong_password_unknown_user_and_inactive_account_look_identical(client, db, make_user):
    _, email = make_user()
    _, locked_email = make_user(status="DISABLED")
    pending = db.add_user("pending@example.com", None, status="PENDING_RESET")

    wrong = client.post("/auth/login", json={"email": email, "password": "wrong-password-1"})
    unknown = client.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    inactive = client.post("/auth/login", json={"email": locked_email, "password": PASSWORD})
    no_pwd = client.post("/auth/login", json={"email": "pending@example.com", "password": PASSWORD})

    for response in (wrong, unknown, inactive, no_pwd):
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid e-mail or password"}
    assert pending  # the account exists but cannot log in


def test_failed_logins_are_audited_with_an_internal_reason(client, db, make_user):
    user_id, email = make_user()
    client.post("/auth/login", json={"email": email, "password": "wrong-password-1"})
    client.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    events = db.audit_of("LOGIN_FAILED")
    assert [e["details"]["reason"] for e in events] == ["bad_password", "unknown_user"]
    assert events[0]["actor_user_id"] == user_id and events[1]["actor_user_id"] is None
    assert all(e["outcome"] == "FAILURE" for e in events)
    assert "wrong-password-1" not in str(events)  # passwords are never written to the audit log


def test_audit_survives_the_failed_request(client, db):
    """The audit row is committed in its own transaction, not rolled back with the failure."""
    client.post("/auth/login", json={"email": "ghost@example.com", "password": PASSWORD})
    assert len(db.audit_of("LOGIN_FAILED")) == 1


def test_login_email_is_case_insensitive(client, make_user):
    _, email = make_user(email="mixed@example.com")
    ok = client.post("/auth/login", json={"email": "MIXED@Example.com", "password": PASSWORD})
    assert ok.status_code == 200


# ---- current user ---------------------------------------------------------------------------
def test_me_returns_the_logged_in_user(client, make_user, login):
    user_id, email = make_user(role="AUDITOR")
    response = client.get("/auth/me", headers=login(email))
    assert response.status_code == 200
    assert response.json() == {
        "user_id": user_id,
        "full_name": "Test User",
        "email": email,
        "role": "AUDITOR",
    }


def test_me_without_token_is_401_with_bearer_challenge(client):
    response = client.get("/auth/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_with_garbage_token_is_401(client):
    response = client.get("/auth/me", headers={"Authorization": "Bearer not.a.token"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid token"


def test_expired_token_is_401_token_expired(client, make_user, settings):
    user_id, _ = make_user()
    token = create_access_token(
        settings, user_id, "USER", now=datetime.now(UTC) - timedelta(hours=3), expires_minutes=30
    )
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Token expired"


def test_token_of_a_disabled_user_stops_working_immediately(client, db, make_user, login):
    user_id, email = make_user()
    headers = login(email)
    assert client.get("/auth/me", headers=headers).status_code == 200
    db.set_status(user_id, "DISABLED")
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_token_for_a_user_that_no_longer_exists_is_401(client, db, make_user, login):
    user_id, email = make_user()
    headers = login(email)
    del db.state["users"][user_id]
    assert client.get("/auth/me", headers=headers).status_code == 401
