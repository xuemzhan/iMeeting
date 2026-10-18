"""增量落库与收尾批量落库的并发去重回归。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlmodel import Session, select


def test_concurrent_persist_keeps_single_row(client):
    """同一 segment_id 被并发写多次，只能有一条记录。"""
    from imeeting.api.ws.stream import _persist_segments
    from imeeting.db import engine
    from imeeting.models import Meeting, Segment, User

    with Session(engine) as db:
        user = db.exec(select(User)).first()
        db.add(Meeting(id=555, title="并发落库测试", owner_id=user.id, status="recording"))
        db.commit()

    payload = {
        "type": "final",
        "session_id": "sess-race",
        "segment_id": "s-sess-race-0-race",
        "text": "并发落库",
        "start_ms": 0,
        "end_ms": 1000,
        "speaker": "说话人 1",
        "speaker_key": "spk_race",
        "seq": 0,
    }

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: _persist_segments(555, [payload], 1000), range(4)))

    with Session(engine) as db:
        rows = db.exec(select(Segment).where(Segment.meeting_id == 555)).all()
    assert len(rows) == 1
    assert rows[0].session_id == "sess-race"
    assert rows[0].speaker_key == "spk_race"
