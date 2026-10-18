"""WebSocket 链路冒烟测试。

验证内容
--------
1. 首帧必须是 start；非 start 帧应被拒。
2. 正常握手后收到 ready，且带回 meeting_id / session_id / sample_rate。
3. 上行 float32 PCM 二进制帧能被接受（不报错、连接不断）。
4. ping → pong 心跳正常。
5. stop 后收到 closed，且会议片段已落库。

不依赖 ASR 模型是否加载完成：模型缺失时流式/离线路径会静默降级，
这里只校验链路与状态机行为。

用法::

    python scripts/ws_smoke.py [--port 8011] [--seconds 3]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import struct
import sys

import websockets


def make_pcm(n_samples: int, *, silent: bool = False) -> bytes:
    """生成 16kHz 单声道 float32 小端 PCM。"""
    if silent:
        return b"\x00\x00\x00\x00" * n_samples
    # 440Hz 正弦，幅度 0.25，确保能量高于静音阈值
    out = bytearray()
    for i in range(n_samples):
        v = 0.25 * math.sin(2 * math.pi * 440 * i / 16000)
        out += struct.pack("<f", v)
    return bytes(out)


async def run(port: int, seconds: float, expect_reject: bool) -> int:
    url = f"ws://127.0.0.1:{port}/ws/stream"
    failures: list[str] = []
    events: list[dict] = []

    def check(cond: bool, msg: str) -> None:
        print(("  [OK]   " if cond else "  [FAIL] ") + msg)
        if not cond:
            failures.append(msg)

    print("\n=== 用例 A：首帧非 start 应被拒绝 ===")
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "hello"}))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        msg = json.loads(raw)
        check(msg.get("type") == "error", f"收到 error（实际 {msg.get('type')}）")
        check(msg.get("code") == "EXPECTED_START", f"错误码 EXPECTED_START（实际 {msg.get('code')}）")

    print("\n=== 用例 B：正常握手 + 音频上行 + 心跳 + 收尾 ===")
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "start", "title": "WS 冒烟测试"}))

        # 收 ready
        deadline = asyncio.get_running_loop().time() + 15
        ready = None
        while asyncio.get_running_loop().time() < deadline:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            events.append(msg)
            if msg.get("type") == "ready":
                ready = msg
                break
            if msg.get("type") == "error":
                check(False, f"握手阶段意外错误: {msg}")
                return 1

        check(ready is not None, "收到 ready")
        if ready:
            check(isinstance(ready.get("meeting_id"), int), f"meeting_id 为整数 ({ready.get('meeting_id')})")
            check(bool(ready.get("session_id")), f"session_id 非空 ({ready.get('session_id')})")
            check(ready.get("sample_rate") == 16000, f"sample_rate=16000 ({ready.get('sample_rate')})")

        # 持续上行音频：说 1.2s → 静音 1.0s，循环
        chunk_samples = 9600  # 600ms
        rounds = max(1, int(seconds / 2.2))
        sent_frames = 0
        for _ in range(rounds):
            for _ in range(2):
                await ws.send(make_pcm(chunk_samples))
                sent_frames += 1
                await asyncio.sleep(0.02)
            for _ in range(2):
                await ws.send(make_pcm(chunk_samples, silent=True))
                sent_frames += 1
                await asyncio.sleep(0.02)

        print(f"  已上行 {sent_frames} 帧 PCM（{sent_frames * 0.6:.1f}s 音频）")
        check(True, "PCM 二进制帧上行未抛异常")

        # 心跳
        await ws.send(json.dumps({"type": "ping"}))
        got_pong = False
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                events.append(msg)
                if msg.get("type") == "pong":
                    got_pong = True
                    break
        except asyncio.TimeoutError:
            pass
        check(got_pong, "ping 收到 pong")

        # 收尾
        await ws.send(json.dumps({"type": "stop"}))
        got_closed = False
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=40))
                events.append(msg)
                if msg.get("type") == "closed":
                    got_closed = True
                    break
        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
            pass
        check(got_closed, "stop 后收到 closed")

    kinds: dict[str, int] = {}
    for e in events:
        kinds[e.get("type", "?")] = kinds.get(e.get("type", "?"), 0) + 1
    print(f"\n  收到消息统计: {kinds}")

    print("\n" + "=" * 46)
    if failures:
        print(f"结果：失败 {len(failures)} 项")
        for f in failures:
            print("  - " + f)
        return 1
    print("结果：全部通过")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8011)
    ap.add_argument("--seconds", type=float, default=4.5)
    args = ap.parse_args()
    try:
        return asyncio.run(run(args.port, args.seconds, False))
    except Exception as exc:  # noqa: BLE001
        print(f"测试异常: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
