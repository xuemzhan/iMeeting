"""说话人识别（声纹）。

背景：为什么不能直接用 FunASR 返回的 `spk` 字段
------------------------------------------------

FunASR 的 `spk` 是**单次 `generate()` 调用内部的聚类下标**，每次调用都从
0 开始重新编号。而本项目是「说完一句话就调用一次离线 generate 定稿」，
于是每一句都被独立地聚成第 0 类 —— 结果所有片段的说话人永远是 0。
实测确认：`generate(return_spk_res=True)` 对单句音频返回 `spk: 0`。

要真正区分说话人，必须在**会话层面**维护向量：
  1. 用 CAM++ 对每句定稿音频抽取 192 维声纹向量；
  2. 先和「已录入的声纹」比对，命中就给出姓名；
  3. 未命中则与本会话已出现的未知说话人聚类，给出「说话人 N」。

这样同时满足了「跨句区分说话人」和「声纹录入后能认出是谁」。
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..settings import get_config

logger = logging.getLogger(__name__)

EMBED_DIM = 192
SAMPLE_RATE = 16000

#: 声纹模型的默认值。管理员可在设置里换模型（spk.embedding_model）。
DEFAULT_EMBEDDING_MODEL = "iic/speech_campplus_sv_zh-cn_16k-common"

_model = None
_model_name: str | None = None
_model_lock = threading.Lock()


def _configured_model_name() -> str:
    return str(get_config("spk.embedding_model") or DEFAULT_EMBEDDING_MODEL)


def _load_model():
    """懒加载声纹模型。首次约 2 秒，之后常驻内存。

    模型名变了要重建：否则管理员在设置里换了声纹模型，程序仍拿旧模型跑，
    表现为「改了没反应」。
    """
    global _model, _model_name
    want = _configured_model_name()
    if _model is not None and _model_name == want:
        return _model
    with _model_lock:
        if _model is not None and _model_name == want:
            return _model
        try:
            from funasr import AutoModel

            _model = AutoModel(
                model=want,
                hub=get_config("asr.hub") or "ms",
                model_revision=get_config("asr.model_revision") or "master",
                disable_update=bool(get_config("asr.disable_update")),
            )
            _model_name = want
            logger.info("声纹模型已加载：%s", want)
        except Exception:
            logger.exception("声纹模型加载失败，说话人识别将不可用")
            _model = None
            _model_name = None
    return _model


def extract_embedding(pcm: np.ndarray) -> Optional[np.ndarray]:
    """对一段 16kHz float32 单声道音频抽取声纹向量（已 L2 归一化）。

    返回 None 表示失败（模型未加载或音频太短），调用方应容忍。
    """
    model = _load_model()
    if model is None:
        return None

    audio = np.asarray(pcm, dtype=np.float32).reshape(-1)
    # 太短的音频抽不出稳定向量，反而会产生错误的匹配。
    #
    # 这条下限是被真实数据逼出来的：实测会议里「别。」「啊，话。」这类
    # 一两个字的片段，其向量与**所有**其它片段的余弦相似度都只有
    # 0.15~0.26（连自己人的正常句子都够不上），于是每个短句都会新开一个
    # 说话人类，把一个人的会议拆成「说话人 1/2/3/4」。
    # 宁可不给说话人（显示为未知），也不要给出错误且会污染声纹库的标签。
    # 同样不能用 `or`：0 表示不限制，是合法值。
    _min_audio = get_config("spk.min_audio_ms")
    min_audio_ms = 1000 if _min_audio is None else int(_min_audio)
    if min_audio_ms > 0 and audio.size < SAMPLE_RATE * min_audio_ms / 1000:
        return None

    try:
        # 逐段抽声纹会触发 FunASR 的 tqdm 进度条；在服务/会后复核里
        # 229 段会刷满 stderr。关闭进度条，只保留结构化业务日志。
        # 兼容不接收该参数的自定义/测试替身模型。
        try:
            res = model.generate(input=audio, disable_pbar=True)
        except TypeError:
            res = model.generate(input=audio)
        if not res or not isinstance(res, list):
            return None
        emb = res[0].get("spk_embedding")
        if emb is None:
            return None
        if hasattr(emb, "detach"):  # torch.Tensor
            emb = emb.detach().cpu().numpy()
        v = np.asarray(emb, dtype=np.float32).reshape(-1)
        if v.size != EMBED_DIM or not np.isfinite(v).all():
            return None
        norm = float(np.linalg.norm(v))
        if norm < 1e-6:
            return None
        return v / norm
    except Exception:
        logger.exception("声纹向量抽取失败")
        return None


def preload_embedding_model() -> bool:
    """提前加载 CAM++，避免首段定稿承担模型冷启动。

    真实会议 #231 的首段 speaker_ms=2677ms，而后续 P95 只有 398ms；
    根因就是声纹模型在首段处理时才加载。服务预热和会话开始都会调用这里，
    重复调用由 ``_load_model`` 的锁和缓存保证幂等。
    """
    if not bool(get_config("spk.enabled")):
        return False
    return _load_model() is not None


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """余弦相似度（输入已归一化时即点积）。"""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


# ---------------------------------------------------------------------------
# 已录入声纹（数据库）
# ---------------------------------------------------------------------------

#: 按会议作用域缓存：(meeting_id -> (时间戳, [(姓名, 向量, 声纹id)]))
_CACHE: dict[
    int | None, tuple[float, list[tuple[str, np.ndarray, int | None, str | None]]]
] = {}
_CACHE_TTL = 10.0
_cache_lock = threading.Lock()


def load_enrolled(
    meeting_id: int | None = None, force: bool = False
) -> list[tuple[str, np.ndarray, int | None, str | None]]:
    """读取参与匹配的声纹。

    作用域规则（修复跨会议「说话人 1」串味）：

    - ``provisional=False``：人工命名过的正式身份，全局可见；
    - ``provisional=True``：只有 ``meeting_id`` 等于当前会议时才参与匹配。

    旧实现把自动发现的临时声纹当成全局身份，于是 9 月某场会议的
    「说话人 1」会抢在真实声纹之前命中，改名时也会改到那一行上。
    """
    now = time.time()
    with _cache_lock:
        cached = _CACHE.get(meeting_id)
        if not force and cached and now - cached[0] < _CACHE_TTL:
            return cached[1]
        rows: list[tuple[str, np.ndarray, int | None, str | None]] = []
        try:
            from sqlmodel import select

            from ..db import session_scope
            from ..models import Voiceprint

            with session_scope() as db:
                for vp in db.exec(select(Voiceprint)).all():
                    if vp.disabled:
                        continue
                    if vp.provisional and vp.meeting_id != meeting_id:
                        continue
                    v = np.frombuffer(vp.embedding, dtype=np.float32)
                    if v.size == EMBED_DIM:
                        rows.append(
                            (
                                vp.name,
                                v / (np.linalg.norm(v) + 1e-9),
                                vp.id,
                                vp.speaker_key,
                            )
                        )
        except Exception:
            logger.exception("读取已录入声纹失败")
        _CACHE[meeting_id] = (now, rows)
        return rows


def invalidate_cache() -> None:
    """录入/删除/改名后调用，让下次识别立刻看到最新数据。"""
    with _cache_lock:
        _CACHE.clear()


def match_enrolled(
    emb: np.ndarray,
    threshold: float,
    meeting_id: int | None = None,
    *,
    formal_only: bool = False,
) -> Optional[tuple[str, float, int | None, str | None]]:
    """与作用域内的声纹比对，返回 (姓名, 相似度, 声纹 id)；未命中返回 None。"""
    best: Optional[tuple[str, float, int | None, str | None]] = None
    for name, ref, voiceprint_id, speaker_key in load_enrolled(meeting_id):
        if formal_only and name.startswith(("说话人", "疑似")):
            continue
        score = cosine(emb, ref)
        if best is None or score > best[1]:
            best = (name, score, voiceprint_id, speaker_key)
    if best and best[1] >= threshold:
        return best
    return None


def save_discovered(
    name: str,
    emb: np.ndarray,
    source_meeting_id: int | None,
    *,
    speaker_key: str | None = None,
    meeting_id: int | None = None,
) -> None:
    """把开会时自动聚出来的「陌生说话人」落库为待命名声纹。

    旧实现按**全局名字**去重：发现「说话人 1」已存在就直接返回，
    导致本场说话人的向量从未入库。改名时按名字找行，改到的是另一场
    会议的旧向量（真实数据里相似度 -0.09），后续自然认不出来。

    现在按 ``speaker_key``（会议内稳定键）或 ``(meeting_id, name)``
    定位，并做滚动平均更新质心；临时声纹带 ``meeting_id``，不跨会议生效。
    """
    try:
        from sqlmodel import select

        from ..db import session_scope
        from ..models import Voiceprint, utcnow

        with session_scope() as db:
            stmt = select(Voiceprint)
            if speaker_key:
                stmt = stmt.where(Voiceprint.speaker_key == speaker_key)
            else:
                stmt = stmt.where(
                    Voiceprint.name == name,
                    Voiceprint.meeting_id == meeting_id,
                )
            existing = db.exec(stmt).first()
            if existing is not None:
                old = np.frombuffer(existing.embedding, dtype=np.float32)
                if old.size == EMBED_DIM:
                    merged = old + emb
                    norm = float(np.linalg.norm(merged))
                    if norm > 1e-6:
                        existing.embedding = (
                            merged / norm
                        ).astype(np.float32).tobytes()
                existing.name = name
                existing.sample_count += 1
                existing.quality_score = min(
                    1.0, 0.5 if existing.provisional else 0.9
                )
                existing.last_seen_at = utcnow()
                existing.updated_at = utcnow()
                db.add(existing)
                db.commit()
                invalidate_cache()
                return
            db.add(
                Voiceprint(
                    name=name,
                    embedding=emb.astype(np.float32).tobytes(),
                    sample_ms=0,
                    sample_count=1,
                    provisional=True,
                    source_meeting_id=source_meeting_id,
                    meeting_id=meeting_id,
                    speaker_key=speaker_key,
                    quality_score=0.4,
                    last_seen_at=utcnow(),
                )
            )
            db.commit()
        invalidate_cache()
    except Exception:
        # 声纹落库失败绝不能影响转写主流程
        logger.exception("待命名声纹落库失败 name=%s", name)


def load_meeting_speakers(
    meeting_id: int,
) -> list[tuple[str, str, np.ndarray]]:
    """读取某场会议已确认的说话人（key, label, 向量）。

    重连后的新会话据此恢复聚类，编号与姓名接着上一段走，而不是从
    「说话人 1」重新开始。
    """
    try:
        from sqlmodel import select

        from ..db import session_scope
        from ..models import Voiceprint

        out: list[tuple[str, str, np.ndarray]] = []
        with session_scope() as db:
            rows = db.exec(
                select(Voiceprint).where(Voiceprint.meeting_id == meeting_id)
            ).all()
            for vp in rows:
                if not vp.speaker_key:
                    continue
                v = np.frombuffer(vp.embedding, dtype=np.float32)
                if v.size == EMBED_DIM:
                    out.append(
                        (
                            vp.speaker_key,
                            vp.name,
                            v / (np.linalg.norm(v) + 1e-9),
                        )
                    )
        return out
    except Exception:
        logger.exception("读取会议说话人失败 meeting=%s", meeting_id)
        return []


# ---------------------------------------------------------------------------
# 会话内聚类：给未录入的说话人一个稳定的「说话人 N」
# ---------------------------------------------------------------------------


@dataclass
class SpeakerCluster:
    """会议内一个说话人的稳定身份。"""

    key: str
    vector: np.ndarray
    label: str


@dataclass
class SpeakerResolution:
    label: str
    key: str
    score: float = 0.0
    source: str = "cluster"


class SpeakerResolver:
    """一场会议内的说话人判定器。

    - 会议内聚类 → 「说话人 N」，但身份由稳定的 ``key`` 承载；
    - 已录声纹 → 直接返回姓名，并把该声纹挂成 key 为 ``vp_<id>`` 的簇；
    - 改名只改 ``label``，``key`` 不变，因此后续定稿、历史片段和重连
      会话都能对上同一个人。
    """

    def __init__(
        self, threshold: float = 0.6, meeting_id: int | None = None
    ) -> None:
        self.threshold = threshold
        self.enrolled_threshold = max(
            threshold, float(get_config("spk.enrolled_threshold") or 0.68)
        )
        self.ambiguous_threshold = min(
            self.enrolled_threshold,
            max(
                threshold,
                float(get_config("spk.enrolled_ambiguous_threshold") or 0.58),
            ),
        )
        self.arbitration_margin = float(
            get_config("spk.arbitration_margin") or 0.03
        )
        self.meeting_id = meeting_id
        self._clusters: list[SpeakerCluster] = []
        #: 本会话新聚出来的说话人 (key, label, 向量)，由调用方取走后落库
        self.new_clusters: list[tuple[str, str, np.ndarray]] = []
        self._next_index = 1

        if meeting_id is not None:
            for key, label, vector in load_meeting_speakers(meeting_id):
                self._clusters.append(SpeakerCluster(key, vector, label))
                match = re.match(r"说话人\s*(\d+)", label)
                if match:
                    self._next_index = max(self._next_index, int(match.group(1)) + 1)

    # ---- 内部匹配 -------------------------------------------------------
    def _best_cluster(
        self, emb: np.ndarray, threshold: float
    ) -> tuple[Optional[SpeakerCluster], float]:
        best: Optional[SpeakerCluster] = None
        best_score = -1.0
        for cluster in self._clusters:
            score = cosine(emb, cluster.vector)
            if score > best_score:
                best_score = score
                best = cluster
        if best is not None and best_score >= threshold:
            return best, best_score
        return None, max(0.0, best_score)

    def _attach_enrolled(
        self, emb: np.ndarray, threshold: float
    ) -> tuple[Optional[SpeakerCluster], float]:
        hit = match_enrolled(emb, threshold, self.meeting_id)
        if not hit:
            return None, 0.0
        cluster, score = self._attach_enrolled_hit(emb, hit)
        return cluster, score

    def _attach_enrolled_hit(
        self, emb: np.ndarray, hit: tuple[str, float, int | None, str | None]
    ) -> tuple[Optional[SpeakerCluster], float]:
        name, score, voiceprint_id, speaker_key = hit
        key = (
            speaker_key
            or (f"vp_{voiceprint_id}" if voiceprint_id is not None else f"nm_{name}")
        )
        for cluster in self._clusters:
            if cluster.key == key:
                merged = cluster.vector + emb
                cluster.vector = merged / (np.linalg.norm(merged) + 1e-9)
                cluster.label = name
                return cluster, score
        cluster = SpeakerCluster(key, emb, name)
        self._clusters.append(cluster)
        return cluster, score

    def _formal_enrolled_candidate(
        self, emb: np.ndarray
    ) -> tuple[str, float, int | None, str | None] | None:
        """只在正式录入身份中找候选。

        本场的临时「说话人 N」「疑似 X」由匿名簇路径处理，不能作为
        ``enrolled`` 候选，否则会被当成已知身份参与优先级仲裁。
        """
        return match_enrolled(
            emb,
            self.ambiguous_threshold,
            self.meeting_id,
            formal_only=True,
        )

    def _attach_ambiguous(
        self, emb: np.ndarray, hit: tuple[str, float, int | None, str | None]
    ) -> SpeakerResolution:
        """已知身份接近但未过阈值时，保留“疑似姓名”，不要并入匿名簇。"""
        name, score, _voiceprint_id, _speaker_key = hit
        label = f"疑似 {name}"
        for cluster in self._clusters:
            if cluster.label == label:
                merged = cluster.vector + emb
                cluster.vector = merged / (np.linalg.norm(merged) + 1e-9)
                return SpeakerResolution(label, cluster.key, score, "enrolled_ambiguous")
        key = f"spk_{uuid.uuid4().hex[:8]}"
        self._clusters.append(SpeakerCluster(key, emb, label))
        self.new_clusters.append((key, label, emb))
        return SpeakerResolution(label, key, score, "enrolled_ambiguous")

    # ---- 对外接口 -------------------------------------------------------
    def match_known_key(
        self, emb: np.ndarray, threshold: float | None = None
    ) -> Optional[tuple[str, str]]:
        """只匹配已有说话人，绝不新建类别；返回 (label, key)。"""
        hit = self.match_known_meta(emb, threshold)
        return (hit.label, hit.key) if hit else None

    def match_known_meta(
        self, emb: np.ndarray, threshold: float | None = None
    ) -> Optional[SpeakerResolution]:
        """只匹配已有身份，返回相似度与来源。"""
        thr = self.threshold if threshold is None else threshold
        # 已录入身份优先于匿名簇：否则一个被开场杂谈污染的匿名簇
        # 会在 0.5 就抢占，导致 0.9 的已知声纹也永远没有机会命中。
        hit = self._formal_enrolled_candidate(emb)
        if hit and hit[1] >= max(thr, self.enrolled_threshold):
            key = (
                hit[3]
                or (f"vp_{hit[2]}" if hit[2] is not None else f"nm_{hit[0]}")
            )
            return SpeakerResolution(hit[0], key, hit[1], "enrolled")
        cluster, score = self._best_cluster(emb, thr)
        if cluster is not None:
            return SpeakerResolution(cluster.label, cluster.key, score, "cluster")
        return None

    def match_known(
        self, emb: np.ndarray, threshold: float | None = None
    ) -> Optional[str]:
        hit = self.match_known_key(emb, threshold)
        return hit[0] if hit else None

    def resolve_with_key(self, emb: np.ndarray) -> tuple[str, str]:
        """返回 (显示名, 稳定 key)。"""
        result = self.resolve_with_meta(emb)
        return result.label, result.key

    def resolve_with_meta(self, emb: np.ndarray) -> SpeakerResolution:
        # 同时算匿名簇和已录入声纹两个候选，避免“先命中匿名簇就返回”
        # 造成的身份遮蔽。已录入高分永远优先；接近已知身份时优先保留
        # “疑似姓名”，只有匿名簇明显更可信时才归入匿名簇。
        cluster, cluster_score = self._best_cluster(emb, self.threshold)
        enrolled = self._formal_enrolled_candidate(emb)
        enrolled_score = enrolled[1] if enrolled is not None else 0.0

        if enrolled is not None and enrolled_score >= self.enrolled_threshold:
            attached, score = self._attach_enrolled_hit(emb, enrolled)
            if attached is not None:
                if cluster is not None:
                    logger.debug(
                        "已知声纹优先 meeting=%s name=%s enrolled=%.3f cluster=%.3f",
                        self.meeting_id,
                        attached.label,
                        score,
                        cluster_score,
                    )
                return SpeakerResolution(
                    attached.label, attached.key, score, "enrolled"
                )

        if (
            enrolled is not None
            and enrolled_score >= self.ambiguous_threshold
            and (
                cluster is None
                or enrolled_score + self.arbitration_margin >= cluster_score
            )
        ):
            return self._attach_ambiguous(emb, enrolled)

        if cluster is not None:
            merged = cluster.vector + emb
            cluster.vector = merged / (np.linalg.norm(merged) + 1e-9)
            return SpeakerResolution(
                cluster.label, cluster.key, cluster_score, "cluster"
            )

        if enrolled is not None:
            return self._attach_ambiguous(emb, enrolled)

        key = f"spk_{uuid.uuid4().hex[:8]}"
        label = f"说话人 {self._next_index}"
        self._next_index += 1
        self._clusters.append(SpeakerCluster(key, emb, label))
        self.new_clusters.append((key, label, emb))
        return SpeakerResolution(label, key, 0.0, "cluster")

    def resolve(self, emb: np.ndarray) -> Optional[str]:
        return self.resolve_with_key(emb)[0]

    def rename(self, old_label: str, new_name: str) -> int:
        """把显示名从旧标签改成新姓名，key 保持不变。"""
        changed = 0
        for cluster in self._clusters:
            if cluster.label == old_label:
                cluster.label = new_name
                changed += 1
        return changed

    def labels(self) -> list[str]:
        return [c.label for c in self._clusters]

    def cluster_vector(self, key: str) -> np.ndarray | None:
        for cluster in self._clusters:
            if cluster.key == key:
                return cluster.vector.copy()
        return None

    def cluster_items(self) -> list[tuple[str, str, np.ndarray]]:
        """返回当前内存聚类快照 (label, key, vector)，供持续落库。"""
        return [
            (cluster.label, cluster.key, cluster.vector.copy())
            for cluster in self._clusters
        ]
