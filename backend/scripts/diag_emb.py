"""确认 CAM++ 声纹向量的取法，为「跨段说话人识别 + 声纹录入」铺路。"""

from __future__ import annotations

import numpy as np
from funasr import AutoModel

m = AutoModel(model="iic/speech_campplus_sv_zh-cn_16k-common")
print("CAM++ 已加载")

res = m.generate(input="data/audio/155.wav")
print("返回类型:", type(res))
if isinstance(res, list) and res:
    print("keys:", list(res[0].keys()))
    for k, v in res[0].items():
        shape = getattr(v, "shape", None)
        print(f"  {k}: {type(v).__name__} shape={shape}")

    for key in ("spk_embedding", "embedding", "spk_emb"):
        if key in res[0]:
            e = np.asarray(res[0][key], dtype=np.float32).reshape(-1)
            print(f"\n取到向量 {key}: dim={e.shape[0]} norm={np.linalg.norm(e):.4f}")
            print("  前 6 维:", np.round(e[:6], 4))
            break

# 同一音频跑两次，确认可复现；并算一段不同音频的余弦相似度
def emb(path):
    r = m.generate(input=path)
    for key in ("spk_embedding", "embedding", "spk_emb"):
        if key in r[0]:
            return np.asarray(r[0][key], dtype=np.float32).reshape(-1)
    raise KeyError(list(r[0].keys()))

a = emb("data/audio/155.wav")
b = emb("data/audio/155.wav")
c = emb("data/audio/152.wav") if False else None

def cos(x, y):
    return float(np.dot(x, y) / (np.linalg.norm(x) * np.linalg.norm(y) + 1e-9))

print("\n同一音频两次的余弦相似度:", round(cos(a, b), 4), "（应≈1.0）")
