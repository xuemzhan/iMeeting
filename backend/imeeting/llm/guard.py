"""LLM 并发、速率与预算护栏。"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone

from sqlmodel import func, select

from ..db import session_scope
from ..models import LLMUsage
from ..settings import get_config

_semaphore: asyncio.Semaphore | None = None
_semaphore_size = 0
_lock = asyncio.Lock()
_timestamps: deque[float] = deque()


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    input_price = float(get_config("llm.input_price_per_million") or 0)
    output_price = float(get_config("llm.output_price_per_million") or 0)
    return (
        prompt_tokens / 1_000_000 * input_price
        + completion_tokens / 1_000_000 * output_price
    )


def _budget_check(meeting_id: int | None) -> None:
    monthly = float(get_config("llm.monthly_budget_usd") or 0)
    per_meeting = float(get_config("llm.meeting_budget_usd") or 0)
    if monthly <= 0 and (per_meeting <= 0 or meeting_id is None):
        return
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with session_scope() as db:
        if monthly > 0:
            spent = float(
                db.exec(
                    select(func.coalesce(func.sum(LLMUsage.cost_usd), 0.0)).where(
                        LLMUsage.created_at >= month_start
                    )
                ).one()
                or 0
            )
            if spent >= monthly:
                raise RuntimeError(f"本月 AI 预算已用尽（${spent:.4f}/${monthly:.2f}）")
        if per_meeting > 0 and meeting_id is not None:
            spent = float(
                db.exec(
                    select(func.coalesce(func.sum(LLMUsage.cost_usd), 0.0)).where(
                        LLMUsage.meeting_id == meeting_id
                    )
                ).one()
                or 0
            )
            if spent >= per_meeting:
                raise RuntimeError(
                    f"本场会议 AI 预算已用尽（${spent:.4f}/${per_meeting:.2f}）"
                )


async def acquire(meeting_id: int | None) -> None:
    global _semaphore, _semaphore_size
    size = max(1, int(get_config("llm.max_concurrent_requests") or 4))
    if _semaphore is None or _semaphore_size != size:
        _semaphore = asyncio.Semaphore(size)
        _semaphore_size = size
    await _semaphore.acquire()
    rate = max(1, int(get_config("llm.requests_per_minute") or 60))
    while True:
        async with _lock:
            now = time.monotonic()
            while _timestamps and now - _timestamps[0] > 60:
                _timestamps.popleft()
            if len(_timestamps) < rate:
                _timestamps.append(now)
                break
            wait = max(0.05, 60 - (now - _timestamps[0]))
        await asyncio.sleep(min(wait, 10))
    try:
        await asyncio.to_thread(_budget_check, meeting_id)
    except Exception:
        _semaphore.release()
        raise


def release() -> None:
    if _semaphore is not None:
        _semaphore.release()
