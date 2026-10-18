"""登录防爆破限流测试。

分两层：
- ``TestThrottleUnit`` 直接打 ``login_throttle`` 模块，用 monkeypatch 冻结时间，
  验证指数退避、键隔离、成功清零等纯逻辑（快、确定）。
- ``TestLoginThrottleApi`` 走真实 HTTP，验证接口层的状态码、Retry-After 头、
  以及「限流不泄漏账号是否存在」。
"""

from __future__ import annotations

import pytest

from imeeting import login_throttle as lt


@pytest.fixture(autouse=True)
def clean_throttle():
    """每个用例前后清空限流状态与配置缓存，避免跨用例污染。"""
    lt.reset_state()
    lt.reset_config_cache()
    yield
    lt.reset_state()
    lt.reset_config_cache()


@pytest.fixture(autouse=True)
def throttle_config(request):
    """写入本用例需要的低阈值。

    不能简单依赖 ``reset_db``：``reset_db`` 是 autouse 的，pytest 会在
    测试函数所需的其它夹具（如 ``client``）之前就把它跑完，而 ``client``
    构造时会再次触碰配置，导致这里写下的覆盖层被清掉。改在「所有夹具
    都就绪之后」通过 ``request.getfixturevalue`` 显式确保 ``reset_db``
    已运行，再写覆盖并立刻校验生效。
    """
    request.getfixturevalue("reset_db")

    from imeeting.settings import config_store

    config_store._db_overrides.update(
        {
            "auth.login_throttle_enabled": True,
            "auth.max_login_failures": 3,
            "auth.login_lockout_seconds": 10,
            "auth.login_throttle_by_ip": True,
        }
    )
    lt.reset_config_cache()
    assert lt._read_config()[:3] == (True, 3, 10), "限流测试配置未生效"
    yield


class _Clock:
    """可手动推进的假时钟，替换 ``login_throttle.time.time``。"""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(lt.time, "time", c)
    return c


# ---------------------------------------------------------------------------
# 模块级逻辑
# ---------------------------------------------------------------------------


