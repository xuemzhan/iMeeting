"""说话人身份命名空间与改名传播的回归测试。"""

from __future__ import annotations

import numpy as np
from sqlmodel import Session, select


def vec(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=192).astype(np.float32)
    return v / np.linalg.norm(v)


def test_rename_keeps_stable_key(client):
    """改名只改显示名，speaker_key 必须保持不变。"""
    from imeeting.engine.speaker import SpeakerResolver

    resolver = SpeakerResolver(threshold=0.5)
    label, key = resolver.resolve_with_key(vec(1))
    assert label == "说话人 1"

    assert resolver.rename(label, "王工") == 1
    label2, key2 = resolver.resolve_with_key(vec(1))
    assert (label2, key2) == ("王工", key)


def test_reconnect_restores_meeting_speakers(client):
    """新会话（重连）应加载本场已确认的说话人，编号/姓名接着走。"""
    from imeeting.engine import speaker
    from imeeting.engine.speaker import SpeakerResolver

    mid = 777
    v = vec(2)
    speaker.save_discovered(
        "说话人 1",
        v,
        source_meeting_id=mid,
        speaker_key="spk_test",
        meeting_id=mid,
    )

    resolver = SpeakerResolver(threshold=0.55, meeting_id=mid)
    label, key = resolver.resolve_with_key(v)
    assert (label, key) == ("说话人 1", "spk_test")


def test_provisional_voiceprint_is_meeting_scoped(client):
    """另一场会议不得复用本场的临时「说话人 1」。"""
    from imeeting.engine import speaker
    from imeeting.engine.speaker import SpeakerResolver

    mid = 888
    v = vec(3)
    speaker.save_discovered(
        "说话人 1",
        v,
        source_meeting_id=mid,
        speaker_key="spk_only_here",
        meeting_id=mid,
    )

    other = SpeakerResolver(threshold=0.55, meeting_id=mid + 1)
    _label, key = other.resolve_with_key(v)
    assert key != "spk_only_here", "临时声纹跨会议泄漏"


def test_save_discovered_updates_embedding(client):
    """同名临时声纹已存在时必须更新向量，而不是直接 return。

    旧实现按全局名字去重，导致本场说话人的向量永远不入库；
    改名时改到的是另一场会议的旧向量（真实数据相似度 -0.09）。
    """
    from imeeting.db import engine
    from imeeting.engine import speaker
    from imeeting.models import Voiceprint

    mid = 999
    first = vec(4)
    second = vec(5)
    speaker.save_discovered(
        "说话人 1",
        first,
        source_meeting_id=mid,
        speaker_key="spk_x",
        meeting_id=mid,
    )
    speaker.save_discovered(
        "说话人 1",
        second,
        source_meeting_id=mid,
        speaker_key="spk_x",
        meeting_id=mid,
    )

    with Session(engine) as db:
        rows = db.exec(select(Voiceprint).where(Voiceprint.meeting_id == mid)).all()
    assert len(rows) == 1
    stored = np.frombuffer(rows[0].embedding, dtype=np.float32)
    stored = stored / np.linalg.norm(stored)
    assert not np.allclose(stored, first, atol=1e-5), "向量没有被更新"
