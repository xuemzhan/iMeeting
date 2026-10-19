"""持久化后台任务：入队、领取、重试与执行。"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

from sqlmodel import Session, select

from ..db import engine, session_scope
from ..models import Job, utcnow

logger = logging.getLogger(__name__)

_claim_lock = asyncio.Lock()
_HEAVY_JOB_KINDS = frozenset(
    {"final_minutes", "audio_review", "embedding_reindex"}
)


def enqueue_job(
    *,
    kind: str,
    meeting_id: int | None = None,
    owner_id: int | None = None,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    run_after=None,
    priority: int = 100,
    max_attempts: int = 3,
) -> Job:
    """入队任务；相同去重键处于 pending/running 时不重复创建。"""
    with session_scope() as db:
        if dedupe_key:
            existing = db.exec(
                select(Job)
                .where(
                    Job.dedupe_key == dedupe_key,
                    Job.status.in_(("pending", "running")),
                )
                .order_by(Job.id.desc())
            ).first()
            if existing is not None:
                return existing
        job = Job(
            kind=kind,
            meeting_id=meeting_id,
            owner_id=owner_id,
            payload=json.dumps(payload or {}, ensure_ascii=False),
            status="pending",
            priority=priority,
            max_attempts=max(1, max_attempts),
            dedupe_key=dedupe_key,
            run_after=run_after or utcnow(),
        )
        db.add(job)
        db.flush()
        db.refresh(job)
        db.expunge(job)
        return job


def _claim_job(heavy: bool | None = None) -> Job | None:
    """领取一个到期任务。

    单进程由 asyncio 锁串行化；多进程未来可替换为 PostgreSQL 的
    ``SELECT ... FOR UPDATE SKIP LOCKED``。

    ``heavy=True/False`` 将 CPU/模型密集型任务与导出等轻任务分开，
    避免长纪要生成阻塞用户下载。
    """
    with session_scope() as db:
        query = select(Job).where(
            Job.status == "pending",
            Job.run_after <= utcnow(),
        )
        if heavy is True:
            query = query.where(Job.kind.in_(tuple(_HEAVY_JOB_KINDS)))
        elif heavy is False:
            query = query.where(Job.kind.not_in(tuple(_HEAVY_JOB_KINDS)))
        job = db.exec(
            query.order_by(Job.priority, Job.run_after, Job.id)
        ).first()
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.locked_at = utcnow()
        job.updated_at = utcnow()
        db.add(job)
        db.flush()
        db.refresh(job)
        db.expunge(job)
        return job


def _finish(job: Job, *, result: dict | None = None, result_path: str | None = None,
            download_name: str | None = None) -> None:
    with session_scope() as db:
        row = db.get(Job, job.id)
        if row is None:
            return
        row.status = "succeeded"
        row.result = json.dumps(result or {}, ensure_ascii=False)
        row.result_path = result_path
        row.download_name = download_name
        row.last_error = None
        row.locked_at = None
        row.updated_at = utcnow()
        db.add(row)


def _fail(job: Job, error: str) -> None:
    with session_scope() as db:
        row = db.get(Job, job.id)
        if row is None:
            return
        row.last_error = error[:4000]
        row.locked_at = None
        row.updated_at = utcnow()
        if row.attempts >= row.max_attempts:
            row.status = "failed"
        else:
            row.status = "pending"
            row.run_after = utcnow() + timedelta(
                seconds=min(300, 5 * 2 ** max(0, row.attempts - 1))
            )
        db.add(row)


async def _run_job(job: Job) -> None:
    from ..settings import get_config

    payload = json.loads(job.payload or "{}")

    if job.kind == "final_minutes" and job.meeting_id is not None:
        from ..engine.speaker_merge import merge_meeting_speakers
        from ..engine.speaker_reconcile import reconcile_meeting_speakers
        from ..llm.progressive import generate_final_minutes
        from ..meeting_meta import backfill_participants

        if bool(get_config("spk.global_merge_enabled")):
            await asyncio.to_thread(
                merge_meeting_speakers,
                job.meeting_id,
                min_segments=int(get_config("spk.merge_min_segments") or 3),
                threshold=float(get_config("spk.merge_threshold") or 0.45),
            )
        if bool(get_config("spk.post_meeting_reconcile_enabled")):
            try:
                await asyncio.to_thread(
                    reconcile_meeting_speakers,
                    job.meeting_id,
                    max_samples=int(
                        get_config("spk.reconcile_max_samples") or 5
                    ),
                    segment_threshold=float(
                        get_config("spk.reconcile_segment_threshold") or 0.62
                    ),
                )
            except Exception:
                logger.exception(
                    "会后声纹复核失败 meeting=%s", job.meeting_id
                )
        await asyncio.to_thread(backfill_participants, job.meeting_id)
        result = await generate_final_minutes(
            job.meeting_id,
            min_chars=int(get_config("llm.final_min_chars") or 80),
        )
        if bool(get_config("privacy.auto_redact")):
            from ..privacy import apply_meeting_redaction

            await asyncio.to_thread(apply_meeting_redaction, job.meeting_id)
        if bool(get_config("review.auto_after_meeting")):
            enqueue_job(
                kind="audio_review",
                meeting_id=job.meeting_id,
                dedupe_key=f"audio_review:{job.meeting_id}",
                priority=30,
            )
        _finish(job, result={"minutes": result})
        return

    if job.kind == "meeting_meta" and job.meeting_id is not None:
        from ..meeting_meta import capture_meeting_metadata

        result = await capture_meeting_metadata(job.meeting_id)
        _finish(job, result={"metadata": result})
        return

    if job.kind in ("export_single", "export_bulk"):
        from .exports import run_export_job

        path, name, result = await asyncio.to_thread(run_export_job, job, payload)
        _finish(job, result=result, result_path=str(path), download_name=name)
        return

    if job.kind == "audio_review" and job.meeting_id is not None:
        from ..audio_review import run_audio_review

        result = await asyncio.to_thread(run_audio_review, job.meeting_id)
        _finish(job, result=result)
        return

    if job.kind == "embedding_reindex":
        from ..search_index import rebuild_index

        result = await asyncio.to_thread(rebuild_index, payload.get("meeting_id"))
        _finish(job, result=result)
        return

    raise RuntimeError(f"未知任务类型：{job.kind}")


async def worker_loop(
    stop_event: asyncio.Event,
    *,
    heavy: bool | None = None,
    lane: str = "default",
) -> None:
    logger.info("持久化任务 Worker 已启动 lane=%s", lane)
    while not stop_event.is_set():
        job: Job | None = None
        try:
            async with _claim_lock:
                job = await asyncio.to_thread(_claim_job, heavy)
            if job is None:
                await asyncio.sleep(1)
                continue
            logger.info(
                "执行任务 lane=%s id=%s kind=%s meeting=%s",
                lane,
                job.id,
                job.kind,
                job.meeting_id,
            )
            await _run_job(job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("任务执行失败 id=%s", getattr(job, "id", None))
            if job is not None:
                await asyncio.to_thread(_fail, job, f"{type(exc).__name__}: {exc}")
        finally:
            await asyncio.sleep(0.2)
    logger.info("持久化任务 Worker 已停止 lane=%s", lane)


def _worker_counts() -> tuple[int, int]:
    from ..settings import get_config

    def clamp(value: object, default: int, maximum: int) -> int:
        try:
            return min(max(int(value), 1), maximum)
        except (TypeError, ValueError):
            return default

    heavy = clamp(get_config("job.heavy_workers") or 1, 1, 4)
    light = clamp(get_config("job.light_workers") or 2, 2, 8)
    return heavy, light


async def start_job_workers(stop_event: asyncio.Event) -> list[asyncio.Task]:
    """Recover crash leftovers once, then start isolated heavy/light lanes."""
    await asyncio.to_thread(_recover_stale_jobs)
    heavy_count, light_count = _worker_counts()
    tasks: list[asyncio.Task] = []
    for index in range(heavy_count):
        tasks.append(
            asyncio.create_task(
                worker_loop(stop_event, heavy=True, lane=f"heavy-{index + 1}")
            )
        )
    for index in range(light_count):
        tasks.append(
            asyncio.create_task(
                worker_loop(stop_event, heavy=False, lane=f"light-{index + 1}")
            )
        )
    logger.info(
        "持久化任务分池已启动：heavy=%d light=%d",
        heavy_count,
        light_count,
    )
    return tasks


def _recover_stale_jobs() -> None:
    """进程崩溃后把超时的 running 任务重新放回队列。"""
    cutoff = utcnow() - timedelta(minutes=10)
    with session_scope() as db:
        rows = db.exec(select(Job).where(Job.status == "running")).all()
        for row in rows:
            if row.locked_at is None or row.locked_at < cutoff:
                row.status = "pending"
                row.locked_at = None
                row.run_after = utcnow()
                row.updated_at = utcnow()
                db.add(row)


def job_stats() -> dict[str, int]:
    with Session(engine) as db:
        rows = db.exec(select(Job)).all()
    counts = {"pending": 0, "running": 0, "succeeded": 0, "failed": 0}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    return counts


def job_metrics() -> dict[str, float]:
    """Return queue depth and age gauges used by Prometheus alerts."""
    now = utcnow()
    with Session(engine) as db:
        rows = db.exec(select(Job)).all()
    pending = [row for row in rows if row.status == "pending"]
    running = [row for row in rows if row.status == "running"]

    def age(row: Job, field: str) -> float:
        value = getattr(row, field, None)
        return max(0.0, (now - value).total_seconds()) if value else 0.0

    return {
        "oldest_pending_seconds": max(
            (age(row, "run_after") for row in pending),
            default=0.0,
        ),
        "oldest_running_seconds": max(
            (age(row, "locked_at") for row in running),
            default=0.0,
        ),
    }
