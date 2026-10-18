"""Runtime configuration guardrails for production deployments."""

from __future__ import annotations

import os
from collections.abc import Mapping

from .db import DEFAULT_ADMIN_PASSWORD

MIN_SECRET_BYTES = 32


def production_config_errors(
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Return unsafe production settings without mutating the environment."""
    values = os.environ if env is None else env
    if values.get("APP_ENV", "development").strip().lower() != "production":
        return []

    errors: list[str] = []
    secret = values.get("SECRET_KEY", "").strip()
    if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        errors.append(
            f"SECRET_KEY 必须显式配置且至少 {MIN_SECRET_BYTES} 字节"
        )

    admin_password = values.get("ADMIN_PASSWORD", "")
    if not admin_password or admin_password == DEFAULT_ADMIN_PASSWORD:
        errors.append("ADMIN_PASSWORD 必须显式配置且不能使用出厂默认口令")

    if values.get("REQUIRE_AUTH", "0").strip() != "1":
        errors.append("REQUIRE_AUTH 必须设为 1")

    cors_origins = [
        origin.strip()
        for origin in values.get("CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]
    if "*" in cors_origins:
        errors.append("CORS_ORIGINS 不能包含通配符 *")

    return errors


def validate_production_settings(
    env: Mapping[str, str] | None = None,
) -> None:
    """Fail fast when production starts with unsafe defaults."""
    errors = production_config_errors(env)
    if errors:
        detail = "\n".join(f"- {item}" for item in errors)
        raise RuntimeError(f"生产环境配置不安全，拒绝启动：\n{detail}")
