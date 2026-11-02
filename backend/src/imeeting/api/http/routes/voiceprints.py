"""声纹录入与识别接口。

录入流程：前端用麦克风录一段 3~5 秒的语音 → 以 wav 上传 → 后端用 CAM++
抽 192 维向量存库 → 之后转写时命中该向量就把说话人显示为录入的姓名。
"""

from __future__ import annotations

import io
import json
import logging
import uuid
import wave

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from ....application.identity_access import identity_owner, owned_voiceprint
from ....application.meeting_access import owned_meeting
from ....db import get_session
from ....engine import speaker
from ....models import AuditLog, Segment, User, Voiceprint, utcnow
from .deps import get_current_user_or_anonymous

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/voiceprints", tags=["voiceprints"])

# 录入音频要求：太短抽不出稳定向量，太长没必要且拖慢上传
MIN_MS = 1500
MAX_MS = 15000


class VoiceprintOut(BaseModel):
    id: int
    name: str
    sample_ms: int
    created_at: str
    # 开会时自动发现、尚未人工命名的人
    provisional: bool = False
    source_meeting_id: int | None = None
    sample_count: int = 0
    quality_score: float = 0.0
    disabled: bool = False
    last_seen_at: str | None = None


class VoiceprintRename(BaseModel):
    name: str


class VoiceprintState(BaseModel):
    disabled: bool


class VoiceprintRebuild(BaseModel):
    meeting_id: int
    label: str
    name: str


def _out(row: Voiceprint) -> VoiceprintOut:
    return VoiceprintOut(
        id=row.id or 0,
        name=row.name,
        sample_ms=row.sample_ms,
        created_at=row.created_at.isoformat(),
        provisional=row.provisional,
        source_meeting_id=row.source_meeting_id,
        sample_count=row.sample_count,
        quality_score=row.quality_score,
        disabled=row.disabled,
        last_seen_at=row.last_seen_at.isoformat() if row.last_seen_at else None,
    )


def _decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    """把上传的 wav 解成 16kHz 单声道 float32。

    只接受 PCM wav：前端用 OfflineAudioContext / AudioWorklet 产出的就是
    标准 PCM，没必要为了少见格式引入解码依赖。
    """
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels = w.getnchannels()
            width = w.getsampwidth()
            rate = w.getframerate()
            frames = w.readframes(w.getnframes())
    except wave.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"音频格式无法识别（需要 PCM WAV）：{exc}",
        ) from exc

    if width == 2:
        pcm = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        # 可能是 int32 也可能是 float32，按取值范围判断
        raw = np.frombuffer(frames, dtype="<i4")
        pcm = raw.astype(np.float32) / 2147483648.0
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的位深 {width * 8} bit，请上传 16bit 或 32bit PCM WAV",
        )

    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)

    if rate != 16000:
        # 线性重采样到 16k（录入音频质量要求不高，够用且无额外依赖）
        n = int(len(pcm) * 16000 / rate)
        if n <= 0:
            raise HTTPException(status_code=400, detail="音频过短")
        pcm = np.interp(
            np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm
        ).astype(np.float32)

    return pcm, 16000


