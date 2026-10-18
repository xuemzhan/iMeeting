"""在服务内复用已加载离线模型，评估温和谱减降噪对真实片段的损伤。"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import soundfile as sf
from sqlmodel import select

from .db import session_scope
from .engine import pool
from .engine.audio_enhance import spectral_denoise
from .glossary import hotwords_text
from .models import Meeting, Segment
from .settings import get_config

logger = logging.getLogger(__name__)


def recommend_denoise(
    mean_raw: float, mean_clean: float, improved: int, worsened: int
) -> str:
    """把 A/B 数字翻译成可执行结论，避免只看“文本变了多少”。"""
    if mean_clean >= mean_raw + 0.02 and worsened <= improved:
        return "可在低 SNR 远场会议启用"
    if mean_clean >= mean_raw - 0.005 and worsened <= improved + 1:
        return "效果中性，仅在底噪明显时可选启用"
    return "不建议默认启用：文本与高频辅音有损伤"


def _text(result: object) -> str:
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return str(result[0].get("text") or "").strip()
    return ""


def _hf_ratio(audio: np.ndarray) -> float:
    if not len(audio):
        return 0.0
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio)))) ** 2
    freqs = np.fft.rfftfreq(len(audio), 1 / 16000)
    total = float(spectrum[(freqs >= 100) & (freqs <= 6000)].sum()) + 1e-9
    high = float(spectrum[(freqs >= 2000) & (freqs <= 6000)].sum())
    return high / total


def evaluate_denoise(
    meeting_id: int, *, limit: int = 8, strength: float = 0.3
) -> dict:
    with session_scope() as db:
        meeting = db.get(Meeting, meeting_id)
        if meeting is None or not meeting.audio_path:
            raise RuntimeError("会议没有留存音频")
        path = Path(meeting.audio_path)
        if not path.exists():
            raise RuntimeError("音频文件不存在")
        result_rows = db.exec(
            select(Segment)
            .where(
                Segment.meeting_id == meeting_id,
                Segment.speaker_score >= 0.65,
                Segment.end_ms - Segment.start_ms >= 3000,
                Segment.end_ms - Segment.start_ms <= 9000,
            )
            .order_by(Segment.start_ms)
        ).all()
        # session_scope 退出后 ORM 实例会过期/脱离 Session；这里先取出
        # 纯标量字段，避免后台线程里访问 row.text 触发 DetachedInstanceError。
        rows = [
            {
                "segment_id": row.segment_id,
                "start_ms": row.start_ms,
                "end_ms": row.end_ms,
                "text": row.text or "",
                "adaptive_gain": float(row.adaptive_gain or 1.0),
            }
            for row in result_rows
        ]
    rows = [row for row in rows if len(row["text"]) >= 20]
    if len(rows) > limit:
        step = max(1, len(rows) // limit)
        rows = rows[::step][:limit]
    if not rows:
        raise RuntimeError("没有合适的评估片段")

    model = pool.get_model("offline")
    records = []
    for row in rows:
        start = int(row["start_ms"] * 16000 / 1000)
        frames = max(1, int((row["end_ms"] - row["start_ms"]) * 16000 / 1000))
        audio, sr = sf.read(
            path,
            start=start,
            frames=frames,
            dtype="float32",
            always_2d=False,
        )
        if sr != 16000 and len(audio):
            n = int(len(audio) * 16000 / sr)
            audio = np.interp(
                np.linspace(0, len(audio) - 1, n),
                np.arange(len(audio)),
                audio,
            ).astype(np.float32)
        audio = np.asarray(audio, dtype=np.float32)
        # 生产链路会先乘会议自适应增益，再决定是否降噪。A/B 必须复用同一个
        # 增益，否则“原始音频”实际上比线上安静 4 倍，会把正常识别差异误判成
        # 降噪收益。
        gain = float(row["adaptive_gain"] or 1.0)
        base = (
            np.clip(audio * gain, -1.0, 1.0).astype(np.float32)
            if gain > 1.01
            else audio
        )
        cleaned = spectral_denoise(base, strength=strength).astype(np.float32)
        kwargs = {
            "batch_size_s": get_config("asr.batch_size_s"),
            "use_itn": get_config("use_itn"),
            "return_spk_res": False,
            "sentence_timestamp": True,
            "hotword": hotwords_text(get_config("hotwords")),
        }
        raw_text = _text(model.generate(input=base, **kwargs))
        clean_text = _text(model.generate(input=cleaned, **kwargs))
        similarity = SequenceMatcher(None, raw_text, clean_text).ratio()
        stored_text = row["text"]
        raw_vs_stored = SequenceMatcher(None, raw_text, stored_text).ratio()
        clean_vs_stored = SequenceMatcher(None, clean_text, stored_text).ratio()
        records.append(
            {
                "segment_id": row["segment_id"],
                "start_ms": row["start_ms"],
                "adaptive_gain": round(gain, 3),
                "stored_text": stored_text,
                "raw_text": raw_text,
                "denoised_text": clean_text,
                "similarity": round(similarity, 4),
                "raw_vs_stored": round(raw_vs_stored, 4),
                "denoised_vs_stored": round(clean_vs_stored, 4),
                "raw_chars": len(raw_text),
                "denoised_chars": len(clean_text),
                "hf_ratio_raw": round(_hf_ratio(base), 4),
                "hf_ratio_denoised": round(_hf_ratio(cleaned), 4),
            }
        )
    changed = sum(row["raw_text"] != row["denoised_text"] for row in records)
    improved = sum(
        row["denoised_vs_stored"] > row["raw_vs_stored"] + 0.02
        for row in records
    )
    worsened = sum(
        row["denoised_vs_stored"] < row["raw_vs_stored"] - 0.02
        for row in records
    )
    mean_raw = sum(row["raw_vs_stored"] for row in records) / len(records)
    mean_clean = sum(row["denoised_vs_stored"] for row in records) / len(records)
    hf_raw = sum(row["hf_ratio_raw"] for row in records) / len(records)
    hf_clean = sum(row["hf_ratio_denoised"] for row in records) / len(records)
    recommendation = recommend_denoise(mean_raw, mean_clean, improved, worsened)
    return {
        "meeting_id": meeting_id,
        "strength": strength,
        "segments": len(records),
        "changed": changed,
        "improved_vs_stored": improved,
        "worsened_vs_stored": worsened,
        "mean_similarity": round(
            sum(row["similarity"] for row in records) / len(records), 4
        ),
        "mean_raw_vs_stored": round(mean_raw, 4),
        "mean_denoised_vs_stored": round(mean_clean, 4),
        "mean_hf_ratio_raw": round(hf_raw, 4),
        "mean_hf_ratio_denoised": round(hf_clean, 4),
        "hf_drop_pct": round((1 - hf_clean / max(hf_raw, 1e-9)) * 100, 1),
        "recommendation": recommendation,
        "records": records,
    }
