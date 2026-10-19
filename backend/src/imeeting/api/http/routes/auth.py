"""认证路由：登录、注册、当前用户。"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlmodel import Session, select

from ....db import admin_credentials_from_env, get_session
from ....login_throttle import check as throttle_check
from ....login_throttle import record_failure as throttle_failure
from ....login_throttle import record_success as throttle_success
from ....models import AuditLog, User
from ....schemas import (
    AuthBootstrapOut,
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from ....security import create_access_token, hash_password, verify_password
from ....settings import get_config
from .deps import get_current_user_allow_pending_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# 失败的提示文案只有这一条：无论「无此用户」「密码错」「正在退避」都用它，
# 避免通过响应内容区分出账号是否存在。
_BAD_CREDENTIALS = "用户名或密码错误"


def _reject_locked(state, response: Response) -> HTTPException:
    """构造 429 响应并附上 Retry-After，方便前端提示「请 N 秒后再试」。

    文案刻意不区分「账号」与「IP」两个维度：一旦区分开，攻击者就能据此
    判断自己换了用户名/换代理后是否仍被同一维度跟上，从而逆向出限流规则。
    """
    retry_after = str(state.retry_after)
    response.headers["Retry-After"] = retry_after
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"登录尝试过于频繁，请 {retry_after} 秒后重试",
        headers={"Retry-After": retry_after},
    )


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_session),
):
    ip = request.client.host if request.client else None

    # 闸门前置：命中退避直接拒绝，连查库与 bcrypt 都省掉
    state = throttle_check(payload.username, ip)
    if not state.allowed:
        raise _reject_locked(state, response)

    user = db.exec(select(User).where(User.username == payload.username)).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        failure = throttle_failure(payload.username, ip)
        db.add(
            AuditLog(
                user_id=user.id if user else None,
                username=payload.username,
                action="login_failed",
                target="auth",
                ip=ip,
            )
        )
        db.commit()
        if not failure.allowed:
            raise _reject_locked(failure, response)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_BAD_CREDENTIALS)

    if not user.is_active:
        # 停用账号同样计入失败：否则被停用的高权限号会成为爆破练手靶子
        throttle_failure(payload.username, ip)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被停用")

    throttle_success(payload.username, ip)
    token = create_access_token(str(user.id), user.role, {"username": user.username})
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="login",
            target="auth",
            ip=ip,
        )
    )
    db.commit()
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_session)):
    if not get_config("auth.allow_register"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="管理员已关闭注册")

    min_len = get_config("auth.min_password_length")
    if len(payload.password) < min_len:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"密码至少 {min_len} 位",
        )

    exists = db.exec(select(User).where(User.username == payload.username)).first()
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")

    user = User(
        username=payload.username,
        display_name=payload.display_name or payload.username,
        password_hash=hash_password(payload.password),
        role="user",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="register",
            target="auth",
            ip=request.client.host if request.client else None,
        )
    )
    db.commit()

    token = create_access_token(str(user.id), user.role, {"username": user.username})
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user_allow_pending_password)):
    return UserOut.model_validate(user)


@router.post("/password", response_model=UserOut)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user_allow_pending_password),
    db: Session = Depends(get_session),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="当前密码错误",
        )
    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="新密码不能与当前密码相同",
        )
    min_len = int(get_config("auth.min_password_length") or 8)
    if len(payload.new_password) < min_len:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"密码至少 {min_len} 位",
        )

    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    db.add(user)
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="password_change",
            target="auth",
            ip=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(user)
    _reset_bootstrap_cache()
    return UserOut.model_validate(user)


# ---------------------------------------------------------------------------
# 登录页预填
# ---------------------------------------------------------------------------

#: 结果缓存。这个接口不需要登录，而里面要做一次 bcrypt 校验（默认约 250ms），
#: 不加缓存就等于给了一个未鉴权的 CPU 放大器。口令只能在重启时通过环境变量
#: 改变（没有改密接口），所以 TTL 取小一点即可兼顾正确性。
_BOOTSTRAP_CACHE: dict[str, Any] = {"ts": 0.0, "value": None}
_BOOTSTRAP_TTL_SECONDS = 30.0


def _reset_bootstrap_cache() -> None:
    """清空预填缓存（供测试与配置变更后调用）。"""
    _BOOTSTRAP_CACHE["ts"] = 0.0
    _BOOTSTRAP_CACHE["value"] = None


@router.get("/bootstrap", response_model=AuthBootstrapOut)
def bootstrap(db: Session = Depends(get_session)):
    """登录页预填用的初始凭据信息。

    只在管理员**仍在使用出厂默认口令**时才把口令回传，改过密码后一律为 None。
    这样既能「一键登录」，又不会把真实口令暴露给任何未登录的访问者，
    也不会让用户对着一个已失效的预填值反复失败。
    """
    now = time.time()
    cached = _BOOTSTRAP_CACHE["value"]
    if cached is not None and now - float(_BOOTSTRAP_CACHE["ts"]) < _BOOTSTRAP_TTL_SECONDS:
        return cached

    env_username, env_password = admin_credentials_from_env()
    admin = db.exec(select(User).where(User.role == "admin")).first()

    using_default = bool(
        admin is not None
        and admin.username == env_username
        and verify_password(env_password, admin.password_hash)
    )

    result = AuthBootstrapOut(
        # 用户名照常回传：它是公开的默认值，预填省一次输入。
        default_username=admin.username if admin is not None else env_username,
        default_password=env_password if using_default else None,
        using_default_password=using_default,
    )
    _BOOTSTRAP_CACHE["ts"] = now
    _BOOTSTRAP_CACHE["value"] = result
    return result
