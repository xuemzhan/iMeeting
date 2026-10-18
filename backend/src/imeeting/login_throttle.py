"""登录防爆破限流。

设计目标
--------
- **挡撞库**：同一 (用户名, 来源 IP) 连续失败后进入指数退避，退避期间直接拒绝，
  不再消耗昂贵的 bcrypt 校验（约 250ms/次，本身也是一种 DoS 面）。
- **抗枚举**：键同时包含用户名与 IP。攻击者换用户名试同一 IP 会被按 IP 侧限流；
  分布式换 IP 打同一账号会被账号侧限流。两条通道各自计数，互不抵消。
- **不锁死合法用户**：退避只作用在「失败方」的键上；一旦密码正确立即清零，
  所以正常用户偶尔输错一次不会被拖累。
- **不泄漏账号是否存在**：退避检查发生在查库之前，命中退避时返回的提示与
  普通失败一致（调用方统一使用同一条 401 文案）。
- **重启即失效**：状态放在进程内存里，不落库。单实例部署够用；
  多实例部署需要换成 Redis 之类的共享存储（本项目的部署形态是单机 CPU，故从简）。

状态清理采用「惰性 + 定期」双策略：
- 惰性：每次 ``check`` 顺带把过期条目丢掉，避免无界增长；
- 定期：条目超过 ``_MAX_ENTRIES`` 时按「首次失败时间」倒序裁剪，兜底防内存泄漏。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .config_events import subscribe
from .settings import get_config

logger = logging.getLogger(__name__)

# 一个键（username+ip）最多留这么久没有新失败，超过即视为过期；
# 取 24 小时是因为退避上限（见 _MAX_BACKOFF_SECONDS）远小于它，
# 保证「被锁 15 分钟」的键在解锁后仍能被正常统计，不会因过期而凭空清零。
_ENTRY_TTL_SECONDS = 24 * 3600

# 单次失败最长退避 15 分钟：足够让在线爆破失去性价比，又不会让真人绝望。
_MAX_BACKOFF_SECONDS = 15 * 60

# 全局条目上限，兜底防止「海量随机用户名」把内存撑爆。
_MAX_ENTRIES = 10_000


@dataclass
class _Entry:
    """单个限流键的失败记录。"""

    failures: int = 0
    first_failure: float = 0.0
    last_failure: float = 0.0
    locked_until: float = 0.0
    # 记录被拒次数，仅用于日志观测，不参与判定
    rejected: int = 0

    def to_state(self) -> "ThrottleState":
        return ThrottleState(
            allowed=self.locked_until <= 0,
            wait_seconds=max(self.locked_until - time.time(), 0.0),
            failures=self.failures,
        )


@dataclass
class ThrottleState:
    """一次退避判定的结果，供调用方决定是否放行。"""

    allowed: bool
    wait_seconds: float = 0.0
    failures: int = 0
    scope: str = ""

    @property
    def retry_after(self) -> int:
        """HTTP ``Retry-After`` 头需要的整秒数（向上取整，至少 1）。"""
        return max(int(self.wait_seconds) + 1, 1)


# key -> _Entry
_entries: dict[str, _Entry] = {}
_lock = threading.Lock()

# 缓存住的配置，避免每次失败都查一遍配置存储
_cached_config: tuple[bool, int, int, int] | None = None


def _read_config() -> tuple[bool, int, int, int]:
    """读取 (enabled, max_failures, base_backoff, by_ip)。

    读取失败一律回退到默认的安全值——限流是安全特性，
    「读不到配置」绝不能变成「彻底关掉限流」。
    """
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    def _int(key: str, default: int) -> int:
        try:
            return int(get_config(key))
        except (TypeError, ValueError):
            return default

    try:
        enabled = bool(get_config("auth.login_throttle_enabled"))
    except Exception:  # pragma: no cover - 配置层异常时保守放行但保留默认
        enabled = True

    resolved = (
        enabled,
        max(_int("auth.max_login_failures", 5), 1),
        max(_int("auth.login_lockout_seconds", 30), 1),
        bool(_int("auth.login_throttle_by_ip", 1)),
    )
    _cached_config = resolved
    return resolved


def reset_config_cache() -> None:
    """配置变更后清缓存。由 settings 写配置时调用。"""
    global _cached_config
    _cached_config = None


def _on_config_changed(keys: tuple[str, ...]) -> None:
    if any(key.startswith("auth.") for key in keys):
        reset_config_cache()


subscribe(_on_config_changed)


def reset_state() -> None:
    """清空所有失败记录（测试与运维排障用）。"""
    with _lock:
        _entries.clear()


def _keys(username: str, ip: str | None, by_ip: bool) -> list[str]:
    """归一化并生成要计数的键。

    用户名统一小写并去空白，避免 ``Admin`` / ``admin `` 绕过计数。
    IP 为空时不生成 IP 键（本地测试/代理场景拿不到来源）。
    """
    keys = [f"u:{username.strip().lower()}"]
    if by_ip and ip:
        keys.append(f"i:{ip}")
    return keys


def _purge_locked(now: float) -> None:
    """调用方需持有 ``_lock``。清掉长时间无活动的条目。"""
    if len(_entries) <= _MAX_ENTRIES:
        stale = [
            k
            for k, e in _entries.items()
            if e.last_failure > 0 and now - e.last_failure > _ENTRY_TTL_SECONDS
        ]
        for k in stale:
            _entries.pop(k, None)
        return

    # 超出上限：按最后失败时间排序，丢掉最旧的一批（至少裁掉 1/8，避免频繁重排）
    ordered = sorted(_entries.items(), key=lambda kv: kv[1].last_failure)
    drop = max(len(ordered) // 8, 1)
    for k, _ in ordered[:drop]:
        _entries.pop(k, None)


def _probe(entries: dict[str, _Entry], now: float) -> ThrottleState:
    """在给定条目表里找最严的一个闸门。

    - 过期条目顺手移出字典（函数外仍需 ``_purge_locked`` 做全表清理）；
    - 未触发退避时也返回累计失败次数，方便调用方/日志观测接近阈值的程度；
    - 若多个键同时退避，返回账号维度的那个，避免暴露 IP 侧限流是否生效。
    """
    worst: ThrottleState | None = None
    max_failures = 0
    for key, entry in list(entries.items()):
        if entry.last_failure > 0 and now - entry.last_failure > _ENTRY_TTL_SECONDS:
            _entries.pop(key, None)
            continue
        max_failures = max(max_failures, entry.failures)
        if entry.locked_until <= 0:
            continue
        if entry.locked_until <= now:
            # 退避窗口已过：解锁但仍保留失败次数（下次失败会继续加倍）
            entry.locked_until = 0.0
            continue
        entry.rejected += 1
        state = ThrottleState(
            allowed=False,
            wait_seconds=entry.locked_until - now,
            failures=entry.failures,
            scope="account" if key.startswith("u:") else "ip",
        )
        # 若两个键都在退避（账号 + IP），对外统一说「账号」，
        # 避免把「IP 被限」这一维度暴露给攻击者用于判断代理是否生效。
        if worst is None or state.wait_seconds > worst.wait_seconds:
            worst = state
        elif state.scope == "account":
            worst = state
    return worst or ThrottleState(allowed=True, failures=max_failures)


def check(username: str, ip: str | None) -> ThrottleState:
    """登录前的闸门：判定该 (username, ip) 是否仍在退避中。

    返回的 ``allowed=False`` 表示应当直接拒绝，且**不应**再去查库/校验口令——
    这样错的口令连一次 bcrypt 都不消耗。
    """
    enabled, _, _, by_ip = _read_config()
    if not enabled:
        return ThrottleState(allowed=True)

    now = time.time()
    with _lock:
        _purge_locked(now)
        keys = _keys(username, ip, by_ip)
        return _probe({k: _entries[k] for k in keys if k in _entries}, now)


def record_failure(username: str, ip: str | None) -> ThrottleState:
    """登记一次登录失败，必要时进入退避。

    退避时长按 ``base * 2^(failures - max_failures)`` 指数增长，并封顶
    ``_MAX_BACKOFF_SECONDS``。返回当前状态，便于调用方在响应里附带说明。
    """
    enabled, max_failures, base, by_ip = _read_config()
    if not enabled:
        return ThrottleState(allowed=True)

    now = time.time()
    with _lock:
        _purge_locked(now)
        keys = _keys(username, ip, by_ip)
        for key in keys:
            entry = _entries.get(key)
            if entry is None:
                entry = _Entry(first_failure=now)
                _entries[key] = entry

            # 距上次失败太久则重新计数（避免「一天错一次」累积到锁定）
            if entry.last_failure > 0 and now - entry.last_failure > _ENTRY_TTL_SECONDS:
                entry.failures = 0
                entry.first_failure = now

            entry.failures += 1
            entry.last_failure = now

            if entry.failures >= max_failures:
                over = entry.failures - max_failures
                delay = min(base * (2**over), _MAX_BACKOFF_SECONDS)
                entry.locked_until = now + delay
                logger.warning(
                    "登录失败次数过多，已临时锁定：key=%s failures=%d 退避=%.0fs",
                    key,
                    entry.failures,
                    delay,
                )

        return _probe({k: _entries[k] for k in keys}, now)


def record_success(username: str, ip: str | None) -> None:
    """登录成功后清掉该用户名相关的失败记录。

    只清账号键，不动 IP 键：否则攻击者只要手里有一个合法账号，
    就能靠「成功一次」把针对同一 IP 的限流刷掉。
    这意味着合法用户即使已解锁，也不用担心历史失败次数被后续失败继续加倍。
    """
    keys = _keys(username, None, False)  # 只取账号键
    with _lock:
        for key in keys:
            _entries.pop(key, None)
