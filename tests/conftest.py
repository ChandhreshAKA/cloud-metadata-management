"""Shared fixtures. Unit tests use FakeDatabase; integration tests build their own real app."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.config import Settings
from app.main import create_app
from tests.fakes import FakeDatabase
from tests.registry_data import seed_registry

PASSWORD = "correct-horse-battery"  # noqa: S105  (test value)


@pytest.fixture(scope="session")
def password_hash() -> str:
    """Argon2 is deliberately slow; hash once per test session and reuse."""
    return hash_password(PASSWORD)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        oracle_dsn="unused",
        oracle_user="unused",
        oracle_password="unused",
        jwt_secret="unit-test-secret-" + "x" * 40,
        jwt_expiry_minutes=30,
        app_env="test",
    )


@pytest.fixture
def db() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def client(db: FakeDatabase, settings: Settings):
    with TestClient(create_app(database=db, settings=settings)) as test_client:
        yield test_client


@pytest.fixture
def make_user(db: FakeDatabase, password_hash: str):
    """Create a user directly in the fake DB and return (user_id, email)."""

    def _make(role: str = "USER", status: str = "ACTIVE", email: str | None = None):
        address = email or f"{role.lower()}{len(db.state['users']) + 1}@example.com"
        return db.add_user(address, password_hash, role=role, status=status), address

    return _make


@pytest.fixture
def login(client: TestClient):
    """Log in through the API and return the Authorization header dict."""

    def _login(email: str, password: str = PASSWORD) -> dict[str, str]:
        response = client.post("/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _login


@pytest.fixture
def seeded(db: FakeDatabase) -> FakeDatabase:
    """The fake database loaded with the Chunk 1 provider reference data (illustrative values)."""
    seed_registry(db)
    return db


@pytest.fixture
def as_role(client, seeded, make_user, login):
    """Return Authorization headers for a fresh user of the given role."""

    def _as(role: str) -> dict[str, str]:
        _, email = make_user(role=role)
        return login(email)

    return _as
