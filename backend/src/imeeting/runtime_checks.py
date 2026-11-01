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


def production_config_warnings(
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """生产环境下「不致命但会削弱防护」的配置，返回提示而不是拒绝启动。

    典型场景：后端跑在 nginx/Caddy 后面却没人告诉它信任代理，于是
    ``request.client.host`` 永远是代理地址——按 IP 的登录限流退化成一个全局桶
    （任何人连错几次就能把所有人锁在门外），审计日志的来源 IP 也失去意义。
    """
    values = os.environ if env is None else env
    if values.get("APP_ENV", "development").strip().lower() != "production":
        return []

    warnings: list[str] = []
    forwarded = values.get("FORWARDED_ALLOW_IPS", "").strip()
    if not forwarded or forwarded == "127.0.0.1":
        warnings.append(
            "未配置 FORWARDED_ALLOW_IPS：前端经反代访问时，后端看到的是代理 IP，"
            "登录按 IP 限流会退化成全局桶、审计 IP 失真。"
            "用 compose 部署时设为代理网段（默认已给出 RFC1918 段）。"
        )
    if not values.get("METRICS_TOKEN", "").strip():
        warnings.append(
            "未配置 METRICS_TOKEN：/metrics 仅接受环回访问（127.0.0.1/::1）。"
            "若 Prometheus 从其他主机/容器采集，需要设置该令牌。"
        )
    return warnings
