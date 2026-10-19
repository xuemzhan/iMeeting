"""Background job lane isolation and queue health tests."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from imeeting.application import jobs
from imeeting.models import utcnow


def test_claim_separates_heavy_and_light_jobs() -> None:
    light = jobs.enqueue_job(kind="export_single")
    heavy = jobs.enqueue_job(kind="final_minutes")

    claimed_heavy = jobs._claim_job(heavy=True)
    claimed_light = jobs._claim_job(heavy=False)

    assert claimed_heavy is not None
    assert claimed_heavy.id == heavy.id
    assert claimed_light is not None
    assert claimed_light.id == light.id
    assert jobs._claim_job(heavy=True) is None
    assert jobs._claim_job(heavy=False) is None


def test_job_metrics_expose_oldest_pending_age() -> None:
    jobs.enqueue_job(
        kind="embedding_reindex",
        run_after=utcnow() - timedelta(seconds=120),
    )

    metrics = jobs.job_metrics()

    assert metrics["oldest_pending_seconds"] >= 119
    assert metrics["oldest_running_seconds"] == 0


async def test_start_job_workers_creates_isolated_lanes(monkeypatch) -> None:
    monkeypatch.setattr(jobs, "_worker_counts", lambda: (1, 2))
    stop = asyncio.Event()

    workers = await jobs.start_job_workers(stop)
    assert len(workers) == 3

    stop.set()
    await asyncio.wait_for(asyncio.gather(*workers), timeout=3)
