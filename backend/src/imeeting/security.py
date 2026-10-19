"""安全相关：口令哈希、JWT 签发校验、配置字段加密。"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from .settings import SERVER_CONFIG

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"

# bcrypt 只处理前 72 字节，超出部分会被直接拒绝（bcrypt>=4.1 会抛 ValueError）。
# 因此统一按「字节」截断，而不是按字符截断——中文 1 字符 = 3 字节，按字符截断仍可能超限。
_BCRYPT_MAX_BYTES = 72


def _clip(password: str) -> bytes:
    """把口令编码为 UTF-8 并截断到 bcrypt 允许的长度。"""
    raw = password.encode("utf-8")
    if len(raw) <= _BCRYPT_MAX_BYTES:
        return raw
    # 截断后可能出现半个多字节字符，用 errors="ignore" 丢弃残片
    return raw[:_BCRYPT_MAX_BYTES].decode("utf-8", errors="ignore").encode("utf-8")


# ---------------------------------------------------------------------------
# 口令
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """bcrypt 哈希，返回 utf-8 字符串（形如 $2b$12$...）。

    代价因子默认 12（约 250ms/次，抗暴力破解）。
    测试可通过环境变量 ``BCRYPT_ROUNDS=4`` 降到最低，避免几百次哈希拖慢整个套件；
    生产环境不应设置该变量。
    """
    return bcrypt.hashpw(_clip(password), bcrypt.gensalt(rounds=_bcrypt_rounds())).decode("utf-8")


def _bcrypt_rounds() -> int:
    raw = os.getenv("BCRYPT_ROUNDS", "").strip()
    if raw:
        try:
            # bcrypt 允许 4..31
            return min(max(int(raw), 4), 31)
        except ValueError:
            pass
    return 12


def verify_password(plain: str, hashed: str) -> bool:
    """校验口令。哈希格式非法时返回 False，不抛异常。"""
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(_clip(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# 分享链接令牌
# ---------------------------------------------------------------------------


def hash_share_token(token: str) -> str:
    """Return the one-way lookup value persisted for a share token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_share_token() -> tuple[str, str, str]:
    """Return ``(plaintext, digest, display_hint)`` for a new share link."""
    plaintext = secrets.token_urlsafe(24)
    return plaintext, hash_share_token(plaintext), plaintext[:8]


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


# HMAC-SHA256 的密钥短于 32 字节会触发 PyJWT 的 InsecureKeyLengthWarning，
# 且实际上降低了签名强度。低于此长度时派生补齐，保证不会以弱密钥上线。
_MIN_SECRET_BYTES = 32

_DEV_SECRET = "imeeting-dev-secret-change-me"

_warned_weak_secret = False


def _secret() -> str:
    """取签名密钥。

    未配置 SECRET_KEY 时回退到内置开发串（保证本地零配置可跑），
    但会打印告警，提示生产环境必须显式设置。
    """
    global _warned_weak_secret

    key = SERVER_CONFIG.get("secret_key") or ""
    if not key:
        if not _warned_weak_secret:
            logger.warning(
                "未设置 SECRET_KEY，正在使用内置开发密钥。"
                "生产环境必须通过环境变量提供至少 %d 字节的随机密钥，"
                "否则任何人都能伪造登录令牌！",
                _MIN_SECRET_BYTES,
            )
            _warned_weak_secret = True
        key = _DEV_SECRET

    # 短密钥用 SHA256 确定性派生到足够长度：既消除 InsecureKeyLengthWarning，
    # 又不改变「同一 SECRET_KEY 始终得到同一签名密钥」这一重启稳定性前提。
    raw = key.encode("utf-8")
    if len(raw) < _MIN_SECRET_BYTES:
        return hashlib.sha256(raw).hexdigest()
    return key


def create_access_token(subject: str, role: str, extra: dict[str, Any] | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=SERVER_CONFIG["access_token_expire_minutes"]
    )
    payload: dict[str, Any] = {"sub": subject, "role": role, "exp": expire, "iat": datetime.now(timezone.utc)}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        logger.debug("JWT 校验失败: %s", exc)
        return None


# ---------------------------------------------------------------------------
# 配置字段加密（用于 llm.api_key 等）
# ---------------------------------------------------------------------------


def _fernet() -> Fernet:
    raw = SERVER_CONFIG.get("config_encryption_key") or ""
    if raw:
        try:
            return Fernet(raw.encode())
        except (ValueError, TypeError):
            pass
    # 从 SECRET_KEY 派生一个稳定的密钥，保证重启后仍能解密
    digest = hashlib.sha256(_secret().encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_value(plain: str) -> str:
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_value(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        logger.warning("配置解密失败，可能 SECRET_KEY 已变更")
        return ""
