"""会后说话人全局合并。"""

from __future__ import annotations

import numpy as np
from sqlmodel import Session, select


def _vec(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=192).astype(np.float32)
    return v / np.linalg.norm(v)


def _near(v: np.ndarray, eps: float = 0.03) -> np.ndarray:
    rng = np.random.default_rng(99)
    out = v + rng.normal(0, eps, size=v.shape).astype(np.float32)
    return out / np.linalg.norm(out)


def _seed_meeting(*, formal_target: bool = False) -> int:
    from imeeting.db import engine
    from imeeting.models import Meeting, Segment, User, Voiceprint

    a = _vec(1)
    b = _near(a)
    c = _vec(2)  # 与 a 基本不相似
    with Session(engine) as db:
        user = db.exec(select(User)).first()
        meeting = Meeting(title="合并测试", owner_id=user.id, status="finished")
        db.add(meeting)
        db.commit()
        db.refresh(meeting)

        large = Voiceprint(
            name="说话人 A",
            embedding=a.tobytes(),
            provisional=not formal_target,
            meeting_id=meeting.id,
            speaker_key="spk_a",
        )
        small = Voiceprint(
            name="说话人 B",
            embedding=b.tobytes(),
            provisional=True,
            meeting_id=meeting.id,
            speaker_key="spk_b",
        )
        other = Voiceprint(
            name="说话人 C",
            embedding=c.tobytes(),
            provisional=True,
            meeting_id=meeting.id,
            speaker_key="spk_c",
        )
        db.add_all([large, small, other])
        db.commit()
        for i in range(5):
            db.add(
                Segment(
                    meeting_id=meeting.id,
                    segment_id=f"s-a-{i}",
                    text="A",
                    is_final=True,
                    start_ms=i * 1000,
                    end_ms=i * 1000 + 800,
                    speaker=large.name,
                    speaker_key="spk_a",
                    seq=i,
                )
            )
        for i in range(2):
            db.add(
                Segment(
                    meeting_id=meeting.id,
                    segment_id=f"s-b-{i}",
                    text="B",
                    is_final=True,
                    start_ms=6000 + i * 1000,
                    end_ms=6000 + i * 1000 + 800,
                    speaker=small.name,
                    speaker_key="spk_b",
                    seq=5 + i,
                )
            )
        db.add(
            Segment(
                meeting_id=meeting.id,
                segment_id="s-c-0",
                text="C",
                is_final=True,
                start_ms=9000,
                end_ms=9800,
                speaker=other.name,
                speaker_key="spk_c",
                seq=7,
            )
        )
        db.commit()
        return meeting.id


def test_small_cluster_merges_into_similar_large_cluster(client):
    from imeeting.db import engine
    from imeeting.engine.speaker_merge import merge_meeting_speakers
    from imeeting.models import Segment, Voiceprint

    mid = _seed_meeting()
    result = merge_meeting_speakers(mid, min_segments=3, threshold=0.45)
    assert result["merged_clusters"] == 1
    assert result["relabeled_segments"] == 2

    with Session(engine) as db:
        segs = db.exec(
            select(Segment).where(Segment.meeting_id == mid).order_by(Segment.seq)
        ).all()
        b_segs = [s for s in segs if s.segment_id.startswith("s-b-")]
        assert all(s.speaker == "说话人 A" and s.speaker_key == "spk_a" for s in b_segs)
        vp_b = db.exec(
            select(Voiceprint).where(
                Voiceprint.meeting_id == mid, Voiceprint.speaker_key == "spk_b"
            )
        ).first()
        assert vp_b is None


def test_dissimilar_cluster_is_kept(client):
    from imeeting.db import engine
    from imeeting.engine.speaker_merge import merge_meeting_speakers
    from imeeting.models import Voiceprint

    mid = _seed_meeting()
    merge_meeting_speakers(mid, min_segments=3, threshold=0.95)
    with Session(engine) as db:
        names = {
            vp.speaker_key
            for vp in db.exec(
                select(Voiceprint).where(Voiceprint.meeting_id == mid)
            ).all()
        }
    assert "spk_c" in names


def test_formal_target_can_absorb_small_cluster(client):
    from imeeting.db import engine
    from imeeting.engine.speaker_merge import merge_meeting_speakers
    from imeeting.models import Segment

    mid = _seed_meeting(formal_target=True)
    result = merge_meeting_speakers(mid, min_segments=3, threshold=0.45)
    assert result["merged_clusters"] == 1
    with Session(engine) as db:
        b_segs = db.exec(
            select(Segment).where(
                Segment.meeting_id == mid,
                Segment.segment_id.like("s-b-%"),
            )
        ).all()
    assert all(s.speaker == "说话人 A" and s.speaker_key == "spk_a" for s in b_segs)


def test_merge_speakers_api(client):
    mid = _seed_meeting()
    resp = client.post(f"/api/v1/meetings/{mid}/merge-speakers")
    assert resp.status_code == 200, resp.text
    assert resp.json()["merged_clusters"] == 1


def test_match_enrolled_returns_stable_speaker_key(client):
    """正式声纹的匹配结果必须带上 speaker_key，不能退化成 vp_<id>。"""
    from imeeting.db import engine
    from imeeting.engine import speaker
    from imeeting.models import User, Voiceprint

    v = _vec(42)
    with Session(engine) as db:
        user = db.exec(select(User)).first()
        db.add(
            Voiceprint(
                name="张三",
                embedding=v.tobytes(),
                provisional=False,
                owner_id=user.id,
                speaker_key="spk_zhang",
            )
        )
        db.commit()
    hit = speaker.match_enrolled(v, 0.5, None)
    assert hit is not None
    assert hit[0] == "张三"
    assert hit[3] == "spk_zhang"
