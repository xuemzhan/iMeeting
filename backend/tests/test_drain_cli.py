"""排空 CLI（IM-PLN-004 §6.1）的测试。

为什么单独测这段：它原先只是 Windows 上的一份 PowerShell 脚本——登录、触发 drain、
轮询、超时判定各写一遍，Linux 侧再抄一份必然漂移。现在只有一份实现，
用 ``MockTransport`` 就能把每个分支（等到归零 / 超时仍有会话 / 令牌缺失）钉住，
不需要真的起一个服务。
"""

from __future__ import annotations

import sys

import httpx
import pytest

from imeeting import sqlite_backup as module
from imeeting.sqlite_backup import DrainError, drain_server


def _transport(active_sequence: list[int], seen: list[tuple[str, str]]) -> httpx.MockTransport:
    states = list(active_sequence)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/auth/login"):
            return httpx.Response(200, json={"access_token": "t0k"})
        if path.endswith("/maintenance/drain"):
            return httpx.Response(200, json={"ok": True})
        if path.endswith("/maintenance"):
            return httpx.Response(
                200,
                json={"active_sessions": states.pop(0) if states else 0},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_drain_polls_until_no_active_sessions() -> None:
    seen: list[tuple[str, str]] = []
    client = httpx.Client(transport=_transport([2, 1, 0], seen), base_url="http://x")

    assert drain_server("http://x", "admin", "pw", poll_seconds=0, client=client) == 0
    # 顺序必须是：先登录 → 再触发 drain → 最后才轮询状态
    assert seen[0] == ("POST", "/api/v1/auth/login")
    assert seen[1] == ("POST", "/api/v1/admin/maintenance/drain")
    assert [path for _, path in seen[2:]] == ["/api/v1/admin/maintenance"] * 3


def test_drain_reports_remaining_sessions_on_timeout() -> None:
    """超时必须把"还有几个会话"报上去，否则发布脚本会以为可以重启了。"""
    seen: list[tuple[str, str]] = []
    client = httpx.Client(transport=_transport([1, 1, 1], seen), base_url="http://x")

    active = drain_server(
        "http://x", "admin", "pw", timeout_seconds=0, poll_seconds=0, client=client
    )

    assert active == 1


def test_drain_requires_a_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    with pytest.raises(DrainError, match="access_token"):
        drain_server("http://x", "admin", "pw", poll_seconds=0, client=client)


def test_cli_exits_zero_after_drain(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["imeeting-cli", "drain", "--password", "pw"])
    monkeypatch.setattr(module, "drain_server", lambda *a, **k: 0)

    assert module._main() == 0
    assert "drained=ok" in capsys.readouterr().out


def test_cli_exits_nonzero_when_sessions_remain(monkeypatch, capsys) -> None:
    """非零退出码是发布流水线的唯一门禁，不能悄悄返回 0。"""
    monkeypatch.setattr(sys, "argv", ["imeeting-cli", "drain", "--password", "pw"])
    monkeypatch.setattr(module, "drain_server", lambda *a, **k: 2)

    assert module._main() == 3
    assert "仍有 2 个活跃会话" in capsys.readouterr().err
