"""鉴权接口测试：登录、令牌校验、权限边界。"""

from __future__ import annotations


class TestLogin:
    def test_login_success(self, client, admin_user):
        resp = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"].lower() == "bearer"
        assert body["user"]["username"] == "admin"
        assert body["user"]["role"] == "admin"

    def test_login_never_leaks_password_hash(self, client, admin_user):
        resp = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
        )
        assert "password" not in resp.text.lower().replace("password_hash", "")

    def test_wrong_password_401(self, client, admin_user):
        resp = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "wrong"}
        )
        assert resp.status_code == 401

    def test_unknown_user_401(self, client, admin_user):
        resp = client.post(
            "/api/v1/auth/login", json={"username": "nobody", "password": "whatever"}
        )
        assert resp.status_code == 401

    def test_same_error_for_bad_user_and_bad_password(self, client, admin_user):
        """用户名枚举防护：两种失败必须返回同样的提示。"""
        a = client.post("/api/v1/auth/login", json={"username": "nobody", "password": "x"})
        b = client.post("/api/v1/auth/login", json={"username": "admin", "password": "x"})
        assert a.status_code == b.status_code == 401
        assert a.json().get("detail") == b.json().get("detail")

    def test_missing_fields_422(self, client, admin_user):
        assert client.post("/api/v1/auth/login", json={}).status_code == 422
        assert client.post("/api/v1/auth/login", json={"username": "admin"}).status_code == 422

    def test_malformed_json_422(self, client, admin_user):
        resp = client.post(
            "/api/v1/auth/login",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_inactive_user_cannot_login(self, client, admin_user):
        from sqlmodel import Session

        from imeeting.db import engine
        from imeeting.models import User

        with Session(engine) as db:
            user = db.get(User, admin_user.id)
            user.is_active = False
            db.add(user)
            db.commit()

        resp = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
        )
        assert resp.status_code in (401, 403)


class TestMe:
    def test_me_requires_token(self, client, admin_user):
        assert client.get("/api/v1/auth/me").status_code == 401

    def test_me_with_valid_token(self, client, auth_headers):
        resp = client.get("/api/v1/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["username"] == "admin"

    def test_me_rejects_forged_token(self, client, admin_user):
        resp = client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer forged.token.here"}
        )
        assert resp.status_code == 401

    def test_me_rejects_wrong_scheme(self, client, admin_user):
        resp = client.get("/api/v1/auth/me", headers={"Authorization": "Basic abc"})
        assert resp.status_code == 401

    def test_me_rejects_token_of_deleted_user(self, client, admin_user):
        from imeeting.security import create_access_token

        token = create_access_token("999999", "user")
        resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401


