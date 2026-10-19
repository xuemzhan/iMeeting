"""依赖注入：当前用户、管理员校验。"""

from __future__ import annotations

import os

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session, select

from ....db import get_session
from ....models import User
from ....security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def auth_required() -> bool:
    """是否强制登录。与 WS 侧 `_auth_required()` 共用同一个环境变量。"""
    return os.getenv("REQUIRE_AUTH", "0") == "1"


def _anonymous_user(db: Session) -> User | None:
    """未开启鉴权时把请求挂到 admin 名下，与 WS 的 `_anonymous_identity()` 一致。"""
    return db.exec(select(User).where(User.role == "admin")).first()


def _ensure_user_ready(user: User, *, allow_pending_password: bool = False) -> User:
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被停用")
    if user.must_change_password and not allow_pending_password:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="必须先修改初始密码",
        )
    return user


def _user_from_credentials(
    credentials: HTTPAuthorizationCredentials,
    db: Session,
    *,
    allow_pending_password: bool = False,
) -> User:
    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效或已过期")

    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌格式异常")

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return _ensure_user_ready(
        user,
        allow_pending_password=allow_pending_password,
    )


def get_current_user_or_anonymous(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_session),
) -> User:
    """用于「本地个人偏好」类接口（声纹录入）。

    为什么不能直接复用 `get_current_user`：本应用默认 `REQUIRE_AUTH=0`，
    设计上允许不登录就使用（WS 转写与主界面都走匿名身份）。但声纹录入
    如果硬要求令牌，就会出现「转写能用、录声纹却报未提供访问令牌」的
    前后不一致——这正是实际踩到的坑。

    因此这里对齐 WS 的策略：
      - 带有效令牌 → 该用户
      - 未带令牌且未开启鉴权 → 匿名（挂到 admin 名下）
      - 未带令牌且**开启了鉴权** → 依然 401，生产环境不会被绕过
    """
    if credentials is None:
        if not auth_required():
            user = _anonymous_user(db)
            if user is not None:
                return _ensure_user_ready(user)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供访问令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return _user_from_credentials(credentials, db)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_session),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供访问令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return _user_from_credentials(credentials, db)


def get_current_user_allow_pending_password(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_session),
) -> User:
    """Authenticated user who is allowed to change an initial password."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供访问令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _user_from_credentials(
        credentials,
        db,
        allow_pending_password=True,
    )


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user
