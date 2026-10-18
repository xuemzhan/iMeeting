"""pytest 公共夹具。

设计要点
--------
- **绝不碰生产库**：每个测试用独立的临时 SQLite 文件，测试结束即删除。
- **不加载模型**：单测只验证协议、鉴权、配置、导出等纯逻辑；
  需要真实模型的场景由 ``scripts/`` 下的端到端脚本覆盖。
- **配置隔离**：``ConfigStore`` 的内存缓存按测试重置，避免用例间串味。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

# 保证 `import imeeting.*` 可用（pytest 从 tests/ 目录运行时 cwd 不一定是 backend/）
BACKEND_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = BACKEND_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

# 必须在导入 imeeting 之前设定，保证 imeeting.db 读到的是临时路径
_TMPDIR = tempfile.mkdtemp(prefix="meeting-test-")
_TEST_DB = Path(_TMPDIR) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret-key-for-pytest-only-0123456789abcdef"
os.environ["ASR_MODE"] = "off"  # 单测不加载任何模型
# 业务日志写到临时目录。否则 `imeeting.main` 导入时会把测试里的异常栈写进
# 生产 `data/logs/backend.log`，真实故障排查时会被测试噪声干扰。
os.environ["LOG_FILE"] = str(Path(_TMPDIR) / "backend-test.log")
# bcrypt 默认 12 轮约 250ms/次，整个套件有几十次哈希，会把测试拖到 6 分钟以上。
# 降到 4 轮（bcrypt 允许的最低值）纯为提速，不影响被测逻辑正确性。
os.environ["BCRYPT_ROUNDS"] = "4"
# 数百个用例都会重建临时库；Alembic 的 CLI 启动开销会主导测试时间。
# 生产启动仍执行版本迁移，单测专注业务逻辑。
os.environ["MEETING_SKIP_ALEMBIC"] = "1"
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
for _p in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_p, None)


# ---------------------------------------------------------------------------
# .env 污染隔离
# ---------------------------------------------------------------------------
# ``settings.py`` 在导入时执行 ``load_dotenv(BASE_DIR / ".env")``，把开发机上的
# 真实配置（尤其是 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL）写进 os.environ。
# 而 ``ENV_OVERRIDES`` 让环境变量优先级**高于**数据库覆盖，于是：
#   * ``test_remote_endpoint_without_key_not_configured`` 设 api_key="" 期望未配置，
#     却被环境里的真实 key 顶回 True；
#   * ``test_mask_value_does_not_overwrite_secret`` 期望读到自己写入的假 key，
#     真实 key 优先级更高，断言失败。
# 结论：本地一旦配了 .env，这两个用例必然红——是测试隔离缺失，不是产品缺陷。
#
# 注意**不能只在模块级 pop**：``settings.py`` 是懒导入的（由 ``reset_db`` 夹具里
# 的 ``from imeeting.db import ...`` 触发），那时 ``load_dotenv`` 才真正把这些键写回
# ``os.environ``，模块级清理会被它覆盖。必须在每个用例运行前清。
_ENV_KEYS_SHADOWED_BY_DOTENV = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "FUNASR_DEVICE",
    "FUNASR_NCPU",
    "MAX_CONCURRENT_SESSIONS",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """每个用例前清掉会被 .env 击穿配置优先级的那些环境变量。

    必须在**每个用例**都执行，而不是模块级一次性 pop：
    ``settings.py`` 是懒导入的，``load_dotenv`` 到那时才把 .env 的键写进
    ``os.environ``，一次性清理会被它覆盖掉。

    与 ``_reset_config_caches`` 的先后顺序也有讲究——``settings.config_store.get``
    每次读取都会实时查环境变量，所以只要在本用例开始前清干净，
    环境变量层就不会干扰「数据库覆盖」与「代码默认值」的断言。
    """
    for _k in _ENV_KEYS_SHADOWED_BY_DOTENV:
        monkeypatch.delenv(_k, raising=False)
    yield


@pytest.fixture(scope="session")
def db_path() -> Path:
    return _TEST_DB


@pytest.fixture(autouse=True)
def reset_db(db_path: Path, _isolate_env: None) -> Iterator[None]:
    """每个测试前重建库，保证用例之间完全独立。

    显式依赖 ``_isolate_env``：``reset_db`` 里会 ``from imeeting.db import ...``，
    这是 ``settings.py``（进而 ``load_dotenv``）的首次导入时机。
    如果不先把 .env 带来的环境变量清掉，它们会在导入瞬间生效并击穿
    配置优先级——表现为「本地配了 .env 就必红」的那两个用例。
    """
    from sqlmodel import SQLModel

    from imeeting.application.bootstrap import initialize_app_database
    from imeeting.db import engine
    from imeeting.settings import config_store

    SQLModel.metadata.drop_all(engine)
    # FTS5 虚拟表不在 metadata 里；先释放连接，Windows 才能删除旧库文件
    engine.dispose()
    if db_path.exists():
        try:
            db_path.unlink()
        except PermissionError:
            pass

    # 清掉数据库覆盖层，避免用例间互相污染（环境变量层不受影响）
    config_store._db_overrides = {}
    _reset_config_caches()

    initialize_app_database()

    yield

    SQLModel.metadata.drop_all(engine)
    config_store._db_overrides = {}
    _reset_config_caches()


def _reset_config_caches() -> None:
    """清掉依赖配置的模块级缓存。

    ``login_throttle`` 会把 ``auth.*`` 限流配置缓存在模块变量里；
    若不在用例之间清掉，前一个用例设的阈值会漏到后一个用例。
    """
    from imeeting import login_throttle

    login_throttle.reset_config_cache()


@pytest.fixture(autouse=True)
def no_model_warmup(monkeypatch) -> None:
    """禁止任何测试触发真实模型预热。

    ``imeeting.main`` 的 lifespan 会调用 ``warmup_async()``，在后台线程里加载
    真实 FunASR 权重（约 30s + 数 GB 内存）。测试绝不能做这件事：
    既慢，又会通过并发写 ``pool._models`` 污染依赖模型池状态的用例。
    """
    monkeypatch.setenv("SKIP_MODEL_WARMUP", "1")


@pytest.fixture(autouse=True)
def clean_model_pool() -> Iterator[None]:
    """每个测试前后清空模型池，避免用例间通过 _models 串味。"""
    from imeeting.engine import pool

    saved_models = dict(pool._models)
    saved_keys = dict(pool._build_keys)
    saved_executor = pool._EXECUTOR
    saved_threads = pool._EXECUTOR_THREADS
    pool._models.clear()
    pool._build_keys.clear()
    pool._EXECUTOR = None
    pool._EXECUTOR_THREADS = 0

    yield

    pool._models.clear()
    pool._models.update(saved_models)
    pool._build_keys.clear()
    pool._build_keys.update(saved_keys)
    pool._EXECUTOR = saved_executor
    pool._EXECUTOR_THREADS = saved_threads


@pytest.fixture(autouse=True)
def clean_active_sessions() -> Iterator[None]:
    """重置 WebSocket 活跃会话登记表。

    ``imeeting.session_registry.session_registry`` 持有进程内活跃会话。
    若某个用例留下的会话没被清理，后面断言 ``active_sessions`` 的用例就会
    莫名失败——这是跨文件污染，不是产品问题。这里每个用例前后都清一次。
    """
    from imeeting.session_registry import session_registry

    session_registry.clear()
    yield
    session_registry.clear()


@pytest.fixture(autouse=True)
def clean_login_throttle() -> Iterator[None]:
    """重置登录防爆破状态。

    ``app.login_throttle`` 用进程内存记录 (用户名, IP) 失败次数。整个套件里
    ``TestClient`` 的来源 IP 恒为 ``testclient``，于是所有用例共用同一个 IP 桶：
    前面的鉴权/配置用例各登录几次，累计就能把后面的用例顶进退避，
    表现为「单独跑通过、全量跑失败」的诡异现象。

    这里每个用例前后都清一次，让限流只在本用例内部生效。
    """
    from imeeting import login_throttle

    login_throttle.reset_state()
    yield
    login_throttle.reset_state()


@pytest.fixture(autouse=True)
def clean_bootstrap_cache() -> Iterator[None]:
    """重置登录页预填缓存。

    ``routers/auth._BOOTSTRAP_CACHE`` 是模块级缓存（TTL 30s），而整个套件跑完
    远不到 30s。于是第一个用例缓存下的「口令仍是出厂默认」会被后面所有用例读到，
    表现为「明明改了密码，bootstrap 还回传旧口令」这种假失败。
    """
    from imeeting.api.http.routes import auth as auth_router

    auth_router._reset_bootstrap_cache()
    yield
    auth_router._reset_bootstrap_cache()


@pytest.fixture
def admin_user(reset_db: None):
    """确保存在 admin 账号并返回它。"""
    from sqlmodel import Session, select

    from imeeting.db import engine
    from imeeting.models import User
    from imeeting.security import hash_password

    with Session(engine) as db:
        user = db.exec(select(User).where(User.username == "admin")).first()
        if user is None:
            user = User(
                username="admin",
                display_name="管理员",
                password_hash=hash_password("admin123"),
                role="admin",
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        db.expunge(user)
        return user


@pytest.fixture
def client(admin_user):
    """FastAPI TestClient（同步，便于断言）。"""
    from fastapi.testclient import TestClient

    from imeeting.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin_token(client) -> str:
    resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture
def auth_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}