class TestThrottleUnit:
    def test_below_threshold_always_allowed(self, clock):
        """容忍次数以内不应拦截，但失败次数要正确累计。"""
        for _ in range(2):
            state = lt.record_failure("alice", "1.1.1.1")
            assert state.allowed is True
        assert state.failures == 2  # 账号键失败 2 次（阈值 3）

    def test_locks_at_threshold(self, clock):
        for _ in range(2):
            lt.record_failure("alice", "1.1.1.1")
        state = lt.record_failure("alice", "1.1.1.1")
        assert state.allowed is False
        assert state.wait_seconds == pytest.approx(10.0, abs=0.01)
        assert state.scope == "account"

    def test_check_blocks_while_locked(self, clock):
        for _ in range(3):
            lt.record_failure("alice", "1.1.1.1")
        state = lt.check("alice", "1.1.1.1")
        assert state.allowed is False
        assert state.wait_seconds == pytest.approx(10.0, abs=0.01)

    def test_backoff_grows_exponentially(self, clock):
        """超过阈值后每多失败一次，退避时间翻倍。"""
        waits = []
        for _ in range(5):
            state = lt.record_failure("alice", "1.1.1.1")
            if not state.allowed:
                waits.append(round(state.wait_seconds))
        # 第3/4/5次失败对应 base*1 / base*2 / base*4 = 10 / 20 / 40
        assert waits == [10, 20, 40]

    def test_backoff_capped(self, clock):
        """指数增长必须封顶，否则无界增长会变成永久拒绝服务。"""
        for _ in range(40):
            lt.record_failure("alice", "1.1.1.1")
        state = lt.check("alice", "1.1.1.1")
        assert state.wait_seconds <= lt._MAX_BACKOFF_SECONDS

    def test_lock_expires_and_remaining_failures_drive_longer_wait(self, clock):
        """退避到期后放行；再次失败应继续加倍而不是从头计数。"""
        for _ in range(3):
            lt.record_failure("alice", "1.1.1.1")
        clock.advance(11)
        assert lt.check("alice", "1.1.1.1").allowed is True

        state = lt.record_failure("alice", "1.1.1.1")
        assert state.allowed is False
        assert round(state.wait_seconds) == 20

    def test_success_clears_account_key(self, clock):
        """成功登录后账号键被清掉；此时换一个干净 IP 应能直接登录。"""
        for _ in range(3):
            lt.record_failure("alice", "1.1.1.1")
        lt.record_success("alice", "1.1.1.1")
        # 账号键已清除 -> 换 IP 不再受账号维度退避影响
        assert lt.check("alice", "2.2.2.2").allowed is True
        assert "u:alice" not in lt._entries

    def test_success_does_not_clear_ip_key(self, clock):
        """否则一个有合法账号的攻击者能靠成功登录刷掉 IP 侧限流。"""
        for _ in range(3):
            lt.record_failure("victim", "9.9.9.9")
        lt.record_success("attacker-own-account", "9.9.9.9")
        assert lt.check("victim", "9.9.9.9").allowed is False

    def test_different_usernames_same_ip_still_limited(self, clock):
        """换用户名轮着试：IP 键应触发限流。"""
        for name in ("u1", "u2", "u3"):
            lt.record_failure(name, "5.5.5.5")
        assert lt.check("u4", "5.5.5.5").allowed is False

    def test_same_username_different_ip_still_limited(self, clock):
        """分布式换 IP 打同一账号：账号键应触发限流。"""
        for ip in ("1.0.0.1", "1.0.0.2", "1.0.0.3"):
            lt.record_failure("admin", ip)
        assert lt.check("admin", "8.8.8.8").allowed is False

    def test_username_normalized(self, clock):
        """大小写 / 前后空格不能绕过计数。"""
        for name in ("Alice", "ALICE", " alice "):
            lt.record_failure(name, "1.1.1.1")
        assert lt.check("alice", "1.1.1.1").allowed is False

    def test_disabled_never_blocks(self, clock):
        from imeeting.settings import config_store

        config_store._db_overrides["auth.login_throttle_enabled"] = False
        lt.reset_config_cache()
        for _ in range(20):
            lt.record_failure("alice", "1.1.1.1")
        assert lt.check("alice", "1.1.1.1").allowed is True

    def test_by_ip_can_be_disabled(self, clock):
        from imeeting.settings import config_store

        config_store._db_overrides["auth.login_throttle_by_ip"] = False
        lt.reset_config_cache()
        for i in range(10):
            lt.record_failure(f"user{i}", "7.7.7.7")
        assert lt.check("another", "7.7.7.7").allowed is True

    def test_missing_ip_does_not_crash(self, clock):
        for _ in range(3):
            state = lt.record_failure("alice", None)
        assert state.allowed is False
        assert lt.check("alice", None).allowed is False

    def test_ip_not_shared_when_ip_missing(self, clock):
        """拿不到 IP 时不能把所有请求归到同一个桶里互相误伤。"""
        for _ in range(3):
            lt.record_failure("alice", None)
        assert lt.check("bob", None).allowed is True

    def test_stale_entry_pruned(self, clock):
        lt.record_failure("alice", "1.1.1.1")
        clock.advance(lt._ENTRY_TTL_SECONDS + 1)
        # 触发一次惰性清理
        lt.check("someone", None)
        assert all(
            not key.startswith("u:alice") for key in lt._entries
        ), "过期条目应被清理"

    def test_entry_count_bounded(self, clock, monkeypatch):
        """大量随机用户名不应让内存无界增长。"""
        monkeypatch.setattr(lt, "_MAX_ENTRIES", 50)
        for i in range(300):
            lt.record_failure(f"user{i}", None)
        assert len(lt._entries) <= 50

    def test_rejected_counter_increments(self, clock):
        for _ in range(2):
            lt.record_failure("alice", "1.1.1.1")
        lt.record_failure("alice", "1.1.1.1")  # 第 3 次触发退避，_probe 计一次
        lt.check("alice", "1.1.1.1")
        lt.check("alice", "1.1.1.1")
        assert lt._entries["u:alice"].rejected == 3

    def test_retry_after_rounds_up(self, clock):
        for _ in range(3):
            lt.record_failure("alice", "1.1.1.1")
        state = lt.check("alice", "1.1.1.1")
        clock.advance(0.4)
        state = lt.check("alice", "1.1.1.1")
        assert state.retry_after >= 1


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------


