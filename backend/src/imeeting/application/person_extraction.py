"""Persistent extraction admission and non-blocking, deduplicated triggers."""

import threading
import uuid
from datetime import timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import text
from sqlmodel import Session, select

from ..db import engine, session_scope
from ..models import Meeting, PersonExtractionState, Segment, utcnow
from ..settings import get_config

_LOCK = threading.Lock()


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def reserve(meeting_id, *, manual, cursor_id):
    with _LOCK, session_scope() as db:
        # SQLite serializes this short admission transaction across processes,
        # not just across threads. The remote LLM call happens after commit.
        if db.bind.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        state = db.get(PersonExtractionState, meeting_id) or PersonExtractionState(meeting_id=meeting_id)
        now = utcnow()
        if state.lease_until and aware(state.lease_until) > now:
            if manual:
                raise HTTPException(429, "参会人识别正在进行，请稍后重试", headers={"Retry-After": "30"})
            return None, "in_flight"
        if manual:
            if state.manual_count >= 10 or (state.last_manual_at and (now - aware(state.last_manual_at)).total_seconds() < 30):
                raise HTTPException(429, "本场参会人识别过于频繁或次数已用完", headers={"Retry-After": "30"})
            state.manual_count += 1
            state.last_manual_at = now
        else:
            if not get_config("person.extract_enabled"):
                return None, "disabled"
            if state.auto_count >= int(get_config("person.max_auto_extracts")):
                return None, "auto_limit"
            if cursor_id <= state.cursor_id:
                return None, "no_new_segments"
            if state.last_auto_at and (now - aware(state.last_auto_at)).total_seconds() < 300:
                return None, "cooldown"
            state.auto_count += 1
            state.last_auto_at = now
        state.lease_token = uuid.uuid4().hex
        state.lease_until = now + timedelta(minutes=5)
        db.add(state)
        return state.lease_token, state.failures


def request_auto(meeting_id, *, text="", trigger="final"):
    if not get_config("person.extract_enabled"):
        return None
    from ..person_extract import should_extract_on_final
    from .jobs import enqueue_job

    if trigger == "final" and not should_extract_on_final(meeting_id, text):
        return None
    if trigger == "periodic" and int(get_config("person.extract_interval_minutes")) <= 0:
        return None
    with Session(engine) as db:
        meeting = db.get(Meeting, meeting_id)
        if meeting is None or meeting.deleted_at is not None:
            return None
        owner_id = meeting.owner_id
        state = db.get(PersonExtractionState, meeting_id)
        newest = db.exec(select(Segment.id).where(Segment.meeting_id == meeting_id, Segment.is_final.is_(True)).order_by(Segment.id.desc())).first()
        if newest is None or (state and newest <= state.cursor_id):
            return None
        if state and state.auto_count >= int(get_config("person.max_auto_extracts")):
            return None
    return enqueue_job(kind="person_extract", meeting_id=meeting_id, owner_id=owner_id,
                       payload={"trigger": trigger}, dedupe_key=f"person_extract:{meeting_id}", priority=25)
