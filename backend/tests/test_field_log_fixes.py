"""Regressions for the October 31 / November 1 field failures."""
import asyncio
from unittest.mock import AsyncMock

import numpy as np
import pytest
from sqlmodel import Session, select


def test_two_voice_keys_for_one_person_are_idempotent(client):
    from imeeting import persons
    from imeeting.db import engine
    from imeeting.models import Meeting, MeetingParticipant, User
    with Session(engine) as db:
        user = db.exec(select(User)).first()
        meeting = Meeting(title="field", owner_id=user.id)
        db.add(meeting)
        db.commit()
        first = persons.ensure_auto_participant(db, meeting_id=meeting.id, speaker_key="a", label="张工")
        second = persons.ensure_auto_participant(db, meeting_id=meeting.id, speaker_key="b", label="张工")
        db.commit()
        assert first['person_id'] == second['person_id']
        rows = db.exec(select(MeetingParticipant)).all()
        assert len(rows) == 1 and rows[0].speaker_key == 'a'


def test_manual_cluster_rename_is_not_undone_by_enrolled_hit(client, monkeypatch):
    from imeeting.engine import speaker
    emb = np.zeros(192, dtype=np.float32)
    emb[0] = 1
    resolver = speaker.SpeakerResolver()
    monkeypatch.setattr(resolver, '_formal_enrolled_candidate', lambda _: ('旧姓名', .9, 1, 'key'))
    assert resolver.resolve_with_meta(emb).label == '旧姓名'
    resolver.rename('旧姓名', '确认姓名')
    assert resolver.resolve_with_meta(emb).label == '确认姓名'
    assert resolver.match_known_meta(emb).label == '确认姓名'


def test_strong_existing_voice_is_not_switched_for_small_score_gain(client, monkeypatch):
    from imeeting.engine import speaker
    emb = np.zeros(192, dtype=np.float32)
    emb[0] = 1
    resolver = speaker.SpeakerResolver()
    resolver._clusters.append(speaker.SpeakerCluster('main', emb.copy(), '主讲人'))
    monkeypatch.setattr(resolver, '_formal_enrolled_candidate', lambda _: ('其他人', .85, 2, 'other'))
    assert resolver.resolve_with_meta(emb).key == 'main'


def test_old_session_cannot_overwrite_new_session_status(client):
    from imeeting.application.meeting_sessions import persist_segments
    from imeeting.db import engine
    from imeeting.models import Meeting, MeetingSession, User
    with Session(engine) as db:
        user = db.exec(select(User)).first()
        meeting = Meeting(title='reconnect', owner_id=user.id, status='recording')
        db.add(meeting)
        db.commit()
        mid = meeting.id
        db.add(MeetingSession(meeting_id=mid, session_id='old'))
        db.add(MeetingSession(meeting_id=mid, session_id='new'))
        db.commit()
    persist_segments(mid, [], 0, final_status='failed', session_id='old')
    with Session(engine) as db:
        assert db.get(Meeting, mid).status == 'recording'


def test_progressive_backlog_advances_only_consumed_batch(client, monkeypatch):
    from imeeting.db import engine
    from imeeting.llm import progressive
    from imeeting.llm.types import TranscriptLine
    from imeeting.models import Meeting, User
    with Session(engine) as db:
        meeting = Meeting(title='backlog', owner_id=db.exec(select(User)).first().id)
        db.add(meeting)
        db.commit()
        mid = meeting.id
    previous = progressive.save_generated_minutes(mid, {'summary': '已有摘要。' * 30,
        'topics': [], 'todos': [], 'model': 'fake'}, is_draft=True, transcript_end_ms=1000)
    lines = [TranscriptLine(start_ms=i * 1000, end_ms=(i + 1) * 1000,
                            speaker='主讲人', text='内容' * 1000) for i in range(1, 10)]
    monkeypatch.setattr(progressive, 'load_transcript_lines', lambda _: lines)
    seen = []
    async def roll(prev, new_lines, knowledge_context=''):
        seen.extend(new_lines)
        return {'summary': previous.summary, 'topics': [], 'todos': [], 'model': 'fake'}
    monkeypatch.setattr(progressive, '_rolling_update', roll)
    payload = asyncio.run(progressive.generate_progressive_draft(mid))
    assert 0 < len(seen) < len(lines)
    assert payload is not None
    from imeeting.models import Minutes
    with Session(engine) as db:
        latest = db.exec(select(Minutes).order_by(Minutes.id.desc())).first()
        assert latest.transcript_end_ms == seen[-1].end_ms < lines[-1].end_ms


