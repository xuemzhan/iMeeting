"""同机对照测量：模板重建取样缓存与模板匹配路径（2026-10-31）。

如实说明测量边界（写报告时不得超出）：

- 重建：真实 SQLite、真实 WAV 解码（``speaker_reconcile._read_audio``），声纹模型
  用确定性替身（本机没有 torch，加载不了 CAM++）。因此这里能量化的是
  **解码/调用次数** 与不含模型推理的耗时；CAM++ 推理耗时 = 取样条数 × 单条模型
  耗时，本次没有测到，不做外推。
- 匹配：走真实代码路径（``voice_template.templates_matrix`` + ``voice_match.match``），
  纯 numpy 矩阵运算，报告 p50/p95/p99；不含数据库以外的网络/模型开销。
- 不含：目标硬件、真实语料、并发压力、Docker/TLS、长稳。

用法（在 iMeeting 目录执行）::

    .venv-production-review\\Scripts\\python.exe benchmarks\\template_rebuild_20261031\\bench_template_optimization.py \
        --out benchmarks/template_rebuild_20261031/results.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "src"))


def _prepare_environment(workdir: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True, mode=0o777)
    if workdir.exists() and not workdir.is_dir():
        raise SystemExit(f"workdir 不是目录：{workdir}")
    os.environ["DATABASE_URL"] = f"sqlite:///{(workdir / 'bench.db').as_posix()}"
    os.environ["SECRET_KEY"] = "bench-secret-0123456789abcdef0123456789abcdef"
    os.environ["ASR_MODE"] = "off"
    os.environ["DATA_DIR"] = str(workdir)
    os.environ["LOG_FILE"] = str(workdir / "bench.log")
    os.environ["MEETING_SKIP_ALEMBIC"] = "1"
    os.environ["BCRYPT_ROUNDS"] = "4"
    os.environ["SKIP_MODEL_WARMUP"] = "1"

def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    if not ordered:
        return {}
    import numpy as np

    return {
        "count": len(ordered),
        "mean_ms": round(statistics.fmean(ordered) * 1000, 3),
        "p50_ms": round(float(np.percentile(ordered, 50)) * 1000, 3),
        "p95_ms": round(float(np.percentile(ordered, 95)) * 1000, 3),
        "p99_ms": round(float(np.percentile(ordered, 99)) * 1000, 3),
        "max_ms": round(ordered[-1] * 1000, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(Path(__file__).with_name("results.json")))
    parser.add_argument("--workdir", default=str(Path(__file__).with_name("_run")))
    parser.add_argument("--speakers", type=int, default=3)
    parser.add_argument("--segments-per-speaker", type=int, default=24)
    parser.add_argument("--templates", type=int, default=2000)
    parser.add_argument("--queries", type=int, default=200)
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    # 每次测量用独立目录：不删除历史结果，便于复查当时的数据库与音频。
    workdir = workdir / time.strftime("run_%Y%m%d_%H%M%S")
    _prepare_environment(workdir)

    import numpy as np
    import soundfile as sf
    from sqlmodel import Session, select

    from imeeting.application.bootstrap import initialize_app_database
    from imeeting.db import engine
    from imeeting.engine import speaker, speaker_reconcile, voice_match, voice_template
    from imeeting.engine.voice_template import Scenario
    from imeeting.models import (
        Meeting,
        Segment,
        TemplateSampleEmbedding,
        User,
        Voiceprint,
        VoiceprintTemplate,
    )
    from imeeting.security import hash_password
    from imeeting.settings import config_store

    initialize_app_database()

    results: dict = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "numpy": np.__version__,
            "asr_mode": os.environ.get("ASR_MODE"),
            "torch_available": False,
            "embedding_model": "deterministic stand-in (CAM++ unavailable locally)",
        },
    }

    owner_id = None
    with Session(engine) as db:
        admin = User(
            username="bench_admin",
            display_name="Bench",
            role="admin",
            password_hash=hash_password("bench-password"),
            must_change_password=False,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        owner_id = admin.id
    assert owner_id is not None

    # 真实 WAV：16kHz 单声道，长度覆盖全部片段边界，幅值随时间变化使切片各不相同。
    sample_rate = 16000
    seconds = args.speakers * args.segments_per_speaker * 4 + 8
    timeline = np.arange(sample_rate * seconds, dtype=np.float32) / sample_rate
    rng = np.random.default_rng(20261031)
    signal = (
        0.12 * np.sin(2 * np.pi * 220 * timeline)
        + 0.02 * rng.standard_normal(timeline.size).astype(np.float32)
    ).astype(np.float32)
    wav_path = workdir / "meeting.wav"
    sf.write(wav_path, signal, sample_rate, subtype="PCM_16")

    decode_calls = {"n": 0}
    real_read_audio = speaker_reconcile._read_audio

    def counting_read_audio(path, start_ms, end_ms, gain):
        decode_calls["n"] += 1
        return real_read_audio(path, start_ms, end_ms, gain)

    speaker_reconcile._read_audio = counting_read_audio

    def fake_embedding(pcm: np.ndarray) -> np.ndarray:
        # 确定性替身：同一段 PCM 必然得到同一向量，便于观察缓存命中；不是模型输出。
        digest = hashlib.sha256(np.ascontiguousarray(pcm).tobytes()).digest()
        seed = int.from_bytes(digest[:8], "big") % (2**32)
        vec = np.random.default_rng(seed).standard_normal(voice_template.EMBED_DIM)
        vec = vec.astype(np.float32)
        return vec / float(np.linalg.norm(vec))

    speaker.extract_embedding = fake_embedding

    segment_ms = 3000
    with Session(engine) as db:
        meeting = Meeting(
            title="模板重建基准",
            owner_id=owner_id,
            audio_path=str(wav_path),
            duration_ms=args.speakers * args.segments_per_speaker * 4000 + 8000,
            status="finished",
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        meeting_id = meeting.id
        cursor = 8000
        for index in range(args.speakers):
            key = f"spk_bench_{index}"
            db.add(
                Voiceprint(
                    owner_id=owner_id,
                    name=f"基准说话人{index}",
                    embedding=fake_embedding(np.arange(16, dtype=np.float32) + index).tobytes(),
                    speaker_key=key,
                    sample_ms=segment_ms * args.segments_per_speaker,
                    sample_count=args.segments_per_speaker,
                    provisional=False,
                )
            )
            for slot in range(args.segments_per_speaker):
                db.add(
                    Segment(
                        meeting_id=meeting_id,
                        segment_id=f"{key}-{slot}",
                        text="这是一句用于模板取样基准的发言内容。",
                        is_final=True,
                        start_ms=cursor,
                        end_ms=cursor + segment_ms,
                        speech_ms=segment_ms,
                        snr_db=8.0,
                        audio_rms=0.03,
                        speaker_key=key,
                    )
                )
                cursor += 4000
        db.commit()

    samples_per_run = args.speakers * args.segments_per_speaker

    def timed_rebuild(label: str) -> dict:
        decode_calls["n"] = 0
        voice_match.invalidate_cache()
        started = time.perf_counter()
        stats = voice_template.rebuild_meeting_templates(meeting_id)
        wall = time.perf_counter() - started
        return {
            "label": label,
            "wall_seconds": round(wall, 3),
            "decode_calls": decode_calls["n"],
            "cache_hits": stats["cache_hits"],
            "cache_misses": stats["cache_misses"],
            "templates_created": stats["created"],
            "templates_updated": stats["updated"],
        }

    runs = []
    config_store.set_overrides({"spk.template_sample_max": samples_per_run})
    config_store.set_overrides({"spk.template_embedding_cache_enabled": False})
    runs.append(timed_rebuild("cache_disabled_first"))
    # 第二次禁用缓存的重建与下面「缓存预热后」的一次对齐：两者都只更新已有模板，
    # 差别只在是否重新解码音频，可比性最强。
    runs.append(timed_rebuild("cache_disabled_repeat"))

    config_store.set_overrides({"spk.template_embedding_cache_enabled": True})
    runs.append(timed_rebuild("cache_enabled_cold"))
    runs.append(timed_rebuild("cache_enabled_warm"))

    with Session(engine) as db:
        cache_rows = len(db.exec(select(TemplateSampleEmbedding)).all())
        template_rows = len(db.exec(select(VoiceprintTemplate)).all())
        db_size = sum(
            path.stat().st_size
            for path in [
                workdir / "bench.db",
                workdir / "bench.db-wal",
                workdir / "bench.db-shm",
            ]
            if path.exists()
        )
    results["rebuild"] = {
        "speakers": args.speakers,
        "segments_per_speaker": args.segments_per_speaker,
        "samples_per_run": samples_per_run,
        "runs": runs,
        "cache_rows_after": cache_rows,
        "template_rows_after": template_rows,
        "db_bytes": db_size,
    }

    # ---- 匹配路径：全矩阵 vs 场景粗筛 ----
    extra = max(0, args.templates - template_rows)
    with Session(engine) as db:
        rng2 = np.random.default_rng(7)
        # 场景桶轮转，保证粗筛能真正筛掉大部分行；否则测到的仍是全矩阵路径。
        buckets = [
            ("local_mic", distance, noise, voice)
            for distance in ("near", "mid", "far")
            for noise in ("quiet", "reverb")
            for voice in ("normal", "fast")
        ]
        for index in range(extra):
            vec = rng2.standard_normal(voice_template.EMBED_DIM).astype(np.float32)
            vec /= float(np.linalg.norm(vec))
            row = Voiceprint(
                owner_id=owner_id,
                name=f"基准模板{index}",
                embedding=vec.tobytes(),
                sample_ms=4000,
                sample_count=4,
                provisional=False,
            )
            db.add(row)
            db.flush()
            device, distance, noise, voice = buckets[index % len(buckets)]
            db.add(
                VoiceprintTemplate(
                    voiceprint_id=row.id,
                    slot=0,
                    embedding=vec.tobytes(),
                    dim=voice_template.EMBED_DIM,
                    quality=0.8,
                    confirmed=True,
                    scenario_device=device,
                    scenario_distance=distance,
                    scenario_noise=noise,
                    scenario_voice=voice,
                )
            )
        db.commit()

    started = time.perf_counter()
    voice_match.invalidate_cache()
    matrix, _rows = voice_template.templates_matrix(None, owner_id=owner_id)
    cold_load = time.perf_counter() - started
    loaded = int(matrix.shape[0])

    query_rng = np.random.default_rng(11)
    queries = [
        query_rng.standard_normal(voice_template.EMBED_DIM).astype(np.float32)
        for _ in range(args.queries)
    ]
    scenario = Scenario(device="local_mic", distance="near", noise="quiet", voice="normal")
    scope = voice_match._load_scope(None)
    mask = voice_match.scenario_mask(
        scope["rows"], scope["codes"], scope["vocab"], scenario
    )
    eligible_ratio = float(mask.mean()) if mask is not None else None

    def timed_matching(label: str, overrides: dict) -> dict:
        config_store.set_overrides(overrides)
        voice_match.invalidate_cache()
        voice_match._load(None)
        samples: list[float] = []
        hits = 0
        for vec in queries:
            started = time.perf_counter()
            result = voice_match.match(vec, meeting_id=None, scenario=scenario, speech_ms=2500)
            samples.append(time.perf_counter() - started)
            hits += 1 if result is not None else 0
        payload = _percentiles(samples)
        payload.update(
            {"label": label, "templates_loaded": loaded, "queries": args.queries, "hits": hits}
        )
        return payload

    prefilter = timed_matching(
        "scenario_prefilter", {"spk.stage1_min_templates": 50, "spk.stage1_topk": 200}
    )
    full_matrix = timed_matching(
        "full_matrix", {"spk.stage1_min_templates": 10**9, "spk.stage1_topk": 200}
    )
    legacy_mask_samples: list[float] = []
    vector_mask_samples: list[float] = []
    for _ in range(max(20, args.queries)):
        started = time.perf_counter()
        np.array([row["scenario"].match_score(scenario) >= 0.6 for row in scope["rows"]])
        legacy_mask_samples.append(time.perf_counter() - started)
        started = time.perf_counter()
        voice_match.scenario_mask(scope["rows"], scope["codes"], scope["vocab"], scenario)
        vector_mask_samples.append(time.perf_counter() - started)

    results["matching"] = {
        "template_count": loaded,
        "cold_matrix_build_ms": round(cold_load * 1000, 3),
        "prefilter_eligible_ratio": None if eligible_ratio is None else round(eligible_ratio, 4),
        "mask_cost": {
            "legacy_python_loop": _percentiles(legacy_mask_samples),
            "vectorized_int_codes": _percentiles(vector_mask_samples),
        },
        "prefilter": prefilter,
        "full_matrix": full_matrix,
    }

    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o777)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