class TestAdminBoundary:
    def test_normal_user_cannot_read_admin_config_schema(self, client, admin_user):
        from sqlmodel import Session

        from imeeting.db import engine
        from imeeting.models import User
        from imeeting.security import hash_password

        with Session(engine) as db:
            db.add(
                User(
                    username="bob",
                    display_name="Bob",
                    password_hash=hash_password("bob12345"),
                    role="user",
                )
            )
            db.commit()

        token = client.post(
            "/api/v1/auth/login", json={"username": "bob", "password": "bob12345"}
        ).json()["access_token"]

        resp = client.get(
            "/api/v1/config/schema", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403

    def test_normal_user_cannot_write_config(self, client, admin_user):
        from sqlmodel import Session

        from imeeting.db import engine
        from imeeting.models import User
        from imeeting.security import hash_password

        with Session(engine) as db:
            db.add(
                User(
                    username="carol",
                    display_name="Carol",
                    password_hash=hash_password("carol123"),
                    role="user",
                )
            )
            db.commit()

        token = client.post(
            "/api/v1/auth/login", json={"username": "carol", "password": "carol123"}
        ).json()["access_token"]

        resp = client.put(
            "/api/v1/config",
            json={"values": {"asr.mode": "off"}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    def test_register_disabled_by_default(self, client, admin_user):
        """默认不开放注册，接口应明确拒绝而不是静默成功。"""
        resp = client.post(
            "/api/v1/auth/register",
            json={"username": "newbie", "password": "newbie12345"},
        )
        assert resp.status_code == 403

    def test_register_works_when_enabled(self, client, admin_user):
        """管理员打开开关后注册应当可用。"""
        client.put(
            "/api/v1/config",
            json={"values": {"auth.allow_register": True}},
            headers=_admin_headers(client),
        )
        resp = client.post(
            "/api/v1/auth/register",
            json={"username": "newbie", "password": "newbie12345"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["user"]["username"] == "newbie"
        assert resp.json()["user"]["role"] == "user"

    def test_register_rejects_short_password(self, client, admin_user):
        """短口令必须在进业务逻辑前就被拒（400 业务校验 或 422 模式校验均可）。"""
        client.put(
            "/api/v1/config",
            json={"values": {"auth.allow_register": True}},
            headers=_admin_headers(client),
        )
        resp = client.post(
            "/api/v1/auth/register", json={"username": "shorty", "password": "123"}
        )
        assert resp.status_code in (400, 422)

    def test_register_rejects_duplicate_username(self, client, admin_user):
        client.put(
            "/api/v1/config",
            json={"values": {"auth.allow_register": True}},
            headers=_admin_headers(client),
        )
        first = client.post(
            "/api/v1/auth/register", json={"username": "dupe", "password": "dupe12345"}
        )
        assert first.status_code == 201
        second = client.post(
            "/api/v1/auth/register", json={"username": "dupe", "password": "dupe12345"}
        )
        assert second.status_code == 409

    def test_register_cannot_self_assign_admin(self, client, admin_user):
        """提权防护：请求体里塞 role 不应生效。"""
        client.put(
            "/api/v1/config",
            json={"values": {"auth.allow_register": True}},
            headers=_admin_headers(client),
        )
        resp = client.post(
            "/api/v1/auth/register",
            json={"username": "sneaky", "password": "sneaky12345", "role": "admin"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["user"]["role"] == "user"


class TestBootstrap:
    """登录页预填接口：``GET /api/v1/auth/bootstrap``。

    这条接口是**公开**的（登录页在未登录状态下调用），所以它的安全边界只有一条：
    除了「仍在使用出厂默认口令」这一种情况，绝不能再把口令回传给任何人。
    """

    def test_public_and_returns_factory_credentials(self, client, admin_user):
        resp = client.get("/api/v1/auth/bootstrap")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["default_username"] == "admin"
        assert body["default_password"] == "admin123"
        assert body["using_default_password"] is True

    def test_prefilled_password_actually_logs_in(self, client, admin_user):
        """预填的值必须真能登进去，否则等于给用户挖坑。"""
        body = client.get("/api/v1/auth/bootstrap").json()
        resp = client.post(
            "/api/v1/auth/login",
            json={
                "username": body["default_username"],
                "password": body["default_password"],
            },
        )
        assert resp.status_code == 200, resp.text

    def test_password_hidden_after_admin_changes_it(self, client, admin_user):
        _set_admin_password("admin", "brand-new-password-9")

        body = client.get("/api/v1/auth/bootstrap").json()
        assert body["using_default_password"] is False
        assert body["default_password"] is None
        # 用户名仍然照常回传：它是公开的默认值，预填省一次输入
        assert body["default_username"] == "admin"

    def test_hidden_password_must_not_be_the_old_one(self, client, admin_user):
        """改密后回传的字段里不能残留旧口令（哪怕在别的键里）。"""
        _set_admin_password("admin", "brand-new-password-9")
        resp = client.get("/api/v1/auth/bootstrap")
        assert "admin123" not in resp.text

    def test_never_leaks_password_hash(self, client, admin_user):
        resp = client.get("/api/v1/auth/bootstrap")
        assert "password_hash" not in resp.text

    def test_no_admin_row_falls_back_to_env_defaults(self, client, admin_user):
        """管理员行被删掉时，仍应给出可用的出厂账号，而不是报错。"""
        from sqlmodel import Session, select

        from imeeting.db import engine
        from imeeting.models import User

        with Session(engine) as db:
            for row in db.exec(select(User)).all():
                db.delete(row)
            db.commit()

        body = client.get("/api/v1/auth/bootstrap").json()
        assert body["default_username"] == "admin"
        assert body["using_default_password"] is False
        assert body["default_password"] is None

    def test_follows_env_overridden_admin(self, client, admin_user, monkeypatch):
        """用环境变量换了出厂账号时，预填要跟着变，不能写死 admin/admin123。"""
        from sqlmodel import Session, select

        from imeeting.db import engine, init_db
        from imeeting.models import User

        monkeypatch.setenv("ADMIN_USERNAME", "ops")
        monkeypatch.setenv("ADMIN_PASSWORD", "ops-secret-123")

        with Session(engine) as db:
            for row in db.exec(select(User).where(User.role == "admin")).all():
                db.delete(row)
            db.commit()

        init_db()  # 重新按环境变量播种管理员

        body = client.get("/api/v1/auth/bootstrap").json()
        assert body["default_username"] == "ops"
        assert body["default_password"] == "ops-secret-123"
        assert body["using_default_password"] is True


def _set_admin_password(username: str, new_password: str) -> None:
    """直接改库里的口令（产品没有改密接口，这里模拟「用户已改过密码」）。"""
    from sqlmodel import Session, select

    from imeeting.db import engine
    from imeeting.models import User
    from imeeting.security import hash_password

    with Session(engine) as db:
        user = db.exec(select(User).where(User.username == username)).first()
        assert user is not None
        user.password_hash = hash_password(new_password)
        db.add(user)
        db.commit()


def _admin_headers(client) -> dict[str, str]:
    token = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
