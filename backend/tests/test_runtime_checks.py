"""Production configuration guard tests."""

import pytest

from imeeting.runtime_checks import (
    production_config_errors,
    validate_production_settings,
)


def safe_env() -> dict[str, str]:
    return {
        "APP_ENV": "production",
        "SECRET_KEY": "x" * 48,
        "ADMIN_PASSWORD": "not-the-default-password",
        "REQUIRE_AUTH": "1",
        "CORS_ORIGINS": "https://meeting.example.com",
    }


def test_development_environment_keeps_zero_config_startup() -> None:
    assert production_config_errors({"APP_ENV": "development"}) == []


def test_safe_production_environment_passes() -> None:
    assert production_config_errors(safe_env()) == []
    validate_production_settings(safe_env())


def test_production_rejects_weak_secret_and_default_admin_password() -> None:
    env = safe_env()
    env["SECRET_KEY"] = "short"
    env["ADMIN_PASSWORD"] = "admin123"

    errors = production_config_errors(env)

    assert any("SECRET_KEY" in item for item in errors)
    assert any("ADMIN_PASSWORD" in item for item in errors)


def test_production_requires_authentication_and_explicit_cors() -> None:
    env = safe_env()
    env["REQUIRE_AUTH"] = "0"
    env["CORS_ORIGINS"] = "*"

    errors = production_config_errors(env)

    assert any("REQUIRE_AUTH" in item for item in errors)
    assert any("CORS_ORIGINS" in item for item in errors)


def test_validation_raises_with_all_actionable_errors() -> None:
    env = {
        "APP_ENV": "production",
        "SECRET_KEY": "",
        "ADMIN_PASSWORD": "admin123",
        "REQUIRE_AUTH": "0",
        "CORS_ORIGINS": "*",
    }

    with pytest.raises(RuntimeError) as exc_info:
        validate_production_settings(env)

    message = str(exc_info.value)
    assert "SECRET_KEY" in message
    assert "ADMIN_PASSWORD" in message
    assert "REQUIRE_AUTH" in message
    assert "CORS_ORIGINS" in message
