"""Object authorization for private identities and meeting-scoped voice data."""

from fastapi import HTTPException
from sqlalchemy import case, exists, false, func
from sqlmodel import Session, select

from ..models import Meeting, Person, User, Voiceprint, VoiceprintConflict
from .meeting_access import owned_meeting


def identity_owner(db: Session, voiceprint: Voiceprint) -> int | None:
    if voiceprint.owner_id is not None:
        return voiceprint.owner_id
    meeting_id = voiceprint.meeting_id or voiceprint.source_meeting_id
    meeting = db.get(Meeting, meeting_id) if meeting_id else None
    if meeting is not None:
        return meeting.owner_id
    # Old unowned registrations belonged to the local bootstrap account.
    admin = db.exec(select(User).where(User.role == "admin").order_by(User.id)).first()
    return admin.id if admin else None


def owned_person(db: Session, person_id: int, user: User) -> Person:
    row = db.get(Person, person_id)
    if row is None:
        raise HTTPException(404, "人员不存在")
    if user.role != "admin" and row.owner_id != user.id:
        raise HTTPException(403, "无权访问该人员")
    return row


def owned_voiceprint(db: Session, voiceprint_id: int, user: User, *, write: bool = False) -> Voiceprint:
    row = db.get(Voiceprint, voiceprint_id)
    if row is None:
        raise HTTPException(404, "声纹不存在")
    if user.role == "admin" or identity_owner(db, row) == user.id:
        return row
    # Collaboration grants access only to provisional voices in that meeting,
    # never to the owner's reusable formal biometric library.
    if row.provisional and row.meeting_id is not None:
        owned_meeting(row.meeting_id, user, db, write=write)
        return row
    raise HTTPException(403, "无权访问该声纹")


def owned_conflict(db: Session, conflict_id: int, user: User) -> VoiceprintConflict:
    row = db.get(VoiceprintConflict, conflict_id)
    if row is None:
        raise HTTPException(404, "冲突记录不存在")
    owned_voiceprint(db, row.voiceprint_id, user, write=True)
    if row.meeting_id is not None:
        owned_meeting(row.meeting_id, user, db, write=True)
    if row.other_voiceprint_id is not None:
        owned_voiceprint(db, row.other_voiceprint_id, user, write=True)
    return row


def visible_voiceprint(db: Session, row: Voiceprint, meeting_id: int | None = None, owner_id: int | None = None) -> bool:
    if meeting_id is not None:
        meeting = db.get(Meeting, meeting_id)
        if meeting is None or meeting.deleted_at is not None:
            return False
        owner_id = meeting.owner_id
    return owner_id is None or identity_owner(db, row) == owner_id


def visible_voiceprint_ids(db: Session, meeting_id: int | None = None, owner_id: int | None = None):
    """SQL equivalent of the legacy ownership rules, without per-row queries."""
    query = select(Voiceprint.id)
    if meeting_id is not None:
        meeting = db.get(Meeting, meeting_id)
        if meeting is None or meeting.deleted_at is not None:
            return query.where(false())
        owner_id = meeting.owner_id
    if owner_id is None:
        return query
    source_id = func.coalesce(Voiceprint.meeting_id, Voiceprint.source_meeting_id)
    source_exists = exists().where(Meeting.id == source_id).correlate(Voiceprint)
    source_owner = select(Meeting.owner_id).where(Meeting.id == source_id).correlate(Voiceprint).scalar_subquery()
    legacy_admin = select(User.id).where(User.role == "admin").order_by(User.id).limit(1).scalar_subquery()
    resolved_owner = case(
        (Voiceprint.owner_id.is_not(None), Voiceprint.owner_id),
        (source_exists, source_owner),
        else_=legacy_admin,
    )
    return query.where(resolved_owner == owner_id)
