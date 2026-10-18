"""鉴权与安全测试：口令哈希、JWT、权限边界。"""

from __future__ import annotations

import time

import pytest

from imeeting.security import (
    create_access_token,
    decode_access_token,
    decrypt_value,
    encrypt_value,
    hash_password,
    verify_password,
)

# ---------------------------------------------------------------------------
# 口令哈希
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    def test_roundtrip(self):
        hashed = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", hashed) is True

    def test_wrong_password_rejected(self):
        hashed = hash_password("admin123")
        assert verify_password("admin124", hashed) is False

    def test_salt_is_random(self):
        """同一口令两次哈希必须不同（否则说明没加盐）。"""
        a = hash_password("same-password")
        b = hash_password("same-password")
        assert a != b
        assert verify_password("same-password", a)
        assert verify_password("same-password", b)

    def test_hash_is_bcrypt_not_plaintext(self):
        hashed = hash_password("secret")
        assert hashed.startswith("$2")
        assert "secret" not in hashed

    def test_empty_password_rejected(self):
        assert verify_password("", hash_password("x")) is False

    def test_garbage_hash_does_not_raise(self):
        """非法哈希串必须返回 False 而不是抛异常（防呆）。"""
        assert verify_password("anything", "not-a-hash") is False
        assert verify_password("anything", "") is False

    @pytest.mark.parametrize("length", [100, 200, 500])
    def test_long_ascii_password(self, length):
        """超过 72 字节的长口令不能崩，应当被字节截断后可用（防呆）。"""
        pw = "a" * length
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True

    def test_long_chinese_password(self):
        """中文 1 字符 = 3 字节，按字符截断会超 72 字节导致 ValueError。

        这是 passlib 时代的真实崩溃点，必须保证不再复现。
        """
        pw = "会议记录" * 30  # 120 字符 = 360 字节
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True

    def test_password_beyond_72_bytes_shares_prefix(self):
        """按字节截断的已知语义：前 72 字节相同则视为同一口令。

        这是 bcrypt 的固有限制，记录为显式契约而非 bug。
        """
        base = "x" * 72
        hashed = hash_password(base + "AAAA")
        assert verify_password(base + "BBBB", hashed) is True


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


class TestJwt:
    def test_roundtrip(self):
        token = create_access_token("42", "admin", {"username": "alice"})
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "42"
        assert payload["role"] == "admin"
        assert payload["username"] == "alice"

    def test_garbage_token_returns_none(self):
        assert decode_access_token("not.a.jwt") is None
        assert decode_access_token("") is None

    def test_tampered_token_rejected(self):
        token = create_access_token("1", "user")
        head, body, sig = token.split(".")
        forged = f"{head}.{body}.{'A' * len(sig)}"
        assert decode_access_token(forged) is None

    def test_expired_token_rejected(self, monkeypatch):
        from imeeting import security

        monkeypatch.setitem(security.SERVER_CONFIG, "access_token_expire_minutes", -1)
        token = security.create_access_token("1", "user")
        assert decode_access_token(token) is None

    def test_token_has_expiry_claim(self):
        payload = decode_access_token(create_access_token("1", "user"))
        assert payload is not None
        assert "exp" in payload
        assert payload["exp"] > time.time()


# ---------------------------------------------------------------------------
# 配置字段加密
# ---------------------------------------------------------------------------


class TestConfigEncryption:
    def test_roundtrip(self):
        secret = "sk-abcdef1234567890"
        token = encrypt_value(secret)
        assert token != secret
        assert decrypt_value(token) == secret

    def test_empty_stays_empty(self):
        assert encrypt_value("") == ""
        assert decrypt_value("") == ""

    def test_ciphertext_differs_each_time(self):
        """Fernet 带随机 IV，同一明文两次密文应不同。"""
        a = encrypt_value("same")
        b = encrypt_value("same")
        assert a != b
        assert decrypt_value(a) == decrypt_value(b) == "same"

    def test_invalid_token_returns_empty_not_raise(self):
        from imeeting.security import decrypt_value as dv

        assert dv("garbage-not-a-fernet-token") == ""

    def test_unicode_secret(self):
        secret = "密钥-中文-测试🔑"
        assert decrypt_value(encrypt_value(secret)) == secret
