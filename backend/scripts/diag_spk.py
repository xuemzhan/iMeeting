"""诊断：离线模型到底有没有返回说话人（spk）信息。

直接跑一次 FunASR generate(return_spk_res=True)，把返回结构打出来，
用于判断「声纹/说话人识别有问题」是模型没返回、还是我们解析错了。

用法：python scripts/diag_spk.py <wav>
"""

from __future__ import annotations

import json
import sys
import time

import soundfile as sf

from imeeting.engine import pool
from imeeting.settings import get_config

wav = sys.argv[1] if len(sys.argv) > 1 else "data/audio/155.wav"

print("配置：")
for k in ("spk.enabled", "spk.threshold", "vad.enabled", "punc.enabled"):
    print(f"  {k} = {get_config(k)}")

print("\n加载离线模型…")
t0 = time.time()
model = pool.get_model("offline")
print(f"  已加载，用时 {time.time() - t0:.1f}s")

audio, sr = sf.read(wav, dtype="float32")
if audio.ndim > 1:
    audio = audio.mean(axis=1)
print(f"\n音频：{wav}  {len(audio)/sr:.2f}s @{sr}Hz")

print("\n调用 generate(return_spk_res=True, sentence_timestamp=True) …")
t0 = time.time()
res = model.generate(
    input=audio,
    batch_size_s=get_config("asr.batch_size_s"),
    use_itn=get_config("use_itn"),
    return_spk_res=True,
    sentence_timestamp=True,
)
print(f"  用时 {time.time() - t0:.1f}s")

print("\n=== 返回结构 ===")
print("顶层类型:", type(res).__name__)
if isinstance(res, list):
    print("列表长度:", len(res))
    for i, item in enumerate(res):
        print(f"\n[{i}] keys = {sorted(item.keys())}")
        print(f"    text = {item.get('text')!r}")
        si = item.get("sentence_info")
        if si:
            print(f"    sentence_info 共 {len(si)} 条，前 3 条：")
            for s in si[:3]:
                print(f"      {json.dumps(s, ensure_ascii=False)}")
            spks = [s.get("spk") for s in si]
            print(f"    所有 spk = {spks}")
        else:
            print("    sentence_info = None/空  ← 说话人信息缺失！")
        # 有些版本把说话人放在别处
        for k in item:
            if "spk" in k.lower() or "speaker" in k.lower():
                print(f"    [{k}] = {item[k]}")
else:
    print(json.dumps(res, ensure_ascii=False, default=str)[:2000])