def test_socket_closed_during_ack_is_disconnect_not_internal_error():
    from fastapi import WebSocketDisconnect

    from imeeting.api.ws.stream import SocketSender
    socket = AsyncMock()
    socket.send_json.side_effect = RuntimeError('Cannot call "send" once a close message has been sent.')
    sender = SocketSender(socket)
    with pytest.raises(WebSocketDisconnect):
        asyncio.run(sender.send_json({'type': 'audio_ack'}))
    with pytest.raises(WebSocketDisconnect):
        asyncio.run(sender.send_json({'type': 'audio_ack'}))
    assert socket.send_json.await_count == 1


def test_truncated_provider_response_is_rejected_and_usage_recorded(client, monkeypatch):
    import httpx

    from imeeting.llm import client as llm
    instance = llm.LLMClient()
    instance.base_url = 'http://localhost/v1'
    instance.model = 'fake'
    recorded = []
    instance._record_usage = lambda **kwargs: recorded.append(kwargs)
    fake = AsyncMock()
    fake.__aenter__.return_value = fake
    fake.post.return_value = httpx.Response(200, json={
        'choices': [{'message': {'content': '{"summary":"unfinished'}, 'finish_reason': 'length'}],
        'usage': {'prompt_tokens': 100, 'completion_tokens': 4096},
    })
    monkeypatch.setattr(llm.httpx, 'AsyncClient', lambda **kwargs: fake)
    with pytest.raises(llm.LLMTruncatedError):
        asyncio.run(instance.chat([{'role': 'user', 'content': 'JSON'}], purpose='rolling_draft'))
    assert len(recorded) == 1 and recorded[0]['completion_tokens'] == 4096


def test_rolling_truncation_shrinks_batch_without_skipping(client, monkeypatch):
    from imeeting.llm import progressive
    from imeeting.llm.types import TranscriptLine
    from imeeting.models import Minutes
    previous = Minutes(meeting_id=5, summary='旧摘要' * 60, transcript_end_ms=1000)
    lines = [TranscriptLine(i * 1000, '主讲', '内容' * 400, (i + 1) * 1000) for i in range(1, 7)]
    monkeypatch.setattr(progressive, 'load_transcript_lines', lambda _: lines)
    monkeypatch.setattr(progressive, '_latest', lambda *a, **kw: previous)
    calls = []
    async def roll(prev, batch, context=''):
        calls.append(list(batch))
        if len(calls) == 1:
            raise progressive.LLMTruncatedError('length')
        return {'summary': '新摘要' * 60, 'topics': [], 'todos': [], 'model': 'fake'}
    monkeypatch.setattr(progressive, '_rolling_update', roll)
    saved = []
    def save(mid, result, **kwargs):
        saved.append(kwargs)
        return Minutes(id=1, meeting_id=mid, **{k: v for k, v in kwargs.items()})
    monkeypatch.setattr(progressive, 'save_generated_minutes', save)
    assert asyncio.run(progressive.generate_progressive_draft(5)) is not None
    assert len(calls) == 2 and len(calls[1]) < len(calls[0])
    assert calls[1][0] is lines[0]
    assert saved[0]['transcript_end_ms'] == calls[1][-1].end_ms


def test_different_voice_can_still_switch_identity(client, monkeypatch):
    from imeeting.engine import speaker
    main = np.zeros(192, dtype=np.float32)
    main[0] = 1
    other = np.zeros(192, dtype=np.float32)
    other[1] = 1
    resolver = speaker.SpeakerResolver()
    resolver._clusters.append(speaker.SpeakerCluster('main', main, '主讲人'))
    monkeypatch.setattr(resolver, '_formal_enrolled_candidate', lambda _: ('其他人', .9, 2, 'other'))
    assert resolver.resolve_with_meta(other).key == 'other'


def test_paused_speech_still_drains_minutes_backlog(monkeypatch):
    from imeeting.api.ws import stream
    from imeeting.llm import progressive
    from imeeting.llm.types import TranscriptLine

    lines = [TranscriptLine(i * 1000, '主讲', '内容' * 200, (i + 1) * 1000)
             for i in range(3)]
    class SessionStub:
        is_closed = False
        events = []
        async def _emit(self, event):
            self.events.append(event)
            if len(self.events) == 2:
                self.is_closed = True
    session = SessionStub()
    ticks = []
    async def sleep(_):
        ticks.append(1)
        if len(ticks) > 3:
            session.is_closed = True
    async def generate(mid, **kwargs):
        return {'transcript_end_ms': (len(session.events) + 1) * 1000}
    monkeypatch.setattr(stream.asyncio, 'sleep', sleep)
    monkeypatch.setattr(progressive, 'load_transcript_lines', lambda _: lines)
    monkeypatch.setattr(progressive, 'generate_progressive_draft', generate)
    asyncio.run(stream._progressive_minutes_loop(1, session))
    assert len(session.events) == 2
