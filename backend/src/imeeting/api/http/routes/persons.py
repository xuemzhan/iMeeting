"""人员本体接口（REQ-001）：参会人抽取、确认、绑定与合并。

权限与会议接口一致：写操作走 ``owned_meeting(..., write=True)``，匿名模式
（``REQUIRE_AUTH=0``）下与其它接口一样挂到 admin 名下。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from .... import person_extract, persons
from ....application.identity_access import owned_person
from ....application.meeting_access import owned_meeting as _owned
from ....db import get_session
from ....models import User
from .deps import get_current_user_or_anonymous

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["persons"])


class PersonAttributeIn(BaseModel):
    category: str = Field(min_length=1, max_length=16)
    key: str = Field(min_length=1, max_length=32)
    value: str = Field(min_length=0, max_length=64)


class ParticipantUpdate(BaseModel):
    state: str | None = None
    role: str | None = Field(default=None, max_length=16)
    importance: int | None = Field(default=None, ge=1, le=3)
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    attributes: list[PersonAttributeIn] = []


class SpeakerBind(BaseModel):
    speaker_key: str | None = Field(default=None, max_length=32)


class PersonMerge(BaseModel):
    source_person_id: int


def _audit(db: Session, user: User, action: str, target: str, payload: dict) -> None:
    persons.audit(
        db,
        action=action,
        target=target,
        payload=payload,
        user_id=user.id,
        username=user.username,
    )


@router.get("/meetings/{meeting_id}/participants")
def list_participants(
    meeting_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """会议参会人（含属性、绑定、确认状态）。"""
    _owned(meeting_id, user, db)
    return persons.list_participants(db, meeting_id)


class ParticipantCreate(BaseModel):
    """手工或语音输入新增参会人。语音在前端转文字后走同一个接口。"""

    name: str = Field(min_length=1, max_length=64)
    speaker_key: str | None = Field(default=None, max_length=32)
    role: str = Field(default="", max_length=16)
    attributes: list[PersonAttributeIn] = []


@router.post("/meetings/{meeting_id}/participants", status_code=status.HTTP_201_CREATED)
def create_participant(
    meeting_id: int,
    payload: ParticipantCreate,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """手工（或语音输入）加一位参会人：直接标记为已确认。"""
    _owned(meeting_id, user, db, write=True)
    try:
        item = persons.add_manual_participant(
            db,
            meeting_id=meeting_id,
            name=payload.name,
            speaker_key=payload.speaker_key,
            role=payload.role,
            attributes=[a.model_dump() for a in payload.attributes],
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    persons.sync_participants_string(db, meeting_id=meeting_id)
    _audit(
        db,
        user,
        "person_add",
        f"meeting:{meeting_id}",
        payload.model_dump(exclude_none=True),
    )
    db.commit()
    persons.publish_identity_changes(db)
    return item


@router.post("/meetings/{meeting_id}/participants/extract")
async def extract_participants(
    meeting_id: int,
    force: bool = Query(default=True),
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """手动触发一次参会人抽取（幂等；``force=false`` 仍受自动抽取上限约束）。"""
    _owned(meeting_id, user, db, write=True)
    try:
        result = await person_extract.extract_people(meeting_id, manual=force)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _audit(
        db,
        user,
        "person_extract_manual",
        f"meeting:{meeting_id}",
        {
            "created": result.get("created", 0),
            "updated": result.get("updated", 0),
            "degraded": result.get("degraded", False),
            "skipped": result.get("skipped"),
        },
    )
    db.commit()
    persons.publish_identity_changes(db)
    return result


@router.patch("/meetings/{meeting_id}/participants/{person_id}")
def update_participant(
    meeting_id: int,
    person_id: int,
    payload: ParticipantUpdate,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """人工确认/编辑/删除一位参会人。"""
    _owned(meeting_id, user, db, write=True)
    try:
        item = persons.set_participant(
            db,
            meeting_id=meeting_id,
            person_id=person_id,
            state=payload.state,
            role=payload.role,
            importance=payload.importance,
            display_name=payload.display_name,
            attributes=[a.model_dump() for a in payload.attributes],
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    persons.sync_participants_string(db, meeting_id=meeting_id)
    _audit(
        db,
        user,
        "person_confirm",
        f"meeting:{meeting_id}/person:{person_id}",
        payload.model_dump(exclude_none=True),
    )
    db.commit()
    persons.publish_identity_changes(db)
    return item


@router.post("/meetings/{meeting_id}/participants/{person_id}/bind")
def bind_speaker(
    meeting_id: int,
    person_id: int,
    payload: SpeakerBind,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """把本场 ``speaker_key`` 绑到该人员（``null`` = 解绑），并回溯转写标签。"""
    _owned(meeting_id, user, db, write=True)
    try:
        result = persons.bind_speaker(
            db,
            meeting_id=meeting_id,
            person_id=person_id,
            speaker_key=(payload.speaker_key or "").strip() or None,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _audit(
        db,
        user,
        "person_bind",
        f"meeting:{meeting_id}/person:{person_id}",
        result,
    )
    db.commit()
    persons.publish_identity_changes(db)
    return result


@router.get("/persons")
def list_persons(
    q: str = Query(default="", max_length=64),
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """人员检索（姓名/别名/拼音/属性）。"""
    return persons.list_persons(db, user.id, query=q)


@router.post("/persons/{person_id}/merge")
def merge_person(
    person_id: int,
    payload: PersonMerge,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """把 ``source_person_id`` 合并进 ``person_id``（保留目标）。"""
    owned_person(db, person_id, user)
    owned_person(db, payload.source_person_id, user)
    try:
        target = persons.merge_persons(
            db, target_id=person_id, source_id=payload.source_person_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(
        db,
        user,
        "person_merge",
        f"person:{person_id}",
        {"source_person_id": payload.source_person_id},
    )
    db.commit()
    persons.publish_identity_changes(db)
    return {
        "person_id": target.id,
        "display_name": target.display_name,
        "merged_source": payload.source_person_id,
    }


@router.delete("/persons/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_person(
    person_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """软删除人员：状态置 deleted，属性与别名清空，会议内引用保留。"""
    owned_person(db, person_id, user)
    try:
        persons.delete_person(db, person_id=person_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _audit(db, user, "person_delete", f"person:{person_id}", {})
    db.commit()
    persons.publish_identity_changes(db)
    return None


@router.post("/meetings/{meeting_id}/participants/confirm-all")
def confirm_all_participants(meeting_id: int, user: User = Depends(get_current_user_or_anonymous), db: Session = Depends(get_session)):
    _owned(meeting_id, user, db, write=True)
    for item in persons.list_participants(db, meeting_id):
        if item["state"] == "pending" and not item.get("hidden_by_confidence"):
            persons.set_participant(db, meeting_id=meeting_id, person_id=item["person_id"], state="confirmed")
    persons.sync_participants_string(db, meeting_id=meeting_id)
    _audit(db, user, "person_confirm_all", f"meeting:{meeting_id}", {})
    db.commit()
    persons.publish_identity_changes(db)
    return persons.list_participants(db, meeting_id)
