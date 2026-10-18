"""iMeeting · 后端入口。"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

import orjson
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse, PlainTextResponse

from .db import init_db
from .routers import (
    admin_ops,
    auth,
    config,
    export_jobs,
    glossary,
    meeting_extras,
    meetings,
    members,
    minutes_templates,
    system,
    voiceprints,
)
from .settings import SERVER_CONFIG, get_config
from .ws import stream

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


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
    logger.info("=== %s 启动中 ===", SERVER_CONFIG["app_name"])
    init_db()
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
    from .jobs import worker_loop

    job_stop = asyncio.Event()
    job_worker = asyncio.create_task(worker_loop(job_stop))

    yield

    job_stop.set()
    try:
        await asyncio.wait_for(job_worker, timeout=10)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        job_worker.cancel()
    cleanup_task.cancel()
    try:
        await cleanup_task
    except (asyncio.CancelledError, Exception):
        pass

    logger.info("=== 服务关闭 ===")


async def _audio_cleanup_loop(interval_seconds: int = 6 * 3600) -> None:
    """周期性清理过期音频文件，避免磁盘无限增长。"""
    from .engine.audio import cleanup_expired

    while True:
        try:
            # 清理是同步 IO，放到线程里避免阻塞事件循环
            await asyncio.to_thread(cleanup_expired)
            from .export_service import cleanup_exports
            from .meeting_cleanup import purge_retention_expired

            await asyncio.to_thread(cleanup_exports, 24)
            await asyncio.to_thread(purge_retention_expired)
        except Exception:
            logger.exception("音频清理任务异常")
        await asyncio.sleep(interval_seconds)


app = FastAPI(
    title="iMeeting API",
    version=system.VERSION,
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

app.include_router(auth.router)
app.include_router(admin_ops.router)
app.include_router(config.router)
app.include_router(export_jobs.router)
app.include_router(members.router)
app.include_router(glossary.router)
app.include_router(meeting_extras.router)
app.include_router(meetings.router)
app.include_router(minutes_templates.router)
app.include_router(system.router)
app.include_router(voiceprints.router)
app.include_router(stream.router)


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics():
    from .jobs import job_stats
    from .ops import prometheus_text
    from .session_registry import active_session_count

    return PlainTextResponse(
        prometheus_text(
            {
                "meeting_ws_active_sessions": {"active": active_session_count()},
                "meeting_jobs": job_stats(),
            }
        ),
        media_type="text/plain; version=0.0.4",
    )


@app.get("/")
def root():
    return {
        "name": SERVER_CONFIG["app_name"],
        "version": system.VERSION,
        "docs": "/docs",
        "health": "/api/v1/health",
    }


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
