r"""Integration-test setup. Everything here needs a live Oracle database (you run these yourself).

    $env:RUN_INTEGRATION = "1"     # the ORACLE_* and JWT_SECRET values come from .env
    .venv\Scripts\python.exe -m pytest tests/integration -v

Without RUN_INTEGRATION=1 every test in this folder is skipped, so a normal `pytest` run never
needs Oracle. Side effects: test users itest_*@example.test (set DISABLED at the end) and
audit_events rows (append-only). sql/cleanup_integration_tests.sql removes them. Provider rows
registered by the tests (code ITEST*) are deleted at the end of the run.
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from app.config import load_settings
from app.db.pool import OracleDatabase
from app.main import create_app
from app.storage.simulated import SimulatedStorageRegistry
from tests.fakes import FaultyRegistry

load_dotenv()


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="set RUN_INTEGRATION=1 to run tests against Oracle")
    for item in items:
        if "tests/integration" in item.nodeid.replace("\\", "/"):
            item.add_marker(skip)
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def database():
    db = OracleDatabase.from_settings(load_settings())
    yield db
    with db.transaction() as session:
        cursor = session.connection.cursor()
        cursor.execute(
            r"UPDATE users SET account_status = 'DISABLED' WHERE email LIKE 'itest\_%' ESCAPE '\'"
        )
        # remove providers registered by the tests (regions and pricing first; audit rows keep
        # only numeric ids, so nothing references these providers)
        cursor.execute(
            "DELETE FROM performance_measurements WHERE region_id IN "
            "(SELECT r.region_id FROM provider_regions r JOIN cloud_providers p "
            "ON p.provider_id = r.provider_id WHERE p.provider_code LIKE 'ITEST%')"
        )
        cursor.execute(
            "DELETE FROM provider_regions WHERE provider_id IN "
            "(SELECT provider_id FROM cloud_providers WHERE provider_code LIKE 'ITEST%')"
        )
        cursor.execute("DELETE FROM cloud_providers WHERE provider_code LIKE 'ITEST%'")
    db.close()


@pytest.fixture(scope="session")
def storage(tmp_path_factory):
    """SIMULATED storage in a temporary folder (deleted by pytest), with failure switches."""
    return FaultyRegistry(SimulatedStorageRegistry(tmp_path_factory.mktemp("sim")))


@pytest.fixture(scope="session")
def client(database, storage):
    app = create_app(database=database, settings=load_settings(), storage=storage)
    with TestClient(app) as c:
        yield c
