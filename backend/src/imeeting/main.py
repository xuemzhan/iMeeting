"""iMeeting · 后端入口。"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

import orjson
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse, PlainTextResponse

from .api.http import api_router
from .api.http.routes.system import VERSION
from .api.ws import stream
from .application.bootstrap import initialize_app_database
from .settings import SERVER_CONFIG, get_config

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def _log_path() -> Path:
    return Path(
        os.getenv("LOG_FILE")
        or (Path(SERVER_CONFIG["data_dir"]) / "logs" / "backend.log")
    )


def _install_file_logging() -> RotatingFileHandler:
    """安装业务轮转日志，并修复 Alembic fileConfig 对根 logger 的重置。

    `migrations/env.py` 的 ``fileConfig`` 会把根 logger 换成 WARN + console，
    并移除先装好的文件 handler。验证日志是否真的落盘时曾出现“启动日志有、
    会议事件全丢”的假象。这里做成幂等安装：每次启动跑完迁移后重新调用，
    移除旧的业务 handler，再挂上新的轮转文件 handler，并恢复 app.* logger。
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_meeting_app_file", False):
            root.removeHandler(handler)
            handler.close()

    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=20 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    handler._meeting_app_file = True  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(handler)
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

    # fileConfig 默认会把启动前已创建的 app.* logger 设为 disabled。
    # 即使调用方传了 disable_existing_loggers=False，旧版本/自定义 env.py
    # 仍可能留下禁用状态，这里统一恢复。
    for name, obj in logging.Logger.manager.loggerDict.items():
        if isinstance(obj, logging.Logger) and (
            name == "app" or name.startswith("app.")
        ):
            obj.disabled = False
    return handler


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format=_LOG_FORMAT,
)
_file_handler = _install_file_logging()
if os.getenv("LOG_JSON", "0") == "1":
    from .ops import JsonLogFormatter

    for handler in logging.getLogger().handlers:
        if handler is not _file_handler:
            handler.setFormatter(JsonLogFormatter())
logger = logging.getLogger(__name__)


