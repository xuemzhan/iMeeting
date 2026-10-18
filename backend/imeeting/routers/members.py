"""会议级协作者权限。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from ..db import get_session
from ..models import AuditLog, Meeting, MeetingMember, User, utcnow
from .deps import get_current_user_or_anonymous

router = APIRouter(prefix="/api/v1/meetings/{meeting_id}/members", tags=["members"])


class MemberCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    role: str = Field(default="viewer", pattern="^(viewer|editor)$")


class MemberOut(BaseModel):
    user_id: int
    username: str
    display_name: str
    role: str


def _owner_or_admin(meeting_id: int, user: User, db: Session) -> Meeting:
    meeting = db.get(Meeting, meeting_id)
    if meeting is None or meeting.deleted_at is not None:
        raise HTTPException(status_code=404, detail="会议不存在")
    if user.role != "admin" and meeting.owner_id != user.id:
        raise HTTPException(status_code=403, detail="仅会议所有者可管理协作成员")
    return meeting


@router.get("", response_model=list[MemberOut])
def list_members(
    meeting_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    meeting = _owner_or_admin(meeting_id, user, db)
    owner = db.get(User, meeting.owner_id)
    out = (
        [
            MemberOut(
                user_id=owner.id,
                username=owner.username,
                display_name=owner.display_name,
                role="owner",
            )
        ]
        if owner
        else []
    )
    rows = db.exec(
        select(MeetingMember).where(MeetingMember.meeting_id == meeting_id)
    ).all()
    for row in rows:
        member = db.get(User, row.user_id)
        if member:
            out.append(
                MemberOut(
                    user_id=member.id,
                    username=member.username,
                    display_name=member.display_name,
                    role=row.role,
                )
            )
    return out


@router.post("", response_model=MemberOut)
def add_member(
    meeting_id: int,
    payload: MemberCreate,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    _owner_or_admin(meeting_id, user, db)
    target = db.exec(
        select(User).where(User.username == payload.username.strip(), User.is_active == True)  # noqa: E712
    ).first()
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在或已停用")
    row = db.exec(
        select(MeetingMember).where(
            MeetingMember.meeting_id == meeting_id,
            MeetingMember.user_id == target.id,
        )
    ).first()
    if row is None:
        row = MeetingMember(
            meeting_id=meeting_id,
            user_id=target.id,
            role=payload.role,
            created_by=user.id,
        )
    else:
        row.role = payload.role
    row.created_at = row.created_at or utcnow()
    db.add(row)
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="meeting_member_upsert",
            target=f"meeting:{meeting_id}/user:{target.id}",
            payload=f'{{"role":"{payload.role}"}}',
        )
    )
    db.commit()
    return MemberOut(
        user_id=target.id,
        username=target.username,
        display_name=target.display_name,
        role=row.role,
    )


@router.delete("/{member_user_id}", status_code=204)
def remove_member(
    meeting_id: int,
    member_user_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    _owner_or_admin(meeting_id, user, db)
    row = db.exec(
        select(MeetingMember).where(
            MeetingMember.meeting_id == meeting_id,
            MeetingMember.user_id == member_user_id,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="协作成员不存在")
    db.delete(row)
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="meeting_member_remove",
            target=f"meeting:{meeting_id}/user:{member_user_id}",
        )
    )
    db.commit()
    return None
