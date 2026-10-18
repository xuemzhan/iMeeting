"""FunASR 模型池。

设计要点
--------
- **单例复用**：模型加载昂贵（首次数秒至数十秒），按 (模型名, 设备) 缓存复用。
- **懒加载 + 预热**：服务启动时后台线程预热，避免首个请求长时间等待。
- **线程隔离**：ASR 推算是同步阻塞的，统一走线程池，绝不阻塞事件循环。
- **配置感知**：模型参数变化时缓存自动失效重建。
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from ..settings import get_config

logger = logging.getLogger(__name__)

# 推理线程池：CPU 环境下 ASR 是重活，线程数需要与「最大并发会话数」匹配。
# 线程太少 → 并发会话排队；太多 → 与 torch 内部线程争抢反而变慢。
# 因此按配置延迟创建，并在配置变化时重建。
_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_THREADS: int = 0
_EXECUTOR_LOCK = threading.Lock()


def _resolve_threads() -> int:
    """决定推理线程数。

    优先级：显式配置 ``asr.infer_threads`` > 自动（CPU 核数-1）> 兜底 2。
    与 ``session.max_concurrent`` 取较大值，避免并发上限高于线程数导致排队。
    """
    try:
        configured = int(get_config("asr.infer_threads") or 0)
    except (TypeError, ValueError):
        configured = 0

    if configured > 0:
        return configured

    cpu = os.cpu_count() or 2
    auto = max(cpu - 1, 1)

    # 自动模式下，至少覆盖并发上限，否则超出部分必然排队
    try:
        concurrent = int(get_config("session.max_concurrent") or 4)
    except (TypeError, ValueError):
        concurrent = 4
    return max(auto, min(concurrent, 16))


def _get_executor() -> ThreadPoolExecutor:
    """按当前配置取线程池，配置变化时自动重建。"""
    global _EXECUTOR, _EXECUTOR_THREADS

    want = _resolve_threads()
    ex = _EXECUTOR
    if ex is not None and _EXECUTOR_THREADS == want:
        return ex

    with _EXECUTOR_LOCK:
        if _EXECUTOR is not None and _EXECUTOR_THREADS == want:
            return _EXECUTOR
        old = _EXECUTOR
        old_threads = _EXECUTOR_THREADS
        _EXECUTOR = ThreadPoolExecutor(max_workers=want, thread_name_prefix="asr")
        _EXECUTOR_THREADS = want
        logger.info("ASR 推理线程池已就绪：%d 个线程", want)
        if old is not None:
            # 旧池可能有任务在执行，交给解释器退出时回收，不在此处阻塞
            logger.info("推理线程数由 %d 调整为 %d，旧线程池待其任务结束后释放", old_threads, want)
    return _EXECUTOR

_lock = threading.Lock()
_models: dict[str, Any] = {}

# 记录每个模型实例是用什么配置构造的，配置变更时据此失效
_build_keys: dict[str, tuple] = {}


@dataclass
class ModelStatus:
    """模型加载状态，供健康检查与前端展示。"""

    name: str
    loaded: bool
    loading: bool
    error: str | None = None
    load_seconds: float = 0.0
    device: str = "cpu"


_status: dict[str, ModelStatus] = {}


def _pre_register_status(prefix: str) -> ModelStatus:
    """为某个模型登记状态对象（幂等），供预热阶段提前展示「加载中」。"""
    st = _status.get(prefix)
    if st is None:
        st = ModelStatus(name=prefix, loaded=False, loading=False)
        _status[prefix] = st
    return st


def _build_key(prefix: str) -> tuple:
    """构造影响模型实例的配置指纹。

    凡是「只在建模时读取一次」的配置都必须进来，否则管理员改了配置、
    模型却仍用旧参数跑，表现为「改了没反应」。
    """
    common = (
        get_config("asr.device"),
        get_config("asr.ncpu"),
        get_config("asr.hub"),
        get_config("asr.model_revision"),
        get_config("asr.disable_update"),
    )
    if prefix == "streaming":
        return (get_config("asr.streaming_model"), *common)
    return (
        get_config("asr.offline_model"),
        *common,
        get_config("punc.enabled"),
        get_config("vad.enabled"),
        get_config("spk.enabled"),
        get_config("asr.vad_model"),
        get_config("asr.punc_model"),
        get_config("asr.spk_model"),
        # max_single_segment_time 是 VAD 建模参数，改了必须重建才生效
        get_config("vad.max_segment_ms"),
    )


def _build_streaming_model() -> Any:
    """构造流式 ASR 模型。"""
    from funasr import AutoModel

    model_name = get_config("asr.streaming_model")
    device = get_config("asr.device")
    ncpu = get_config("asr.ncpu")

    logger.info("正在加载流式模型 %s (device=%s, ncpu=%s)", model_name, device, ncpu)
    started = time.time()
    model = AutoModel(
        model=model_name,
        device=device,
        ncpu=ncpu,
        hub=get_config("asr.hub") or "ms",
        model_revision=get_config("asr.model_revision") or "master",
        disable_update=bool(get_config("asr.disable_update")),
        disable_pbar=True,
    )
    logger.info("流式模型加载完成，耗时 %.1fs", time.time() - started)
    return model


def _build_offline_model() -> Any:
    """构造离线模型（含 VAD / 标点 / 说话人）。"""
    from funasr import AutoModel

    model_name = get_config("asr.offline_model")
    device = get_config("asr.device")
    ncpu = get_config("asr.ncpu")

    kwargs: dict[str, Any] = {
        "model": model_name,
        "device": device,
        "ncpu": ncpu,
        "hub": get_config("asr.hub") or "ms",
        "model_revision": get_config("asr.model_revision") or "master",
        "disable_update": bool(get_config("asr.disable_update")),
        "disable_pbar": True,
    }

    if get_config("vad.enabled"):
        kwargs["vad_model"] = get_config("asr.vad_model") or "fsmn-vad"
        kwargs["vad_kwargs"] = {
            "max_single_segment_time": get_config("vad.max_segment_ms"),
        }
    if get_config("punc.enabled"):
        kwargs["punc_model"] = get_config("asr.punc_model") or "ct-punc"
    if get_config("spk.enabled") and get_config("vad.enabled"):
        kwargs["spk_model"] = get_config("asr.spk_model") or "cam++"

    logger.info("正在加载离线模型 %s (device=%s)", model_name, device)
    started = time.time()
    model = AutoModel(**kwargs)
    logger.info("离线模型加载完成，耗时 %.1fs", time.time() - started)
    return model


_BUILDERS = {
    "streaming": _build_streaming_model,
    "offline": _build_offline_model,
}


def get_model(prefix: str) -> Any:
    """获取（必要时加载）指定类型的模型。线程安全。

    Args:
        prefix: "streaming" 或 "offline"

    Raises:
        KeyError: prefix 非法
        RuntimeError: 模型加载失败
    """
    if prefix not in _BUILDERS:
        raise KeyError(f"未知模型类型: {prefix}")

    key = _build_key(prefix)
    cached_key = _build_keys.get(prefix)

    # 命中缓存且配置未变
    if prefix in _models and cached_key == key:
        return _models[prefix]

    if prefix in _models and cached_key != key:
        logger.warning("配置已变更，重建 %s 模型", prefix)
        _models.pop(prefix, None)

    # 先无条件登记一个状态对象，保证「等待锁」的阶段也能被 get_status 看到，
    # 否则并发调用时前端会看到该模型仿佛根本没在加载
    status = _status.setdefault(prefix, ModelStatus(name=prefix, loaded=False, loading=False))

    with _lock:
        # 双重检查：可能在等锁期间已被其他线程加载
        if prefix in _models and _build_keys.get(prefix) == key:
            return _models[prefix]

        status.loading = True
        status.error = None
        try:
            started = time.time()
            model = _BUILDERS[prefix]()
            elapsed = time.time() - started
            _models[prefix] = model
            _build_keys[prefix] = key
            status.loaded = True
            status.load_seconds = elapsed
            status.device = get_config("asr.device")
            logger.info("模型 %s 就绪（%.1fs）", prefix, elapsed)
            return model
        except Exception as exc:
            status.error = f"{type(exc).__name__}: {exc}"
            logger.exception("模型 %s 加载失败", prefix)
            raise RuntimeError(f"模型加载失败: {status.error}") from exc
        finally:
            status.loading = False


async def run_in_executor(func, *args, **kwargs):
    """在线程池中执行同步函数，不阻塞事件循环。"""
    loop = asyncio.get_running_loop()
    if kwargs:
        from functools import partial

        func = partial(func, **kwargs)
    return await loop.run_in_executor(_get_executor(), func, *args)


def is_ready(prefix: str) -> bool:
    return prefix in _models


def get_status() -> dict[str, dict]:
    """返回所有模型的状态快照。"""
    out: dict[str, dict] = {}
    for name in _BUILDERS:
        st = _status.get(name)
        if st is None:
            out[name] = {
                "name": name,
                "loaded": name in _models,
                "loading": False,
                "error": None,
                "load_seconds": 0.0,
                "device": get_config("asr.device"),
            }
        else:
            out[name] = {
                "name": st.name,
                "loaded": st.loaded,
                "loading": st.loading,
                "error": st.error,
                "load_seconds": round(st.load_seconds, 2),
                "device": st.device,
            }
    return out


def warmup_async() -> None:
    """后台预热模型。服务启动时调用，避免首个请求长时间等待。

    仅 ``asr.mode=normal`` 且 ``asr.warmup_on_start`` 开启时才真的加载权重：
    ``echo``/``off`` 模式下用不到模型，此时加载会白占几 GB 内存和几十秒启动时间，
    在只有 CPU 的机器上尤其浪费。
    """

    def _worker() -> None:
        if not get_config("asr.warmup_on_start"):
            logger.info("asr.warmup_on_start=false，跳过模型预热")
            return
        mode = str(get_config("asr.mode") or "normal")
        if mode != "normal":
            logger.info("asr.mode=%s，跳过模型预热", mode)
            return
        for prefix in ("streaming", "offline"):
            _pre_register_status(prefix)
            try:
                get_model(prefix)
            except Exception:
                logger.exception("预热 %s 模型失败，将在首次使用时重试", prefix)
        if bool(get_config("spk.enabled")):
            try:
                from .speaker import preload_embedding_model

                preload_embedding_model()
            except Exception:
                logger.exception("预热声纹模型失败，将在首次使用时重试")

    threading.Thread(target=_worker, name="model-warmup", daemon=True).start()
    logger.info("已启动模型预热线程")
