"""用真实会议音频反推合适的说话人聚类阈值。

背景：meeting 179 里同一个人的话被拆成了「说话人 2 / 3 / 4」三个类，
说明 `spk.threshold`（默认 0.6）对真实录音偏严。

做法：按片段的 start_ms/end_ms 从会议录音里切出每句音频，
逐句抽 CAM++ 向量，算两两余弦相似度，看真实分布落在哪里，
据此选一个能合并同一人、又不至于把不同人混起来的阈值。
"""

from __future__ import annotations

import sqlite3
import sys

import numpy as np
import soundfile as sf

from imeeting.engine.speaker import cosine, extract_embedding

MID = int(sys.argv[1]) if len(sys.argv) > 1 else 179
wav = f"data/audio/{MID}.wav"

audio, sr = sf.read(wav, dtype="float32")
if audio.ndim > 1:
    audio = audio.mean(axis=1)
print(f"会议 {MID}: {len(audio)/sr:.1f}s @{sr}Hz")

db = sqlite3.connect("data/meeting.db")
db.row_factory = sqlite3.Row
rows = db.execute(
    "SELECT segment_id,text,speaker,start_ms,end_ms FROM segment "
    "WHERE meeting_id=? AND speaker IS NOT NULL ORDER BY seq",
    (MID,),
).fetchall()

embs: list[tuple[str, str, np.ndarray]] = []
for r in rows:
    a, b = int(r["start_ms"] * sr / 1000), int(r["end_ms"] * sr / 1000)
    seg = audio[max(0, a) : min(len(audio), b)]
    if len(seg) < sr * 0.5:
        continue
    e = extract_embedding(seg)
    if e is not None:
        embs.append((r["speaker"], r["text"][:14], e))

print(f"成功抽取 {len(embs)} / {len(rows)} 条片段的声纹\n")

labels = sorted({s for s, _, _ in embs})
print("当前标签:", labels)

print("\n=== 两两余弦相似度 ===")
for i in range(len(embs)):
    for j in range(i + 1, len(embs)):
        si, ti, ei = embs[i]
        sj, tj, ej = embs[j]
        c = cosine(ei, ej)
        same = "同类" if si == sj else "异类"
        print(f"  {c:.3f}  {same}  [{si}]{ti!r} vs [{sj}]{tj!r}")

if len(embs) > 1:
    intra, inter = [], []
    for i in range(len(embs)):
        for j in range(i + 1, len(embs)):
            c = cosine(embs[i][2], embs[j][2])
            (intra if embs[i][0] == embs[j][0] else inter).append(c)
    print("\n=== 统计 ===")
    if intra:
        print(f"  同类（当前判定为同一人）: n={len(intra)} "
              f"min={min(intra):.3f} 中位={np.median(intra):.3f} max={max(intra):.3f}")
    if inter:
        print(f"  异类（当前判定为不同人）: n={len(inter)} "
              f"min={min(inter):.3f} 中位={np.median(inter):.3f} max={max(inter):.3f}")
    allc = [cosine(embs[i][2], embs[j][2]) for i in range(len(embs)) for j in range(i + 1, len(embs))]
    print(f"  全部: min={min(allc):.3f} 中位={np.median(allc):.3f} max={max(allc):.3f}")
    print("\n  若把阈值设为 0.35 / 0.40 / 0.45 / 0.50 / 0.55 / 0.60，")
    print("  用「单链聚类」看会把这些人分成几类（越接近 1 越好）：")
    for th in (0.35, 0.40, 0.45, 0.50, 0.55, 0.60):
        parent = list(range(len(embs)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i in range(len(embs)):
            for j in range(i + 1, len(embs)):
                if cosine(embs[i][2], embs[j][2]) >= th:
                    parent[find(i)] = find(j)
        n = len({find(i) for i in range(len(embs))})
        print(f"    阈值 {th:.2f} → {n} 类")
