import pytest

from app.config import ConfigError, load_settings

GOOD = {
    "ORACLE_DSN": "localhost:1521/FREEPDB1",
    "ORACLE_USER": "cloudmeta",
    "ORACLE_PASSWORD": "pw",
    "JWT_SECRET": "s" * 40,
    "MASTER_KEY": "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",  # bytes 0..31
}


def test_loads_defaults_from_a_valid_environment():
    s = load_settings(dict(GOOD))
    assert s.jwt_algorithm == "HS256"
    assert s.jwt_expiry_minutes == 30
    assert (s.db_pool_min, s.db_pool_max) == (1, 5)
    assert s.app_env == "dev"


@pytest.mark.parametrize(
    "missing", ["ORACLE_DSN", "ORACLE_USER", "ORACLE_PASSWORD", "JWT_SECRET", "MASTER_KEY"]
)
def test_missing_required_variable_names_the_variable(missing):
    env = {k: v for k, v in GOOD.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        load_settings(env)


def test_short_jwt_secret_is_rejected_without_printing_it():
    with pytest.raises(ConfigError) as err:
        load_settings({**GOOD, "JWT_SECRET": "short"})
    assert "short" not in str(err.value).replace("JWT_SECRET must be", "")


def test_placeholder_secret_is_refused_in_production_only():
    placeholder = "change-me-" + "x" * 30
    assert load_settings({**GOOD, "JWT_SECRET": placeholder}).app_env == "dev"
    with pytest.raises(ConfigError, match="placeholder"):
        load_settings({**GOOD, "JWT_SECRET": placeholder, "APP_ENV": "production"})


def test_bad_integer_and_bad_pool_range_are_rejected():
    with pytest.raises(ConfigError, match="JWT_EXPIRY_MINUTES"):
        load_settings({**GOOD, "JWT_EXPIRY_MINUTES": "soon"})
    with pytest.raises(ConfigError, match="DB_POOL_MAX"):
        load_settings({**GOOD, "DB_POOL_MIN": "5", "DB_POOL_MAX": "2"})


def test_repr_never_contains_secrets():
    text = repr(load_settings({**GOOD, "ORACLE_PASSWORD": "super-secret-pw"}))
    assert "super-secret-pw" not in text
    assert "s" * 40 not in text
