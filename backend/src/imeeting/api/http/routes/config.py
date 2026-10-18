"""配置路由：读取、修改、schema 元数据。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session

from ....db import get_session, persist_config
from ....models import AuditLog, User
from ....schemas import (
    ConfigFieldOut,
    ConfigSchemaOut,
    ConfigUpdateRequest,
    ConfigUpdateResponse,
)
from ....settings import CONFIG_SCHEMA, get_config, validate_update
from .deps import get_current_user, require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/config", tags=["config"])


@router.get("", response_model=dict)
def read_config(user: User = Depends(get_current_user)):
    """读取全部配置（敏感字段脱敏）。

    普通用户也能读，因为前端需要知道当前 VAD/标点/说话人是否开启，
    以便正确渲染界面状态。密钥类字段一律脱敏。
    """
    from ....settings import config_store

    return config_store.snapshot(redact=True)


@router.get("/schema", response_model=ConfigSchemaOut)
def read_schema(user: User = Depends(require_admin)):
    """返回配置项元数据，供后台表单自动渲染。"""
    groups: dict[str, list[ConfigFieldOut]] = {}
    for field in CONFIG_SCHEMA:
        value = get_config(field.key)
        if field.secret and value:
            value = "********"
        groups.setdefault(field.group, []).append(
            ConfigFieldOut(
                key=field.key,
                label=field.label,
                group=field.group,
                type=field.type,
                value=value,
                help=field.help,
                options=field.options,
                secret=field.secret,
                restart_required=field.restart_required,
                min_value=field.min_value,
                max_value=field.max_value,
            )
        )
    return ConfigSchemaOut(groups=groups)


@router.put("", response_model=ConfigUpdateResponse)
def update_config(
    payload: ConfigUpdateRequest,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    """修改配置。仅管理员可操作，变更写入审计日志。

    密钥类字段做加密存储；值为空或掩码时表示「不修改」。
    """
    values = dict(payload.values)

    # 掩码值表示前端未修改该密钥，跳过
    from ....security import encrypt_value
    from ....settings import SCHEMA_BY_KEY

    cleaned_input: dict = {}
    for key, value in values.items():
        meta = SCHEMA_BY_KEY.get(key)
        if meta is None:
            cleaned_input[key] = value
            continue
        if meta.secret and (value == "********" or value == ""):
            continue
        cleaned_input[key] = value

    cleaned, errors = validate_update(cleaned_input)

    # 敏感字段加密后再落库
    stored: dict = {}
    for key, value in cleaned.items():
        meta = SCHEMA_BY_KEY.get(key)
        if meta is not None and meta.secret:
            stored[key] = encrypt_value(str(value))
        else:
            stored[key] = value

    requires_restart: list[str] = []
    if stored:
        persist_config(stored, user_id=user.id)
        for key in stored:
            meta = SCHEMA_BY_KEY.get(key)
            if meta is not None and meta.restart_required:
                requires_restart.append(key)

        db.add(
            AuditLog(
                user_id=user.id,
                username=user.username,
                action="config_update",
                target="config",
                payload=",".join(stored.keys()),
                ip=request.client.host if request.client else None,
            )
        )
        db.commit()

    # 敏感字段读取时需要解密回明文存内存缓存，否则后续调用拿到密文
    if stored:
        plain: dict = {}
        for key, value in stored.items():
            meta = SCHEMA_BY_KEY.get(key)
            if meta is not None and meta.secret:
                plain[key] = str(cleaned.get(key, ""))
            else:
                plain[key] = value
        from ....settings import config_store

        config_store.set_overrides(plain)

    return ConfigUpdateResponse(
        updated=list(stored.keys()),
        requires_restart=requires_restart,
        errors=errors,
    )
