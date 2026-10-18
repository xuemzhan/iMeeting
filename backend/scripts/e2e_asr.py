"""真实 ASR 端到端测试：把 wav 文件按 600ms 分块推给 WebSocket，
验证 partial/final 双路输出与识别文本。

与服务端约定一致：16kHz 单声道 float32 小端 PCM。

用法::

    python scripts/e2e_asr.py --wav ../runtime/funasr_api/asr_example.wav --port 8011
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import numpy as np
import soundfile as sf
import websockets

CHUNK_MS = 600
SAMPLE_RATE = 16000


async def run(wav: str, port: int) -> int:
    audio, sr = sf.read(wav, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    # 重采样到 16k（测试文件本来到就是 16k，这里做兜底）
    if sr != SAMPLE_RATE:
        idx = np.linspace(0, len(audio) - 1, int(len(audio) * SAMPLE_RATE / sr))
        audio = np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)

    duration = len(audio) / SAMPLE_RATE
    print(f"音频：{wav}")
    print(f"  采样率 {sr}Hz → {SAMPLE_RATE}Hz，时长 {duration:.1f}s，共 {len(audio)} 采样点")

    chunk_samples = int(SAMPLE_RATE * CHUNK_MS / 1000)
    url = f"ws://127.0.0.1:{port}/ws/stream"

    partials: dict[str, str] = {}
    finals: list[dict] = []

    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "start", "title": "真实 ASR 端到端测试"}))

        ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
        print(f"  ready: mode={ready.get('mode')} meeting_id={ready.get('meeting_id')}")
        meeting_id = ready.get("meeting_id")

        async def drain() -> None:
            """把当前已到达的消息收干净。"""
            while True:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.01))
                except (asyncio.TimeoutError, Exception):
                    return
                if msg.get("type") == "partial":
                    partials[msg["segment_id"]] = msg["text"]
                elif msg.get("type") == "final":
                    finals.append(msg)

        # 按真实时间节奏推送，模拟麦克风
        for start in range(0, len(audio), chunk_samples):
            block = audio[start : start + chunk_samples]
            await ws.send(block.astype("<f4").tobytes())
            await asyncio.sleep(0.02)
            await drain()

        # 尾部补静音，触发最后一句断句
        silence = np.zeros(SAMPLE_RATE * 2, dtype=np.float32)
        for start in range(0, len(silence), chunk_samples):
            await ws.send(silence[start : start + chunk_samples].astype("<f4").tobytes())
            await asyncio.sleep(0.02)
            await drain()

        await ws.send(json.dumps({"type": "stop"}))

        # 等收尾
        deadline = asyncio.get_running_loop().time() + 120
        while asyncio.get_running_loop().time() < deadline:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            except asyncio.TimeoutError:
                continue
            except Exception:
                break
            if msg.get("type") == "partial":
                partials[msg["segment_id"]] = msg["text"]
            elif msg.get("type") == "final":
                finals.append(msg)
            elif msg.get("type") == "closed":
                break

    print("\n--- 结果 ---")
    print(f"partial 事件 {len(partials)} 条（同一 segment 会反复覆盖，这里是最终态）")
    for sid, text in list(partials.items())[:5]:
        print(f"  {sid}: {text[:60]}")

    print(f"\nfinal 定稿 {len(finals)} 条：")
    for f in sorted(finals, key=lambda x: x.get("seq", 0)):
        spk = f.get("speaker") or "-"
        print(
            f"  seq={f.get('seq')} [{f.get('start_ms')}-{f.get('end_ms')}ms] "
            f"spk={spk} :: {f.get('text')}"
        )

    ok = len(finals) > 0 and any(f.get("text", "").strip() for f in finals)
    print("\n" + "=" * 50)
    if ok:
        joined = "".join(f.get("text", "") for f in finals)
        print(f"识别成功，共 {len(joined)} 字：{joined[:200]}")
        print(f"meeting_id = {meeting_id}")
        return 0
    print("未产出有效定稿文本")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--port", type=int, default=8011)
    args = ap.parse_args()
    try:
        return asyncio.run(run(args.wav, args.port))
    except Exception as exc:  # noqa: BLE001
        print(f"测试异常: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
