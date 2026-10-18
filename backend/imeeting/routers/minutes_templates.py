"""会议纪要模板：列表、导入、编辑、删除。"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import AuditLog, MinutesTemplate, User, utcnow
from ..schemas import MinutesTemplateCreate, MinutesTemplateOut
from ..settings import get_config
from .deps import get_current_user_or_anonymous

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/minutes-templates", tags=["minutes-templates"])


class TemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    content: str | None = None


def _audit(db: Session, user: User, action: str, target: str, payload: str = "") -> None:
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action=action,
            target=target,
            payload=payload,
        )
    )


@router.get("", response_model=list[MinutesTemplateOut])
def list_templates(
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    return db.exec(select(MinutesTemplate).order_by(MinutesTemplate.id)).all()


@router.post("", response_model=MinutesTemplateOut)
def create_template(
    payload: MinutesTemplateCreate,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    name = payload.name.strip()
    content = payload.content.strip()
    if not name or not content:
        raise HTTPException(status_code=400, detail="模板名称与内容不能为空")
    if db.exec(select(MinutesTemplate).where(MinutesTemplate.name == name)).first():
        raise HTTPException(status_code=409, detail=f"已存在名为「{name}」的模板")
    row = MinutesTemplate(
        name=name[:64],
        description=payload.description.strip()[:200],
        content=content,
        owner_id=user.id,
    )
    db.add(row)
    _audit(db, user, "minutes_template_create", name)
    db.commit()
    db.refresh(row)
    return row


def _read_upload(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in (".md", ".txt", ".markdown"):
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise HTTPException(status_code=400, detail="无法识别文件编码，请另存为 UTF-8")
    if suffix == ".docx":
        try:
            import docx  # python-docx

            document = docx.Document(io.BytesIO(data))
            return "\n".join(p.text for p in document.paragraphs if p.text.strip())
        except ImportError as exc:
            raise HTTPException(
                status_code=400, detail="服务器未安装 python-docx，暂不支持 .docx"
            ) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"无法解析 Word 模板：{exc}") from exc
    raise HTTPException(
        status_code=400, detail="仅支持 .md / .txt / .docx 模板文件"
    )


@router.post("/import", response_model=MinutesTemplateOut)
async def import_template(
    file: UploadFile = File(...),
    name: str = Form(default=""),
    description: str = Form(default=""),
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    limit = int(get_config("upload.max_template_bytes") or 10 * 1024 * 1024)
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail="模板文件超过大小上限")
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    content = _read_upload(file.filename or "template.txt", data).strip()
    if not content:
        raise HTTPException(status_code=400, detail="模板内容为空")
    template_name = (name or Path(file.filename or "模板").stem).strip()[:64]
    existing = db.exec(
        select(MinutesTemplate).where(MinutesTemplate.name == template_name)
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"已存在名为「{template_name}」的模板")
    row = MinutesTemplate(
        name=template_name,
        description=description.strip()[:200],
        content=content,
        owner_id=user.id,
    )
    db.add(row)
    _audit(db, user, "minutes_template_import", template_name, file.filename or "")
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{template_id}", response_model=MinutesTemplateOut)
def update_template(
    template_id: int,
    payload: TemplateUpdate,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    row = db.get(MinutesTemplate, template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="模板名称不能为空")
        clash = db.exec(
            select(MinutesTemplate).where(
                MinutesTemplate.name == name, MinutesTemplate.id != template_id
            )
        ).first()
        if clash is not None:
            raise HTTPException(status_code=409, detail=f"已存在名为「{name}」的模板")
        row.name = name[:64]
    if payload.description is not None:
        row.description = payload.description.strip()[:200]
    if payload.content is not None:
        if not payload.content.strip():
            raise HTTPException(status_code=400, detail="模板内容不能为空")
        row.content = payload.content
    row.updated_at = utcnow()
    db.add(row)
    _audit(db, user, "minutes_template_update", row.name)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(
    template_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    row = db.get(MinutesTemplate, template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    if row.is_builtin:
        raise HTTPException(status_code=400, detail="内置模板不能删除")
    _audit(db, user, "minutes_template_delete", row.name)
    db.delete(row)
    db.commit()
    return None