@router.get("", response_model=list[VoiceprintOut])
def list_voiceprints(
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    rows = db.exec(select(Voiceprint).order_by(Voiceprint.created_at.desc())).all()
    return [_out(r) for r in rows if user.role == "admin" or identity_owner(db, r) == user.id]


def _suggestion_pairs(rows: list[Voiceprint], threshold: float) -> list[dict]:
    """按余弦相似度给出「可能同一人」的声纹对（只计算，不写库）。"""
    vectors = []
    for row in rows:
        vec = np.frombuffer(row.embedding, dtype=np.float32)
        if vec.size:
            vectors.append((row, vec))
    out = []
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            left, lv = vectors[i]
            right, rv = vectors[j]
            if left.name == right.name:
                continue
            score = speaker.cosine(lv, rv)
            if score >= threshold:
                out.append(
                    {
                        "score": round(score, 3),
                        "source": _out(left).model_dump(),
                        "target": _out(right).model_dump(),
                    }
                )
    return sorted(out, key=lambda item: item["score"], reverse=True)[:100]


@router.get("/merge-suggestions")
def merge_suggestions(
    meeting_id: int | None = None,
    threshold: float = 0.62,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """返回可能属于同一人的声纹对，供人工确认后合并。"""
    rows = db.exec(select(Voiceprint).where(Voiceprint.disabled == False)).all()  # noqa: E712
    rows = [r for r in rows if user.role == "admin" or identity_owner(db, r) == user.id]
    if meeting_id is not None:
        owned_meeting(meeting_id, user, db)
        rows = [
            row
            for row in rows
            if row.meeting_id in (None, meeting_id) or not row.provisional
        ]
    return _suggestion_pairs(rows, threshold)


@router.post("/rebuild-identity", response_model=VoiceprintOut)
def rebuild_identity(
    payload: VoiceprintRebuild,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """用某场会议的真实音频重建全局声纹身份。

    用于修复历史上“人被匹配成了另一位已录入老师”后只改了文字、
    没有留下正确声纹的情况。
    """
    owned_meeting(payload.meeting_id, user, db, write=True)
    name = payload.name.strip()
    label = payload.label.strip()
    if not name:
        raise HTTPException(status_code=400, detail="姓名不能为空")
    existing = next((row for row in db.exec(select(Voiceprint).where(Voiceprint.name == name)).all() if identity_owner(db, row) == user.id), None)
    if existing is not None:
        return _out(existing)
    segment = db.exec(
        select(Segment)
        .where(
            Segment.meeting_id == payload.meeting_id,
            Segment.speaker == label,
            Segment.speaker_key.is_not(None),
        )
        .limit(1)
    ).first()
    key = segment.speaker_key if segment else None
    vector = _meeting_speaker_vector(db, payload.meeting_id, label, key)
    if vector is None:
        raise HTTPException(
            status_code=422,
            detail="无法从该会议音频提取声纹，请确认会议留存了音频且片段有效",
        )
    row = Voiceprint(
        owner_id=user.id,
        name=name,
        embedding=vector.astype(np.float32).tobytes(),
        sample_ms=0,
        sample_count=1,
        quality_score=0.8,
        provisional=False,
        source_meeting_id=payload.meeting_id,
        meeting_id=payload.meeting_id,
        speaker_key=f"spk_{uuid.uuid4().hex[:8]}",
        last_seen_at=utcnow(),
    )
    db.add(row)
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="voiceprint_rebuild",
            target=f"meeting:{payload.meeting_id}",
            payload=json.dumps({"label": label, "name": name}, ensure_ascii=False),
        )
    )
    db.commit()
    db.refresh(row)
    speaker.invalidate_cache()
    from ....engine import voice_match
    voice_match.invalidate_cache()
    return _out(row)


@router.post("", response_model=VoiceprintOut)
async def create_voiceprint(
    name: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """录入一条声纹。同名会覆盖（重新录入同一个人的场景）。"""
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写姓名")
    if len(name) > 32:
        raise HTTPException(status_code=400, detail="姓名过长（最多 32 字）")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="没有收到音频数据")

    pcm, _ = _decode_wav(data)
    ms = int(len(pcm) / 16000 * 1000)
    if ms < MIN_MS:
        raise HTTPException(
            status_code=400, detail=f"录音太短（{ms}ms），请至少录制 {MIN_MS // 1000} 秒"
        )
    if ms > MAX_MS:
        raise HTTPException(
            status_code=400, detail=f"录音太长（{ms}ms），请控制在 {MAX_MS // 1000} 秒内"
        )

    emb = speaker.extract_embedding(pcm)
    if emb is None:
        raise HTTPException(
            status_code=400,
            detail="没能提取出声纹特征。请靠近麦克风、正常语速连续说话几秒后重试。",
        )

    # 同名覆盖：同一人重录时不该出现两条
    existing = next((row for row in db.exec(select(Voiceprint).where(Voiceprint.name == name)).all() if identity_owner(db, row) == user.id), None)
    if existing is not None:
        existing.embedding = emb.astype(np.float32).tobytes()
        existing.sample_ms = ms
        existing.updated_at = utcnow()
        db.add(existing)
        db.commit()
        db.refresh(existing)
        speaker.invalidate_cache()
        from ....engine import voice_match
        voice_match.invalidate_cache()
        return _out(existing)

    row = Voiceprint(
        owner_id=user.id,
        name=name,
        embedding=emb.astype(np.float32).tobytes(),
        sample_ms=ms,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    # 清缓存，让下一次识别立刻用上新声纹
    speaker.invalidate_cache()
    from ....engine import voice_match
    voice_match.invalidate_cache()
    return _out(row)


class RelabelRequest(BaseModel):
    meeting_id: int
    from_label: str
    to_name: str
    decision: str | None = None
    conflict_id: int | None = None
    target_voiceprint_id: int | None = None
    slot: int | None = Field(default=None, ge=0, le=4)


class RelabelResponse(BaseModel):
    renamed_voiceprint: bool
    updated_segments: int
    name: str
    identity_created: bool = False
    corrected_false_match: bool = False


def _meeting_speaker_vector(
    db: Session, meeting_id: int, from_label: str, speaker_key: str | None
) -> np.ndarray | None:
    """从活跃会话或会议录音中恢复当前说话人的真实质心。"""
    from ....engine import streaming

    if speaker_key:
        active = streaming.active_speaker_vector(meeting_id, speaker_key)
        if active is not None:
            return active
    from ....models import Meeting

    meeting = db.get(Meeting, meeting_id)
    if meeting is None or not meeting.audio_path:
        return None
    path = meeting.audio_path
    from pathlib import Path

    if not Path(path).exists():
        return None
    rows = db.exec(
        select(Segment)
        .where(
            Segment.meeting_id == meeting_id,
            (Segment.speaker == from_label)
            | (Segment.speaker_key == speaker_key if speaker_key else False),
        )
        .order_by((Segment.end_ms - Segment.start_ms).desc())
        .limit(6)
    ).all()
    if not rows:
        return None
    try:
        import soundfile as sf

        vectors = []
        for row in rows:
            audio, sr = sf.read(
                path,
                start=int(row.start_ms * 16000 / 1000),
                frames=max(1, int((row.end_ms - row.start_ms) * 16000 / 1000)),
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
            emb = speaker.extract_embedding(np.asarray(audio, dtype=np.float32))
            if emb is not None:
                vectors.append(emb)
        if not vectors:
            return None
        merged = np.sum(vectors, axis=0)
        norm = float(np.linalg.norm(merged))
        return (merged / norm).astype(np.float32) if norm > 1e-6 else None
    except Exception:
        logger.exception("会议说话人质心提取失败 meeting=%s", meeting_id)
        return None


@router.post("/relabel", response_model=RelabelResponse)
def relabel_speaker(
    payload: RelabelRequest,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """把某场会议里的说话人标签改成真实姓名，并回溯更新片段。

    使用场景：会议进行中，屏幕上出现了「说话人 1」，用户知道这是谁，
    直接点标签改名。此时必须同时做两件事：
      1. 改声纹库里的名字（provisional → False），让**以后的会议**认得出；
     2. 把**本场会议**里 speaker == 旧标签 的记录一起改掉，
         否则回看历史时仍然是「说话人 1」，改名只对将来生效、看着像没生效。

    必须带 ``meeting_id``：旧实现按全局标签更新片段，真实长测里把另一场
    会议的 43 条记录一起改成了同一个名字。

    同时通知该会议的活跃会话，让后续定稿立即使用新名字；只改数据库的话，
    运行中的 resolver 仍会继续输出旧标签。
    """
    if not payload.from_label.strip() or not payload.to_name.strip():
        raise HTTPException(400, "旧标签与新姓名都不能为空")
    if len(payload.to_name.strip()) > 32:
        raise HTTPException(400, "姓名过长（最多 32 字）")
    from .... import persons
    payload = payload.model_copy(update={"from_label": payload.from_label.strip(), "to_name": persons.normalize_name(payload.to_name)})
    scoped_owner = owned_meeting(payload.meeting_id, user, db, write=True).owner_id

    def sync_identity(segments):
        persons.sync_transcript_identity(db, meeting_id=payload.meeting_id, owner_id=scoped_owner,
            from_label=payload.from_label, to_name=payload.to_name.strip(), segments=segments)
    scoped_ids = [row.id for row in db.exec(select(Voiceprint)).all() if identity_owner(db, row) == scoped_owner]
    if user.role != "admin" and user.id != scoped_owner:
        # Meeting editors may correct this transcript, not the owner's private
        # reusable biometric library.
        segments = db.exec(select(Segment).where(Segment.meeting_id == payload.meeting_id, Segment.speaker == payload.from_label)).all()
        for segment in segments:
            segment.speaker = payload.to_name.strip()[:32]
            segment.speaker_source = "manual"
            segment.speaker_score = 1.0
            db.add(segment)
        db.add(AuditLog(user_id=user.id, username=user.username, action="meeting_speaker_relabel", target=f"meeting:{payload.meeting_id}", payload=json.dumps({"from": payload.from_label, "to": payload.to_name})))
        sync_identity(segments)
        db.commit()
        persons.publish_identity_changes(db)
        from ....engine.streaming import rename_active_speaker
        rename_active_speaker(payload.meeting_id, payload.from_label, payload.to_name)
        return RelabelResponse(renamed_voiceprint=False, updated_segments=len(segments), name=payload.to_name)
    from ....application.speaker_confirmation import preflight_relabel
    chosen_name = preflight_relabel(db, user, payload, scoped_owner, _meeting_speaker_vector)
    if chosen_name is None:
        return RelabelResponse(renamed_voiceprint=False, updated_segments=0, name=payload.from_label)
    payload = payload.model_copy(update={"to_name": chosen_name})
    meeting_id = int(payload.meeting_id)
    from_label = payload.from_label.strip()
    to_name = payload.to_name.strip()

    if not from_label or not to_name:
        raise HTTPException(status_code=400, detail="旧标签与新姓名都不能为空")
    if len(to_name) > 32:
        raise HTTPException(status_code=400, detail="姓名过长（最多 32 字）")
    if from_label == to_name:
        # 同名提交表示人工确认低置信说话人。
        segs = db.exec(
            select(Segment).where(
                Segment.meeting_id == meeting_id,
                Segment.speaker == from_label,
            )
        ).all()
        for seg in segs:
            seg.speaker_source = "manual"
            seg.speaker_score = 1.0
            db.add(seg)
        sync_identity(segs)
        db.add(
            AuditLog(
                user_id=user.id,
                username=user.username,
                action="speaker_confirm",
                target=f"meeting:{meeting_id}",
                payload=json.dumps(
                    {"name": to_name, "segments": len(segs)},
                    ensure_ascii=False,
                ),
            )
        )
        db.commit()
        persons.publish_identity_changes(db)
        return RelabelResponse(
            renamed_voiceprint=False,
            updated_segments=len(segs),
            name=to_name,
        )

    # 1) 先按当前片段真正使用的 speaker_key 找声纹；不能只按
    # “当前会议 + 旧名字”查，否则全局命中其他会议声纹时找不到记录。
    renamed = False
    identity_created = False
    corrected_false_match = False
    segment_key_row = db.exec(
        select(Segment)
        .where(
            Segment.meeting_id == meeting_id,
            Segment.speaker == from_label,
            Segment.speaker_key.is_not(None),
        )
        .limit(1)
    ).first()
    speaker_key = segment_key_row.speaker_key if segment_key_row else None
    vp = None
    if speaker_key:
        vp = db.exec(
            select(Voiceprint).where(Voiceprint.id.in_(scoped_ids), Voiceprint.speaker_key == speaker_key)
        ).first()
    if vp is None:
        vp = db.exec(
            select(Voiceprint).where(Voiceprint.id.in_(scoped_ids))
            .where(
                Voiceprint.meeting_id == meeting_id,
                Voiceprint.name == from_label,
            )
            .order_by(Voiceprint.updated_at.desc())
        ).first()
    speaker_key = vp.speaker_key if vp is not None else None
    if vp is not None:
        clash = db.exec(
            select(Voiceprint).where(Voiceprint.id.in_(scoped_ids), Voiceprint.name == to_name, Voiceprint.id != vp.id)
        ).first()
        if clash is not None and not payload.decision:
            raise HTTPException(status_code=409, detail=f"已存在名为「{to_name}」的声纹")
        # 全局已命名身份被误匹配时，绝不能把另一个人的全局声纹改名。
        # 基于本场真实质心创建新身份，保留原身份历史。
        if clash is not None:
            corrected_false_match = True
            if vp.provisional:
                vp.disabled = True
                db.add(vp)
        elif vp.meeting_id not in (None, meeting_id) and not vp.provisional:
            corrected_false_match = True
            vector = _meeting_speaker_vector(
                db, meeting_id, from_label, speaker_key
            )
            if vector is not None:
                db.add(
                    Voiceprint(
                        owner_id=user.id,
                        name=to_name,
                        embedding=vector.astype(np.float32).tobytes(),
                        sample_ms=0,
                        sample_count=1,
                        quality_score=0.75,
                        provisional=False,
                        source_meeting_id=meeting_id,
                        meeting_id=meeting_id,
                        speaker_key=f"spk_{uuid.uuid4().hex[:8]}",
                        last_seen_at=utcnow(),
                    )
                )
                identity_created = True
            renamed = False
        else:
            vp.name = to_name
            vp.provisional = False
            vp.updated_at = utcnow()
            db.add(vp)
            renamed = True
    else:
        vector = _meeting_speaker_vector(db, meeting_id, from_label, speaker_key)
        if vector is not None:
            db.add(
                Voiceprint(
                    owner_id=user.id,
                    name=to_name,
                    embedding=vector.astype(np.float32).tobytes(),
                    sample_ms=0,
                    sample_count=1,
                    quality_score=0.75,
                    provisional=False,
                    source_meeting_id=meeting_id,
                    meeting_id=meeting_id,
                    speaker_key=f"spk_{uuid.uuid4().hex[:8]}",
                    last_seen_at=utcnow(),
                )
            )
            identity_created = True

    # 2) 回溯更新已落库的转写片段
    stmt = select(Segment).where(
        Segment.meeting_id == meeting_id,
        Segment.speaker == from_label,
    )
    if speaker_key:
        stmt = select(Segment).where(
            Segment.meeting_id == meeting_id,
            (Segment.speaker_key == speaker_key)
            | (Segment.speaker == from_label),
        )
    segs = db.exec(stmt).all()
    for s in segs:
        s.speaker = to_name
        s.speaker_source = "manual"
        s.speaker_score = 1.0
        db.add(s)
    sync_identity(segs)

    # 2.1) 迁移「按显示名兜底」的说话人重要程度（历史数据没有 speaker_key 时用）。
    try:
        from .... import speaker_roles

        speaker_roles.migrate_label_key(db, meeting_id, from_label, to_name)
    except Exception:
        logger.exception(
            "迁移说话人重要程度失败 meeting=%s %s->%s",
            meeting_id,
            from_label,
            to_name,
        )

    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="relabel_speaker",
            target=f"meeting:{meeting_id}",
            payload=json.dumps(
                {
                    "from": from_label,
                    "to": to_name,
                    "segments": len(segs),
                    "voiceprint": vp.id if vp is not None else None,
                    "identity_created": identity_created,
                    "corrected_false_match": corrected_false_match,
                },
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    persons.publish_identity_changes(db)
    speaker.invalidate_cache()
    from ....engine import voice_match
    voice_match.invalidate_cache()

    # 同步运行中的会话（标签 + speaker_update 推送）
    try:
        from ....engine import streaming

        streaming.rename_active_speaker(meeting_id, from_label, to_name)
    except Exception:
        logger.exception("通知活跃会话改名失败 meeting=%s", meeting_id)

    return RelabelResponse(
        renamed_voiceprint=renamed,
        updated_segments=len(segs),
        name=to_name,
        identity_created=identity_created,
        corrected_false_match=corrected_false_match,
    )


def _merge_pair(db: Session, user: User, source_id: int, target_id: int) -> RelabelResponse:
    """把 source 合并进 target：接口与批量执行共用同一份口径。

    真实场景：同一个人的话被聚类拆成了「说话人 2 / 说话人 3 / 说话人 4」，
    用户想把它们并成一个。光靠「改名」做不到——目标名字已存在会冲突；
    所以需要这个显式的合并操作：把 source 名下的转写片段全部改挂到
    target 名下，再删掉 source。

    合并后的向量取 target 的（不重新求平均）：平均两个可能来自不同
    录音条件的向量，未必比 target 本身更可靠，而 target 通常是用户
    已经确认过的那一个。
    """
    owned_voiceprint(db, source_id, user, write=True)
    owned_voiceprint(db, target_id, user, write=True)
    if identity_owner(db, db.get(Voiceprint, source_id)) != identity_owner(db, db.get(Voiceprint, target_id)):
        raise HTTPException(409, "不能跨账号合并声纹")
    if source_id == target_id:
        raise HTTPException(status_code=400, detail="不能合并到自己")

    source = db.get(Voiceprint, source_id)
    target = db.get(Voiceprint, target_id)
    if source is None or target is None:
        raise HTTPException(status_code=404, detail="声纹不存在")

    from_label, to_name = source.name, target.name
    scoped_meeting = source.meeting_id

    # 转写片段改挂到目标名下（有会议作用域时只改本场）
    stmt = select(Segment).where(Segment.speaker == from_label)
    if scoped_meeting is not None:
        from sqlalchemy import or_

        conditions = [Segment.speaker == from_label]
        if source.speaker_key:
            conditions.append(Segment.speaker_key == source.speaker_key)
        stmt = select(Segment).where(
            Segment.meeting_id == scoped_meeting, or_(*conditions)
        )
    segs = db.exec(stmt).all()
    for s in segs:
        s.speaker = to_name
        if target.speaker_key:
            s.speaker_key = target.speaker_key
        db.add(s)

    # 人员的永久关联跟着身份走：target 还没关联时继承 source 的，避免合并后丢身份。
    if getattr(target, "person_id", None) is None and getattr(source, "person_id", None) is not None:
        target.person_id = source.person_id
        db.add(target)
    from ....application.voiceprint_lifecycle import merge_dependents
    merge_dependents(db, source.id, target.id)
    db.delete(source)
    db.commit()
    speaker.invalidate_cache()
    from ....engine import voice_match
    voice_match.invalidate_cache()
    if scoped_meeting is not None:
        try:
            from ....engine import streaming
            streaming.rename_active_speaker(scoped_meeting, from_label, to_name)
        except Exception:
            logger.exception("通知活跃会话合并失败 meeting=%s", scoped_meeting)
    return RelabelResponse(
        renamed_voiceprint=True, updated_segments=len(segs), name=to_name
    )


@router.post("/{source_id}/merge/{target_id}", response_model=RelabelResponse)
def merge_voiceprints(
    source_id: int,
    target_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """把一条声纹合并进另一条。

    真实场景：同一个人的话被聚类拆成了「说话人 2 / 说话人 3 / 说话人 4」，
    用户想把它们并成一个。光靠「改名」做不到——目标名字已存在会冲突；
    所以需要这个显式的合并操作：把 source 名下的转写片段全部改挂到
    target 名下，再删掉 source。

    合并后的向量取 target 的（不重新求平均）：平均两个可能来自不同
    录音条件的向量，未必比 target 本身更可靠，而 target 通常是用户
    已经确认过的那一个。
    """
    return _merge_pair(db, user, source_id, target_id)


@router.patch("/{voiceprint_id}", response_model=VoiceprintOut)
def rename_voiceprint(
    voiceprint_id: int,
    payload: VoiceprintRename,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """给声纹改名。改名后 provisional 自动置 False（即「转正」）。

    主要用途：把开会时自动发现的「说话人 1」改成真实姓名，
    之后所有会议都会直接认出这个人。
    """
    owned_voiceprint(db, voiceprint_id, user, write=True)
    row = db.get(Voiceprint, voiceprint_id)
    if row is None:
        raise HTTPException(status_code=404, detail="声纹不存在")

    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="姓名不能为空")
    if len(name) > 32:
        raise HTTPException(status_code=400, detail="姓名过长（最多 32 字）")

    clash = db.exec(
        select(Voiceprint).where(Voiceprint.owner_id == row.owner_id, Voiceprint.name == name, Voiceprint.id != voiceprint_id)
    ).first()
    if clash is not None:
        raise HTTPException(status_code=409, detail=f"已存在名为「{name}」的声纹")

    old_name = row.name
    row.name = name
    row.provisional = False
    row.updated_at = utcnow()
    db.add(row)
    # 如果这条声纹属于某场会议，顺手把该会议里的旧标签一起改掉
    if row.meeting_id is not None and old_name != name:
        from sqlalchemy import or_

        conditions = [Segment.speaker == old_name]
        if row.speaker_key:
            conditions.append(Segment.speaker_key == row.speaker_key)
        for seg in db.exec(
            select(Segment).where(
                Segment.meeting_id == row.meeting_id, or_(*conditions)
            )
        ).all():
            seg.speaker = name
            db.add(seg)
    db.commit()
    db.refresh(row)
    speaker.invalidate_cache()
    from ....engine import voice_match
    voice_match.invalidate_cache()
    if row.meeting_id is not None and old_name != name:
        try:
            from ....engine import streaming

            streaming.rename_active_speaker(row.meeting_id, old_name, name)
        except Exception:
            logger.exception("通知活跃会话改名失败 meeting=%s", row.meeting_id)
    return _out(row)


@router.patch("/{voiceprint_id}/state", response_model=VoiceprintOut)
def set_voiceprint_state(
    voiceprint_id: int,
    payload: VoiceprintState,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    owned_voiceprint(db, voiceprint_id, user, write=True)
    row = db.get(Voiceprint, voiceprint_id)
    if row is None:
        raise HTTPException(status_code=404, detail="声纹不存在")
    row.disabled = payload.disabled
    row.updated_at = utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    speaker.invalidate_cache()
    from ....engine import voice_match
    voice_match.invalidate_cache()
    return _out(row)


@router.delete("/{voiceprint_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_voiceprint(
    voiceprint_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    owned_voiceprint(db, voiceprint_id, user, write=True)
    row = db.get(Voiceprint, voiceprint_id)
    if row is None:
        raise HTTPException(status_code=404, detail="声纹不存在")
    from ....application.voiceprint_lifecycle import remove_dependents
    remove_dependents(db, voiceprint_id)
    db.delete(row)
    db.commit()
    speaker.invalidate_cache()
    from ....engine import voice_match
    voice_match.invalidate_cache()
    return None


class MergeSuggestionsApply(BaseModel):
    """「全库近邻合并」的执行参数。

    ``dry_run`` 默认 True：先出计划再动手。``include_named`` 默认 False，
    即只允许把「待命名」并进更完整的一条，不自动合并两个已命名身份 ——
    自动合并是破坏性操作（source 会被删除），不能只凭相似度。
    """

    threshold: float = 0.92
    limit: int = 20
    dry_run: bool = True
    include_named: bool = False
    meeting_id: int | None = None


@router.post("/merge-suggestions/apply")
def apply_merge_suggestions(
    payload: MergeSuggestionsApply,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """执行全库近邻合并：每对只并一次，冲突对跳过，逐对留审计。

    ``GET /merge-suggestions`` 只给建议、从不闭环，导致「同一人两条声纹」
    长期并存。这里补上执行入口，但保留两道闸：默认 dry-run，默认不合并
    两个已命名身份。
    """
    threshold = min(max(float(payload.threshold), 0.5), 0.999)
    limit = max(1, min(int(payload.limit), 50))
    rows = db.exec(select(Voiceprint).where(Voiceprint.disabled == False)).all()  # noqa: E712
    rows = [r for r in rows if user.role == "admin" or identity_owner(db, r) == user.id]
    if payload.meeting_id is not None:
        owned_meeting(payload.meeting_id, user, db)
        rows = [
            row
            for row in rows
            if row.meeting_id in (None, payload.meeting_id) or not row.provisional
        ]
    candidates = _suggestion_pairs(rows, threshold)

    used: set[int] = set()
    plan: list[dict] = []
    for item in candidates:
        source, target = item["source"], item["target"]
        if source["id"] in used or target["id"] in used:
            continue
        if not payload.include_named and not (source["provisional"] or target["provisional"]):
            continue
        used.update({int(source["id"]), int(target["id"])})
        plan.append({"score": item["score"], "source": source, "target": target})
        if len(plan) >= limit:
            break

    if payload.dry_run:
        return {
            "dry_run": True,
            "threshold": threshold,
            "candidates": len(candidates),
            "plan": plan,
            "applied": [],
            "failed": [],
        }

    applied: list[dict] = []
    failed: list[dict] = []
    for item in plan:
        source_id = int(item["source"]["id"])
        target_id = int(item["target"]["id"])
        try:
            result = _merge_pair(db, user, source_id, target_id)
        except HTTPException as exc:
            db.rollback()
            failed.append({"source_id": source_id, "target_id": target_id, "detail": exc.detail})
            continue
        applied.append(
            {
                "source_id": source_id,
                "source_name": item["source"]["name"],
                "target_id": target_id,
                "target_name": item["target"]["name"],
                "score": item["score"],
                "updated_segments": result.updated_segments,
            }
        )
    if applied:
        db.add(
            AuditLog(
                user_id=user.id,
                username=user.username,
                action="voiceprint_bulk_merge",
                target=f"owner:{user.id}",
                payload=json.dumps({"threshold": threshold, "applied": applied}, ensure_ascii=False),
            )
        )
        db.commit()
    return {
        "dry_run": False,
        "threshold": threshold,
        "candidates": len(candidates),
        "plan": plan,
        "applied": applied,
        "failed": failed,
    }


@router.post("/{voiceprint_id}/enroll")
async def enroll_voiceprint_sample(
    voiceprint_id: int,
    file: UploadFile = File(...),
    slot: int | None = Form(None),
    device: str = Form(""),
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """跨设备重新注册：把新环境录的一段音频追加为该人的一条场景模板。

    与 ``POST /voiceprints`` 的关键差别：同名录入会**覆盖质心**，等于用新环境
    替换旧基线；这里只新增一条模板，原有基线与其他场景样本都保留
    （REQ-002 FR-5 的「跨设备重新注册向导」就建在这条路径上）。
    """
    row = owned_voiceprint(db, voiceprint_id, user, write=True)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="没有收到音频数据")
    pcm, _ = _decode_wav(data)
    ms = int(len(pcm) / 16000 * 1000)
    if ms < MIN_MS:
        raise HTTPException(
            status_code=400, detail=f"录音太短（{ms}ms），请至少录制 {MIN_MS // 1000} 秒"
        )
    if ms > MAX_MS:
        raise HTTPException(
            status_code=400, detail=f"录音太长（{ms}ms），请控制在 {MAX_MS // 1000} 秒内"
        )
    emb = speaker.extract_embedding(pcm)
    if emb is None:
        raise HTTPException(
            status_code=400,
            detail="没能提取出声纹特征。请靠近麦克风、正常语速连续说话几秒后重试。",
        )

    from ....engine import voice_template

    scenario = voice_template.Scenario(device=(device or "").strip()[:16])
    try:
        template = voice_template.store_template(
            db,
            voiceprint_id=row.id,
            embedding=np.asarray(emb, dtype=np.float32),
            scenario=scenario,
            quality=0.85,
            sample_ms=ms,
            confirmed=True,
            confirmed_by=user.id,
            preferred_slot=slot,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="voiceprint_reenroll",
            target=f"voiceprint:{row.id}",
            payload=json.dumps(
                {
                    "template_id": template.id,
                    "slot": template.slot,
                    "sample_ms": ms,
                    "device": scenario.device,
                },
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    speaker.invalidate_cache()
    from ....engine import voice_match
    voice_match.invalidate_cache()
    return {
        "voiceprint_id": row.id,
        "template_id": template.id,
        "slot": template.slot,
        "sample_ms": ms,
    }
