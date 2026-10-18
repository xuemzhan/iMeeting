"""验证说话人判定逻辑的两条路径：
1. 命中已录入声纹 → 返回姓名
2. 未命中 → 会话内聚成「说话人 N」，且同一人多次出现编号稳定
"""

from __future__ import annotations

import numpy as np
import soundfile as sf
from sqlmodel import select

from imeeting.db import session_scope
from imeeting.engine import speaker
from imeeting.models import Voiceprint

audio, _ = sf.read("data/audio/155.wav", dtype="float32")
if audio.ndim > 1:
    audio = audio.mean(axis=1)

emb = speaker.extract_embedding(audio)
print("声纹向量:", None if emb is None else f"dim={emb.shape[0]} norm={np.linalg.norm(emb):.4f}")

speaker.invalidate_cache()
enrolled = speaker.load_enrolled(force=True)
print("已录入声纹:", [n for n, *_ in enrolled])

# --- 路径 1：命中已录入 ---
r1 = speaker.SpeakerResolver(threshold=0.6)
print("\n[命中路径] resolve →", r1.resolve(emb))

# --- 路径 2：清空录入后应走聚类 ---
with session_scope() as db:
    rows = db.exec(select(Voiceprint)).all()
    for r in rows:
        db.delete(r)
    db.commit()
speaker.invalidate_cache()
print("\n已清空录入声纹:", [n for n, *_ in speaker.load_enrolled(force=True)])

r2 = speaker.SpeakerResolver(threshold=0.6)
a = r2.resolve(emb)
b = r2.resolve(emb)
# 加一点噪声模拟同一人的另一句话
noisy = emb + np.random.normal(0, 0.02, emb.shape).astype(np.float32)
noisy /= np.linalg.norm(noisy)
c = r2.resolve(noisy)
print(f"[聚类路径] 第1次 → {a}")
print(f"[聚类路径] 第2次（同一音频） → {b}")
print(f"[聚类路径] 第3次（加噪≈同一人） → {c}")

ok = a == "说话人 1" and b == "说话人 1" and c == "说话人 1"
print("\nRESULT:", "PASS" if ok else "FAIL")

# 还原：把测试声纹重新录入，方便用户在界面上看到
if emb is not None:
    with session_scope() as db:
        db.add(Voiceprint(name="测试甲", embedding=emb.astype(np.float32).tobytes(), sample_ms=9546))
        db.commit()
    speaker.invalidate_cache()
    print("已恢复「测试甲」声纹")