class TestLoginThrottleApi:
    def test_repeated_failures_eventually_429(self, client, admin_user):
        for _ in range(3):
            resp = client.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "nope"}
            )
        assert resp.status_code == 429, resp.text
        assert "Retry-After" in resp.headers

    def test_429_carries_retry_after(self, client, admin_user):
        for _ in range(3):
            resp = client.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "nope"}
            )
        assert int(resp.headers["Retry-After"]) >= 1

    def test_correct_password_rejected_while_locked(self, client, admin_user):
        """锁定期间即使密码正确也必须拒绝——否则退避形同虚设。"""
        for _ in range(3):
            client.post("/api/v1/auth/login", json={"username": "admin", "password": "nope"})
        resp = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
        )
        assert resp.status_code == 429

    def test_unknown_user_also_throttled(self, client, admin_user):
        """针对不存在的用户名爆破也应被挡，且不可据此推断账号是否存在。"""
        for _ in range(3):
            resp = client.post(
                "/api/v1/auth/login", json={"username": "ghost", "password": "x"}
            )
        assert resp.status_code == 429

    def test_failure_below_threshold_is_401_not_429(self, client, admin_user):
        resp = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "nope"}
        )
        assert resp.status_code == 401

    def test_success_after_failures_works(self, client, admin_user):
        """失败 2 次（未达阈值）后正确登录必须成功。"""
        for _ in range(2):
            client.post("/api/v1/auth/login", json={"username": "admin", "password": "nope"})
        resp = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
        )
        assert resp.status_code == 200, resp.text

    def test_success_resets_account_counter(self, client, admin_user):
        """成功登录清掉账号侧计数，但 IP 侧计数按设计保留。

        所以这里要断言的是「账号键消失」，而不是「整个来源都恢复。
        如果连 IP 键也清掉，手里有任意合法账号的人就能靠一次成功登录
        把针对该 IP 的限流刷干净，退避就白做了。
        """
        for _ in range(2):
            client.post("/api/v1/auth/login", json={"username": "admin", "password": "nope"})
        assert lt._entries["u:admin"].failures == 2

        client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert "u:admin" not in lt._entries
        assert lt._entries["i:testclient"].failures == 2

    def test_lockout_detail_does_not_leak_user_existence(self, client, admin_user):
        """不存在的用户名与真实用户名，被限流后的提示措辞必须一致。

        两条请求之间有几百毫秒间隔，``Retry-After`` 的整秒数可能差 1，
        所以只比对不含数字的部分——真正要防的是「措辞」泄漏，
        而不是秒数本身的微小抖动。
        """
        for _ in range(3):
            a = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "x"})
        for _ in range(3):
            b = client.post("/api/v1/auth/login", json={"username": "admin", "password": "x"})
        assert a.status_code == b.status_code == 429

        import re

        strip = lambda s: re.sub(r"\d+", "N", s)  # noqa: E731
        assert strip(a.json()["detail"]) == strip(b.json()["detail"])

    def test_failure_writes_audit_log(self, client, admin_user):
        from sqlmodel import Session, select

        from imeeting.db import engine
        from imeeting.models import AuditLog

        client.post("/api/v1/auth/login", json={"username": "admin", "password": "nope"})
        with Session(engine) as db:
            rows = db.exec(select(AuditLog).where(AuditLog.action == "login_failed")).all()
        assert len(rows) == 1
        assert rows[0].username == "admin"

    def test_disabling_throttle_via_config_takes_effect_immediately(self, client, admin_user):
        """管理员关掉限流后应立即生效，不需要重启。"""
        token = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
        ).json()["access_token"]
        resp = client.put(
            "/api/v1/config",
            json={"values": {"auth.login_throttle_enabled": False}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (200, 204), resp.text

        for _ in range(10):
            resp = client.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "nope"}
            )
        assert resp.status_code == 401
