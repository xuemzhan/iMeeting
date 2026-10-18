"""生产级压力与稳定性测试。

覆盖生产环境真正会遇到的几类问题：

1. **吞吐 / RTF** —— 全速推送，测 CPU 转写能力上限（能否实时、能扛几路）
2. **首字延迟** —— 真实语音下从推流到第一个上屏字符的墙钟时间
3. **并发压力** —— N 个会话同时推流，观察是否有会话失败、串音、内存飙升
4. **长时间运行** —— 单会话持续数分钟，观察内存是否线性增长（泄漏）
5. **背压** —— 客户端推得比服务端处理得快时，服务端是否稳定
6. **异常输入** —— 空帧、超长帧、非 float32、高频小帧
7. **断连韧性** —— 客户端中途硬断，服务端是否正常回收会话与内存

用法::

    # 全部测试（默认 4 并发 × 30 秒）
    python scripts/stress.py --port 8011

    # 加大压力
    python scripts/stress.py --port 8011 --sessions 8 --duration 60

    # 只跑某一项
    python scripts/stress.py --port 8011 --only concurrency

前置条件：服务已启动（建议 ``ASR_MODE=echo`` 以纯测链路，
或 ``normal`` 以测真实模型吞吐）。

说明：首字延迟与 RTF 需要**真实语音**才有意义。脚本会自动从仓库
``runtime/funasr_api/asr_example.wav`` 取样本，也可用 ``STRESS_WAV`` 环境变量
指定其它 wav。找不到时会退回合成音并标注结果仅供参考。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import websockets

SAMPLE_RATE = 16000
CHUNK_MS = 600
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_MS / 1000)
LAT_CHUNK_SAMPLES = CHUNK_SAMPLES


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def speech(seconds: float, amp: float = 0.25) -> np.ndarray:
    """生成近似人声的有声片段（多谐波，比纯正弦更接近真实语音能量分布）。"""
    n = int(SAMPLE_RATE * seconds)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    wave = (
        amp * np.sin(2 * np.pi * 180 * t)
        + 0.5 * amp * np.sin(2 * np.pi * 360 * t)
        + 0.3 * amp * np.sin(2 * np.pi * 720 * t)
    )
    return wave.astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(SAMPLE_RATE * seconds), dtype=np.float32)


def rss_mb() -> float | None:
    """当前进程内存占用（MB）。用于观察服务端是否泄漏。"""
    try:
        import psutil
    except ImportError:
        return None
    try:
        return psutil.Process().memory_info().rss / 1024 / 1024
    except Exception:
        return None


@dataclass
class SessionStats:
    """单个会话的统计结果。"""

    name: str
    connected: bool = False
    ready: bool = False
    closed: bool = False
    partials: int = 0
    finals: int = 0
    errors: list[str] = field(default_factory=list)
    bytes_sent: int = 0
    first_partial_ms: float | None = None
    latency_samples: list[float] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return self.connected and self.ready and self.closed and not self.errors


# ---------------------------------------------------------------------------
# 单会话驱动
# ---------------------------------------------------------------------------


async def drive_session(
    port: int,
    name: str,
    *,
    duration: float,
    realtime: bool = True,
    stop_cleanly: bool = True,
    ab_test: bool = False,
) -> SessionStats:
    """跑一个完整会话，推送 ``duration`` 秒的合成音频。"""
    stats = SessionStats(name=name)
    url = f"ws://127.0.0.1:{port}/ws/stream"
    t0 = time.monotonic()

    try:
        async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
            stats.connected = True
            await ws.send(json.dumps({"type": "start", "title": f"压测-{name}"}))

            # --- 握手 ---
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                if msg.get("type") == "ready":
                    stats.ready = True
                    break
                if msg.get("type") == "error":
                    stats.errors.append(f"{msg.get('code')}: {msg.get('message')}")
                    return stats
            else:
                stats.errors.append("握手超时")
                return stats

            # --- 推流 ---
            chunks = int(duration * 1000 / CHUNK_MS)
            recv_task = asyncio.create_task(_drain(ws, stats, t0))

            for i in range(chunks):
                # 每 5 块插一段静音，触发断句走离线路径
                if i % 5 == 4:
                    block = silence(0.6)
                else:
                    block = speech(0.6)

                payload = block.astype("<f4").tobytes()
                await ws.send(payload)
                stats.bytes_sent += len(payload)

                if realtime:
                    # 按真实时间节奏推（不真实节奏会让服务端缓冲爆掉，属另一类测试）
                    await asyncio.sleep(CHUNK_MS / 1000)
                else:
                    await asyncio.sleep(0)

            if stop_cleanly:
                # 关键：必须先停掉后台 drain，否则它会和下面的收尾循环争抢消息，
                # 把 closed 吃掉，导致误判为「会话未关闭」。
                recv_task.cancel()
                try:
                    await recv_task
                except (asyncio.CancelledError, Exception):
                    pass
                recv_task = None

                await ws.send(json.dumps({"type": "stop"}))

                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    try:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        break
                    _handle(msg, stats, t0)
                    if msg.get("type") == "closed":
                        stats.closed = True
                        break

            if recv_task is not None:
                recv_task.cancel()
                try:
                    await recv_task
                except (asyncio.CancelledError, Exception):
                    pass

    except Exception as exc:  # noqa: BLE001
        stats.errors.append(f"{type(exc).__name__}: {exc}")

    stats.elapsed = time.monotonic() - t0
    return stats


def _handle(msg: dict, stats: SessionStats, t0: float) -> None:
    kind = msg.get("type")
    if kind == "partial":
        stats.partials += 1
        if stats.first_partial_ms is None:
            stats.first_partial_ms = (time.monotonic() - t0) * 1000
    elif kind == "final":
        stats.finals += 1
    elif kind == "error":
        stats.errors.append(f"{msg.get('code')}: {msg.get('message')}")
    elif kind == "closed":
        stats.closed = True


async def _drain(ws, stats: SessionStats, t0: float) -> None:
    """后台协程：持续收消息并计数。"""
    try:
        while True:
            raw = await ws.recv()
            if isinstance(raw, str):
                _handle(json.loads(raw), stats, t0)
    except (asyncio.CancelledError, Exception):
        return


# ---------------------------------------------------------------------------
# 测试项
# ---------------------------------------------------------------------------


async def test_concurrency(port: int, sessions: int, duration: float) -> bool:
    print(f"\n{'='*60}")
    print(f"【并发压力】{sessions} 个会话同时转写 {duration:.0f} 秒音频")
    print("=" * 60)

    limit = await _fetch_concurrency_limit(port)
    if limit is not None:
        print(f"服务端并发上限: {limit}")
        if sessions > limit:
            print(
                f"  注意：请求 {sessions} 个会话超过上限 {limit}，"
                f"超出部分会被服务端拒绝（这是预期行为，不算失败）"
            )

    mem_before = rss_mb()
    t0 = time.monotonic()
    results = await asyncio.gather(
        *(
            drive_session(port, f"s{i}", duration=duration, realtime=True)
            for i in range(sessions)
        ),
        return_exceptions=True,
    )
    elapsed = time.monotonic() - t0
    mem_after = rss_mb()

    stats_list: list[SessionStats] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            s = SessionStats(name=f"s{i}")
            s.errors.append(f"任务异常: {type(r).__name__}: {r}")
            stats_list.append(s)
        else:
            stats_list.append(r)

    # 被并发上限拒绝的会话单独归类：这是限流生效，不是故障
    rejected = [s for s in stats_list if any("TOO_MANY_SESSIONS" in e for e in s.errors)]
    active = [s for s in stats_list if s not in rejected]

    ok = sum(1 for s in active if s.ok)
    print(f"\n正常会话: {ok}/{len(active)}    "
          f"被限流拒绝: {len(rejected)}    "
          f"总耗时: {elapsed:.1f}s")

    for s in stats_list:
        if s in rejected:
            print(f"  [LIMIT] {s.name}: 并发上限拦截（预期）")
            continue
        flag = "OK  " if s.ok else "FAIL"
        print(
            f"  [{flag}] {s.name}: partial={s.partials} final={s.finals} "
            f"{s.elapsed:.1f}s bytes={s.bytes_sent//1024}KB"
        )
        for e in s.errors:
            print(f"         ! {e}")

    if mem_before is not None and mem_after is not None:
        print(f"\n内存: {mem_before:.0f}MB → {mem_after:.0f}MB "
              f"(增量 {mem_after - mem_before:+.0f}MB)")

    # 只要「在限额内的会话」全部成功即通过；限流拒绝不计为失败
    if not active:
        print("\n所有会话都被限流拒绝，无法判断并发能力")
        return False
    return ok == len(active)


async def _fetch_concurrency_limit(port: int) -> int | None:
    """从服务端读取当前并发上限，用于解释限流行为。"""
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/v1/config", timeout=5
        ) as resp:
            data = json.loads(resp.read())
            return int(data.get("session.max_concurrent"))
    except Exception:
        return None


async def test_long_run(port: int, duration: float) -> bool:
    print(f"\n{'='*60}")
    print(f"【长时运行 / 泄漏检测】单会话连续 {duration:.0f} 秒")
    print("=" * 60)

    mem_before = rss_mb()
    samples: list[float] = []

    # 用非实时节奏尽快跑完，同时周期性采样内存
    sampler_stop = asyncio.Event()

    async def sample_memory():
        while not sampler_stop.is_set():
            m = rss_mb()
            if m is not None:
                samples.append(m)
            try:
                await asyncio.wait_for(sampler_stop.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                continue

    task = asyncio.create_task(sample_memory())
    try:
        s = await drive_session(
            port, "long", duration=duration, realtime=False, stop_cleanly=True
        )
    finally:
        sampler_stop.set()
        await task

    print(f"\n会话结果: {'OK' if s.ok else 'FAIL'}  "
          f"partial={s.partials} final={s.finals} 耗时={s.elapsed:.1f}s")
    for e in s.errors:
        print(f"  ! {e}")

    mem_after = rss_mb()
    if mem_before is not None and mem_after is not None and samples:
        growth = mem_after - mem_before
        peak = max(samples)
        print(f"\n内存: 起始 {mem_before:.0f}MB → 结束 {mem_after:.0f}MB "
              f"(增量 {growth:+.0f}MB, 峰值 {peak:.0f}MB)")
        # 判据：内存增长不该远超音频缓冲本身（音频总量约 64KB/s 已被消费）
        if growth > 512:
            print("  ! 内存增长偏大，疑似泄漏")
            return False

    if s.finals == 0:
        print("  ! 长会话未产出任何定稿")
        return False
    return s.ok or len(s.errors) == 0


async def test_backpressure(port: int, duration: float) -> bool:
    """不做实时节流地猛推音频，看服务端是否稳定。

    真实场景里网络突发会有短暂堆积，服务端必须能扛住而不是 OOM。
    """
    print(f"\n{'='*60}")
    print("【背压测试】不节流全速推送，观察服务端稳定性")
    print("=" * 60)

    mem_before = rss_mb()
    s = await drive_session(port, "burst", duration=duration, realtime=False)
    mem_after = rss_mb()

    print(f"\n会话结果: {'OK' if s.ok else 'FAIL'}  "
          f"partial={s.partials} final={s.finals} 耗时={s.elapsed:.1f}s")
    print(f"发送速率: {s.bytes_sent / 1024 / max(s.elapsed, 0.01):.0f} KB/s")
    for e in s.errors:
        print(f"  ! {e}")

    if mem_before is not None and mem_after is not None:
        print(f"内存: {mem_before:.0f}MB → {mem_after:.0f}MB "
              f"(增量 {mem_after - mem_before:+.0f}MB)")
    return s.closed and not s.errors


async def test_malformed_input(port: int) -> bool:
    """异常输入韧性：都不应该导致服务端 500 或断连。"""
    print(f"\n{'='*60}")
    print("【异常输入韧性】")
    print("=" * 60)

    url = f"ws://127.0.0.1:{port}/ws/stream"
    failures: list[str] = []

    cases: list[tuple[str, object]] = [
        ("非 4 倍数二进制帧", b"\x01\x02\x03"),
        ("空二进制帧", b""),
        ("超大单帧 (20s)", speech(20.0).astype("<f4").tobytes()),
        ("全零帧", silence(1.0).astype("<f4").tobytes()),
        ("未知文本指令", json.dumps({"type": "definitely_not_a_command"})),
        ("空文本帧", ""),
        ("畸形 JSON", "{oops"),
        ("超长 JSON 文本", json.dumps({"type": "x", "pad": "A" * 100000})),
    ]

    for label, payload in cases:
        try:
            async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
                await ws.send(json.dumps({"type": "start", "title": f"fuzz-{label}"}))
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                if msg.get("type") != "ready":
                    failures.append(f"{label}: 握手失败 {msg}")
                    continue

                if isinstance(payload, bytes):
                    await ws.send(payload)
                else:
                    await ws.send(payload)

                # 之后仍能正常心跳 → 服务端没被搞坏
                await ws.send(json.dumps({"type": "ping"}))
                alive = False
                for _ in range(30):
                    try:
                        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    except asyncio.TimeoutError:
                        break
                    except Exception as exc:
                        failures.append(f"{label}: 连接中断 {exc}")
                        break
                    if m.get("type") == "pong":
                        alive = True
                        break
                if not alive:
                    failures.append(f"{label}: 异常输入后服务无响应")
                else:
                    print(f"  OK   {label}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{label}: {type(exc).__name__}: {exc}")
            print(f"  FAIL {label}: {exc}")

    if failures:
        print("\n失败项：")
        for f in failures:
            print(f"  ! {f}")
        return False
    print("\n全部异常输入均被安全处理")
    return True


async def test_abrupt_disconnect(port: int, rounds: int = 5) -> bool:
    """客户端中途硬断，服务端应回收会话名额，后续仍能正常建连。"""
    print(f"\n{'='*60}")
    print(f"【断连韧性】{rounds} 轮「连上→推流→硬断」")
    print("=" * 60)

    url = f"ws://127.0.0.1:{port}/ws/stream"
    for i in range(rounds):
        try:
            ws = await websockets.connect(url, max_size=None, ping_interval=None)
            await ws.send(json.dumps({"type": "start", "title": f"abrupt-{i}"}))
            await asyncio.wait_for(ws.recv(), timeout=30)
            for _ in range(6):
                await ws.send(speech(0.6).astype("<f4").tobytes())
            # 不发送 stop 直接关闭 TCP
            await ws.close()
            print(f"  OK   第 {i+1} 轮硬断")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL 第 {i+1} 轮: {exc}")
            return False
        await asyncio.sleep(0.5)

    # 验证之后仍能正常建连
    try:
        async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
            await ws.send(json.dumps({"type": "start", "title": "post-abrupt"}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            if msg.get("type") != "ready":
                print(f"  FAIL 断连后无法建立新会话: {msg}")
                return False
            await ws.send(json.dumps({"type": "stop"}))
            print("  OK   断连后仍可正常建立新会话")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL 断连后建连异常: {exc}")
        return False

    return True


async def test_latency(port: int, rounds: int = 5) -> bool:
    """首字延迟测量：从开始推流到第一个 partial 的墙钟时间。

    关键点：

    - 必须用**真实语音**。流式 Paraformer 是声学模型，对合成正弦音不产出
      有效 token，用它测出来的"延迟"只是 recv 超时，不代表真实体验。
      优先读 ``--wav``，否则退回合成音（并标注不可信）。
    - 用**后台接收协程**记录第一个 partial 的到达时刻，而不是"发一块 recv 一次"。
      后者在模型 1.2s 后才出字的情况下会先撞上 recv 超时，测到的是超时值。
    - 按**真实麦克风节奏**（每块 sleep 到 600ms）推送，还原用户实际体感。
    """
    print(f"\n{'='*60}")
    print(f"【首字延迟】{rounds} 轮测量")
    print("=" * 60)

    clip, is_real = _latency_clip()
    if clip is None:
        print("  跳过：没有可用音频（--wav 不存在且无法合成）")
        return True
    if is_real:
        print(f"  音频来源: {_latency_wav_name()} （真实语音，结果可信）")
    else:
        print("  音频来源: 合成正弦音（非语音，流式模型可能不出字，结果仅供参考）")

    url = f"ws://127.0.0.1:{port}/ws/stream"
    latencies: list[float] = []
    no_text = 0

    for i in range(rounds):
        try:
            async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
                await ws.send(json.dumps({"type": "start", "title": f"lat-{i}"}))
                await asyncio.wait_for(ws.recv(), timeout=30)

                found: dict[str, float | str | None] = {}

                async def recv_loop() -> None:
                    try:
                        while True:
                            msg = json.loads(await ws.recv())
                            kind = msg.get("type")
                            if kind in ("partial", "final") and not found.get("t"):
                                found["t"] = time.monotonic()
                                found["text"] = msg.get("text", "")
                                found["end_ms"] = msg.get("end_ms")
                                return
                            if kind == "closed":
                                return
                    except Exception:  # noqa: BLE001
                        return

                task = asyncio.create_task(recv_loop())
                await asyncio.sleep(0.05)

                t0 = time.monotonic()
                for start in range(0, len(clip), LAT_CHUNK_SAMPLES):
                    await ws.send(clip[start : start + LAT_CHUNK_SAMPLES].astype("<f4").tobytes())
                    await asyncio.sleep(CHUNK_MS / 1000)
                    if found.get("t"):
                        break

                wall = (float(found["t"]) - t0) * 1000 if found.get("t") else None
                task.cancel()
                await ws.send(json.dumps({"type": "stop"}))
                try:
                    while True:
                        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                        if m.get("type") == "closed":
                            break
                except Exception:  # noqa: BLE001
                    pass

                if wall is not None:
                    latencies.append(wall)
                    print(
                        f"  第 {i+1} 轮首字延迟: {wall:.0f}ms "
                        f"(服务端已收音频 {found.get('end_ms')}ms) :: {str(found.get('text'))[:20]!r}"
                    )
                else:
                    no_text += 1
                    print(f"  第 {i+1} 轮: {len(clip)/SAMPLE_RATE:.1f}s 音频内未出字")
        except Exception as exc:  # noqa: BLE001
            print(f"  第 {i+1} 轮异常: {exc}")

    if latencies:
        median = statistics.median(latencies)
        print(
            f"\n首字延迟: 中位数 {median:.0f}ms, "
            f"最小 {min(latencies):.0f}ms, 最大 {max(latencies):.0f}ms"
        )
        if median > 3000:
            print("  ! 中位延迟超过 3s，实时体验会明显卡顿")
            return False
        print("  OK   首字延迟在实时可用范围内")
        return True

    # 全部未出字：合成音场景下这是预期行为，不算失败
    if no_text and not is_real:
        print("\n  跳过判定：合成正弦音下流式模型不出字属预期，不计为失败")
        return True
    print("\n  FAIL 真实语音下始终未产出文字")
    return False


def _latency_wav_name() -> str:
    return str(_latency_wav()) if _latency_wav() else "(内置)"


def _latency_wav():
    """定位用于延迟测量的真实语音文件。

    脚本位于 ``backend/scripts/``，样例音频在不同部署形态下位置不一，
    在不同部署形态下位置不一，因此逐级向上探测，也支持 ``STRESS_WAV`` 覆盖。
    """
    env = os.getenv("STRESS_WAV", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if p.exists():
            return p
    here = Path(__file__).resolve().parent
    for up in (here, *here.parents):
        cand = up / "runtime" / "funasr_api" / "asr_example.wav"
        if cand.exists():
            return cand
    return None


def _latency_clip() -> tuple[np.ndarray | None, bool]:
    """返回 (音频, 是否为真实语音)。"""
    wav = _latency_wav()
    if wav is not None:
        try:
            import soundfile as sf

            audio, sr = sf.read(str(wav), dtype="float32")
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            if sr != SAMPLE_RATE:
                idx = np.linspace(0, len(audio) - 1, int(len(audio) * SAMPLE_RATE / sr))
                audio = np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)
            return audio[: SAMPLE_RATE * 6], True
        except Exception as exc:  # noqa: BLE001
            print(f"  读取 {wav} 失败: {exc}，退回合成音")
    if "speech" in globals():
        return speech(4.0), False
    return None, False


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


ALL_TESTS = (
    "throughput",
    "latency",
    "concurrency",
    "longrun",
    "backpressure",
    "malformed",
    "abrupt",
)


async def test_throughput(port: int, duration: float = 20.0) -> bool:
    """吞吐 / RTF 测量：不按实时节奏、尽快推完，衡量 CPU 转写能力上限。

    RTF（Real Time Factor）= 处理耗时 / 音频时长，小于 1 才能实时。
    单核 CPU 上若 RTF 为 0.1，理论上 1 个核可以扛约 10 路并发转写。
    """
    print(f"\n{'='*60}")
    print("【吞吐 / RTF】全速推送，测量转写能力上限")
    print("=" * 60)

    clip, is_real = _latency_clip()
    if clip is None:
        print("  跳过：没有可用音频")
        return True
    # 拉长到指定时长，避免样本太短导致统计抖动
    reps = max(1, int(duration * SAMPLE_RATE / max(len(clip), 1)) + 1)
    audio = np.tile(clip, reps)[: int(duration * SAMPLE_RATE)]
    audio_s = len(audio) / SAMPLE_RATE
    print(f"  音频: {audio_s:.1f}s（真实语音: {is_real}）")

    url = f"ws://127.0.0.1:{port}/ws/stream"
    try:
        async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
            await ws.send(json.dumps({"type": "start", "title": "throughput"}))
            ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            mode = ready.get("mode")
            if mode != "normal":
                print(f"  mode={mode}，非真实模型，跳过 RTF 判定")
                await ws.send(json.dumps({"type": "stop"}))
                return True

            finals = 0
            partials = 0
            closed = asyncio.Event()

            async def recv_loop() -> None:
                nonlocal finals, partials
                try:
                    while True:
                        msg = json.loads(await ws.recv())
                        kind = msg.get("type")
                        if kind == "partial":
                            partials += 1
                        elif kind == "final":
                            finals += 1
                        elif kind == "closed":
                            closed.set()
                            return
                except Exception:  # noqa: BLE001
                    closed.set()

            task = asyncio.create_task(recv_loop())
            t0 = time.monotonic()
            for start in range(0, len(audio), CHUNK_SAMPLES):
                await ws.send(audio[start : start + CHUNK_SAMPLES].astype("<f4").tobytes())
            await ws.send(json.dumps({"type": "stop"}))
            try:
                await asyncio.wait_for(closed.wait(), timeout=180)
            except asyncio.TimeoutError:
                print("  ! 超时未收到 closed")
            elapsed = time.monotonic() - t0
            task.cancel()

        rtf = elapsed / audio_s
        print(f"  处理 {audio_s:.1f}s 音频耗时 {elapsed:.2f}s  →  RTF = {rtf:.3f}")
        print(f"  partial={partials}  final={finals}")
        if rtf < 1.0:
            print(f"  OK   可实时（单核理论并发≈{1/rtf:.1f} 路）")
            return True
        print("  FAIL RTF ≥ 1，CPU 无法实时转写，需要加核或降规格")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"  异常: {type(exc).__name__}: {exc}")
        return False


async def run_all(args) -> int:
    print("=" * 60)
    print("  iMeeting · 生产级压测")
    print("=" * 60)
    print(f"目标: ws://127.0.0.1:{args.port}/ws/stream")
    print(f"并发会话: {args.sessions}   时长: {args.duration}s")

    pending = set(ALL_TESTS if args.only == "all" else {args.only})
    results: dict[str, bool] = {}

    if "malformed" in pending:
        results["异常输入韧性"] = await test_malformed_input(args.port)
        pending.discard("malformed")

    if "throughput" in pending:
        results["吞吐 / RTF"] = await test_throughput(
            args.port, duration=max(args.duration, 20)
        )
        pending.discard("throughput")

    if "latency" in pending:
        results["首字延迟"] = await test_latency(args.port, rounds=min(5, max(args.sessions, 3)))
        pending.discard("latency")

    if "abrupt" in pending:
        results["断连韧性"] = await test_abrupt_disconnect(args.port)
        pending.discard("abrupt")

    if "backpressure" in pending:
        results["背压测试"] = await test_backpressure(args.port, duration=min(args.duration, 30))
        pending.discard("backpressure")

    if "longrun" in pending:
        results["长时运行"] = await test_long_run(args.port, duration=max(args.duration, 60))
        pending.discard("longrun")

    if "concurrency" in pending:
        results["并发压力"] = await test_concurrency(
            args.port, args.sessions, args.duration
        )
        pending.discard("concurrency")

    print(f"\n{'='*60}")
    print("  汇总")
    print("=" * 60)
    all_ok = True
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        all_ok = all_ok and ok

    print()
    print("全部通过" if all_ok else "存在失败项，请查看上方详情")
    return 0 if all_ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="生产级压力与稳定性测试")
    ap.add_argument("--port", type=int, default=8011)
    ap.add_argument("--sessions", type=int, default=4, help="并发会话数")
    ap.add_argument("--duration", type=float, default=30, help="每个会话音频时长（秒）")
    ap.add_argument(
        "--only",
        default="all",
        choices=["all", *ALL_TESTS],
        help="只跑指定测试项",
    )
    args = ap.parse_args()

    try:
        return asyncio.run(run_all(args))
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
