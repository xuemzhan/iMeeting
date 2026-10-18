"""企业词表管理。"""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from ....db import get_session
from ....glossary import invalidate_cache
from ....models import GlossaryTerm, User, utcnow
from ....schemas import GlossaryTermCreate, GlossaryTermOut, GlossaryTermUpdate
from .deps import get_current_user_or_anonymous, require_admin

router = APIRouter(prefix="/api/v1/glossary", tags=["glossary"])


@router.get("", response_model=list[GlossaryTermOut])
def list_terms(
    _: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    return db.exec(select(GlossaryTerm).order_by(GlossaryTerm.term)).all()


@router.post("", response_model=GlossaryTermOut)
def create_term(
    payload: GlossaryTermCreate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    term = payload.term.strip()
    if not term:
        raise HTTPException(status_code=400, detail="规范词不能为空")
    existing = db.exec(select(GlossaryTerm).where(GlossaryTerm.term == term)).first()
    if existing:
        raise HTTPException(status_code=409, detail="该词已存在")
    row = GlossaryTerm(
        term=term[:128],
        aliases=payload.aliases.strip()[:1000],
        category=payload.category.strip()[:64],
        owner_id=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    invalidate_cache()
    return row


@router.patch("/{term_id}", response_model=GlossaryTermOut)
def update_term(
    term_id: int,
    payload: GlossaryTermUpdate,
    _: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    row = db.get(GlossaryTerm, term_id)
    if row is None:
        raise HTTPException(status_code=404, detail="词条不存在")
    for field in ("term", "aliases", "category"):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value.strip())
    if payload.enabled is not None:
        row.enabled = payload.enabled
    row.updated_at = utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    invalidate_cache()
    return row


@router.delete("/{term_id}", status_code=204)
def delete_term(
    term_id: int,
    _: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    row = db.get(GlossaryTerm, term_id)
    if row is None:
        raise HTTPException(status_code=404, detail="词条不存在")
    db.delete(row)
    db.commit()
    invalidate_cache()
    return None


@router.post("/import")
async def import_terms(
    file: UploadFile = File(...),
    _: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    raw = await file.read(2 * 1024 * 1024)
    text = raw.decode("utf-8-sig", errors="replace")
    imported = 0
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].strip():
            continue
        term = row[0].strip()[:128]
        if db.exec(select(GlossaryTerm).where(GlossaryTerm.term == term)).first():
            continue
        db.add(
            GlossaryTerm(
                term=term,
                aliases=(row[1] if len(row) > 1 else "").strip()[:1000],
                category=(row[2] if len(row) > 2 else "").strip()[:64],
            )
        )
        imported += 1
    db.commit()
    invalidate_cache()
    return {"imported": imported}
