"""健康检查与模型状态路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..engine import pool
from ..schemas import HealthOut, ModelStatusOut
from ..settings import get_config

router = APIRouter(tags=["system"])

VERSION = "0.1.0"


@router.get("/api/v1/health", response_model=HealthOut)
def health() -> HealthOut:
    """健康检查。包含模型加载状态，便于前端展示就绪进度。

    ``status`` 的语义（供编排系统/负载均衡判断）：

    - ``ok``       服务可正常提供转写
    - ``loading``  模型仍在加载，稍后即可用（不是故障，不应被摘除）
    - ``degraded`` 模型加载失败，退化为不可用
    - ``disabled`` 配置为 off/echo 模式，本就不需要模型

    ``disabled`` 是必须区分的状态：``off``/``echo`` 模式下模型永远不会加载，
    若沿用「全加载完才 ok」的判据，健康检查会永远停在 ``loading``，
    让监控误以为服务一直在启动中。
    """
    from ..session_registry import active_session_count

    status = pool.get_status()
    models = {name: ModelStatusOut(**info) for name, info in status.items()}

    mode = str(get_config("asr.mode") or "normal")
    any_error = any(m.error for m in models.values())

    if mode != "normal":
        # echo / off 模式不需要模型，服务本身是健康的
        overall = "disabled"
    elif all(m.loaded for m in models.values()):
        overall = "ok"
    elif any(m.loaded for m in models.values()):
        # 部分就绪：可以开始转写，但精度补偿链路还没好
        overall = "loading"
    elif any_error:
        overall = "degraded"
    else:
        overall = "loading"

    return HealthOut(
        status=overall,
        version=VERSION,
        models=models,
        active_sessions=active_session_count(),
    )


@router.get("/api/v1/models/status", response_model=dict[str, ModelStatusOut])
def model_status() -> dict[str, ModelStatusOut]:
    return {name: ModelStatusOut(**info) for name, info in pool.get_status().items()}


@router.get("/api/v1/live", include_in_schema=False)
def liveness() -> dict:
    return {"status": "alive"}


@router.get("/api/v1/ready", include_in_schema=False)
def readiness() -> dict:
    """编排系统使用：排空中或模型降级时不应再接收新流量。"""
    from ..maintenance import drain_status
    from ..session_registry import active_session_count

    health_value = health()
    drain = drain_status()
    if drain["draining"]:
        raise HTTPException(
            status_code=503,
            detail={
                "ready": False,
                "reason": drain["reason"] or "draining",
                "active_sessions": active_session_count(),
            },
        )
    if health_value.status == "degraded":
        raise HTTPException(
            status_code=503,
            detail={"ready": False, "reason": "models_degraded"},
        )
    return {
        "ready": health_value.status in ("ok", "disabled", "loading"),
        "status": health_value.status,
        "active_sessions": active_session_count(),
    }