def _orjson_dumps(content, *, default=None) -> bytes:
    return orjson.dumps(content, default=default, option=orjson.OPT_SERIALIZE_NUMPY)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：建库、加载配置、预热模型。"""
    from .runtime_checks import (
        production_config_warnings,
        validate_production_settings,
    )

    validate_production_settings()
    for warning in production_config_warnings():
        logger.warning("生产配置提示：%s", warning)
    logger.info("=== %s 启动中 ===", SERVER_CONFIG["app_name"])
    # 启动分解（M0）：分别计时建库/迁移与模型预热，便于判断 C-4 是否退化。
    _startup_started = time.perf_counter()
    initialize_app_database()
    logger.info(
        "启动分解：数据库初始化 %.0f ms",
        (time.perf_counter() - _startup_started) * 1000,
    )
    # init_db 内的 Alembic fileConfig 会重置根 logger；必须在迁移之后重装，
    # 否则只有启动日志落盘，真正重要的会话/定稿/错误事件全部丢失。
    _install_file_logging()
    logger.info("业务日志已写入 %s（20MB × 10 轮转）", _log_path())
    logger.info(
        "关键配置：设备=%s 流式模型=%s VAD=%s 标点=%s 说话人=%s",
        get_config("asr.device"),
        get_config("asr.streaming_model"),
        get_config("vad.enabled"),
        get_config("punc.enabled"),
        get_config("spk.enabled"),
    )

    if os.getenv("SKIP_MODEL_WARMUP", "0") != "1":
        from .engine.pool import warmup_async

        warmup_async()
    else:
        logger.warning("已跳过模型预热（SKIP_MODEL_WARMUP=1）")

    # 音频留存清理：启动时跑一次，之后每 6 小时一次
    cleanup_task = asyncio.create_task(_audio_cleanup_loop())
    from .application.jobs import start_job_workers

    job_stop = asyncio.Event()
    job_workers = await start_job_workers(job_stop)

    yield

    job_stop.set()
    if job_workers:
        _, pending = await asyncio.wait(job_workers, timeout=10)
        for worker in pending:
            worker.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    cleanup_task.cancel()
    try:
        await cleanup_task
    except (asyncio.CancelledError, Exception):
        pass
    # C-3：排空单写者。队列里的都是"已经算完、只差落库"的片段与状态写入，
    # 进程退出时丢掉它们等于丢用户数据；close() 会 join 工作线程（最长 10s），
    # 因此放到线程里等，不能卡在关闭路径的事件循环上。
    from .application.session_writer import shutdown_writer

    try:
        await asyncio.to_thread(shutdown_writer)
    except Exception:  # 排空失败不能让整个关闭流程中断
        logger.exception("会话写入单写者未能正常排空")

    logger.info("=== 服务关闭 ===")


async def _audio_cleanup_loop(interval_seconds: int = 6 * 3600) -> None:
    """周期性清理过期音频文件，避免磁盘无限增长。"""
    from .engine.audio import cleanup_expired, repair_interrupted_wavs

    repaired_done = False
    while True:
        try:
            # 上次进程若被强杀，留下的 WAV 头可能不完整；启动首轮先修一遍，
            # 之后不再重复扫描（运行期间的头由 AudioSink 自己维护）。
            if not repaired_done:
                await asyncio.to_thread(repair_interrupted_wavs)
                repaired_done = True
            # 清理是同步 IO，放到线程里避免阻塞事件循环
            await asyncio.to_thread(cleanup_expired)
            from .application.exports import cleanup_exports
            from .meeting_cleanup import purge_retention_expired

            await asyncio.to_thread(cleanup_exports, 24)
            await asyncio.to_thread(purge_retention_expired)
        except Exception:
            logger.exception("音频清理任务异常")
        await asyncio.sleep(interval_seconds)


app = FastAPI(
    title="总师会议智能伙伴 API",
    version=VERSION,
    description="基于 FunASR 的实时会议转写与纪要服务",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=SERVER_CONFIG["cors_origins"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _request_metrics(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        from .ops import observe_request

        observe_request(
            request.method,
            request.url.path,
            500,
            time.perf_counter() - started,
        )
        raise
    from .ops import observe_request

    observe_request(
        request.method,
        request.url.path,
        response.status_code,
        time.perf_counter() - started,
    )
    incoming_request_id = request.headers.get("X-Request-ID", "").strip()
    request_id = (
        incoming_request_id
        if _REQUEST_ID_PATTERN.fullmatch(incoming_request_id)
        else uuid.uuid4().hex
    )
    response.headers["X-Request-ID"] = request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault(
            "Cache-Control", "no-store, no-cache, must-revalidate"
        )
        response.headers.setdefault("Pragma", "no-cache")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), geolocation=(), microphone=(self)"
    )
    return response

app.include_router(api_router)
app.include_router(stream.router)


def _metrics_allowed(request: Request) -> bool:
    """``/metrics`` 的访问控制。

    配置 ``METRICS_TOKEN`` 时必须带 `Authorization: Bearer <token>`（或
    `?token=`）；生产环境未配置令牌时只允许环回地址访问——容器内的
    `curl 127.0.0.1:8000/metrics` 与同机采集照常可用，但从网络进来的抓取
    会被拒绝，避免把成本、会话数和磁盘水位直接挂在公网上。
    """
    token = os.getenv("METRICS_TOKEN", "").strip()
    if token:
        header = request.headers.get("Authorization", "")
        provided = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not provided:
            provided = request.query_params.get("token", "")
        return secrets.compare_digest(provided, token)
    if os.getenv("APP_ENV", "development").strip().lower() != "production":
        return True
    client = request.client.host if request.client else ""
    return client in {"127.0.0.1", "::1", "localhost"}


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics(request: Request):
    from sqlalchemy import func
    from sqlmodel import Session, select

    from .application.jobs import job_metrics, job_stats
    from .db import db_statement_counts, engine, pool_metrics
    from .engine import pool
    from .models import LLMUsage
    from .ops import disk_gauges, dropped_frames_total, prometheus_text
    from .session_registry import active_session_count

    if not _metrics_allowed(request):
        raise HTTPException(status_code=403, detail="指标接口未授权")

    jobs = job_metrics()
    gauges = disk_gauges(SERVER_CONFIG["data_dir"])
    # M0：连接池水位与取连接超时（S-7 的观测依据）
    pool_gauges, counters = pool_metrics()
    gauges.update(pool_gauges)
    for op, count in db_statement_counts().items():
        name = f"meeting_db_statements_{op.lower()}_total"
        counters[name] = count
    # C-3：单写者队列深度（只在启用且已创建时上报，避免观测反而去创建它）
    from .application.session_writer import writer_stats

    writer = writer_stats()
    if writer is not None:
        gauges["meeting_session_writer_queue_depth"] = float(writer["queue_depth"])
        counters["meeting_session_writer_executed_total"] = writer["executed"]
        counters["meeting_session_writer_dropped_total"] = writer["dropped"]
        counters["meeting_session_writer_errors_total"] = writer["errors"]
    # S-2/P-4：进程线程总数。线程池重建若没有 shutdown，旧 worker 会常驻，
    # 这里给运维一个能直接看到"改一次配置漏几条线程"的数。
    import threading as _threading

    gauges["meeting_process_threads"] = float(_threading.active_count())
    # S-5：被丢弃的超大音频帧（只计数不告警，阈值行为在 stream 侧）
    counters["meeting_ws_frame_dropped_total"] = dropped_frames_total()
    with Session(engine) as db:
        llm_cost = db.exec(
            select(func.coalesce(func.sum(LLMUsage.cost_usd), 0.0))
        ).one()
    gauges.update(
        {
            "meeting_job_oldest_pending_seconds": jobs[
                "oldest_pending_seconds"
            ],
            "meeting_job_oldest_running_seconds": jobs[
                "oldest_running_seconds"
            ],
            "meeting_llm_cost_usd": float(llm_cost),
            "meeting_models_loaded": float(
                sum(1 for item in pool.get_status().values() if item["loaded"])
            ),
            "meeting_model_errors": float(
                sum(1 for item in pool.get_status().values() if item["error"])
            ),
        }
    )
    return PlainTextResponse(
        prometheus_text(
            {
                "meeting_ws_active_sessions": {"active": active_session_count()},
                "meeting_jobs": job_stats(),
            },
            gauges=gauges,
            counters=counters,
        ),
        media_type="text/plain; version=0.0.4",
    )


@app.get("/")
def root():
    # 绿色包/单端口部署：配置了前端构建目录时，根路径直接返回应用首页。
    from .web_static import index_path

    index = index_path()
    if index is not None:
        from fastapi.responses import FileResponse

        return FileResponse(index)
    return {
        "name": SERVER_CONFIG["app_name"],
        "version": VERSION,
        "docs": "/docs",
        "health": "/api/v1/health",
    }


# 绿色包/单端口部署：若设置了 IMEETING_WEB_DIR，则由后端托管前端构建产物。
# 必须在所有 API 路由注册之后调用，SPA 兜底才不会遮蔽接口。
from .web_static import mount_web  # noqa: E402  (路由注册完成后再挂载)

mount_web(app)


def main() -> None:
    """命令行入口：python -m imeeting.main"""
    import uvicorn

    uvicorn.run(
        "imeeting.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("RELOAD", "0") == "1",
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
