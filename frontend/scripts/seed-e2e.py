"""为浏览器验收构造数据：独立 SQLite 里建一个 e2e 用户 + 一场带说话人的会议。

不碰开发库：所有连接参数都由环境变量给出（DATABASE_URL），由调用方准备临时文件。

用法（在 iMeeting/backend 目录）：
  set DATABASE_URL=sqlite:///C:/Temp/e2e.db
  set PYTHONPATH=src
  C:/Python314/python.exe ../frontend/scripts/seed-e2e.py
输出一行 JSON：{"meeting_id":1,"username":"e2e","password":"e2e-pass-123"}
"""

from __future__ import annotations

import json
import os

from sqlmodel import Session, select

from imeeting.db import engine, init_db
from imeeting.models import Meeting, Segment, User
from imeeting.security import hash_password

USERNAME = os.getenv("E2E_USERNAME", "e2e")
PASSWORD = os.getenv("E2E_PASSWORD", "e2e-pass-123")


def main() -> None:
    init_db()
    with Session(engine) as db:
        user = db.exec(select(User).where(User.username == USERNAME)).first()
        if user is None:
            user = User(
                username=USERNAME,
                display_name="E2E 验收",
                password_hash=hash_password(PASSWORD),
                role="admin",
                is_active=True,
                must_change_password=False,
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        meeting = Meeting(
            title="功能验收会",
            owner_id=user.id,
            status="finished",
            duration_ms=36_000,
            participants="张三, 李四, 王五",
            metadata_captured=True,
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        meeting_id = int(meeting.id)

        rows = [
            ("张三", "spk_a", "方案按 A 推进，周五发版。", 0, 5000),
            ("李四", "spk_b", "我补充一下回滚预案。", 6000, 10_000),
            ("王五", "spk_c", "我记录一下。", 11_000, 14_000),
            ("张三", "spk_a", "风险点今天就对齐。", 15_000, 20_000),
        ]
        for index, (speaker, key, text, start, end) in enumerate(rows):
            db.add(
                Segment(
                    meeting_id=meeting_id,
                    segment_id=f"e2e-{meeting.id}-{index}",
                    text=text,
                    original_text=text,
                    is_final=True,
                    start_ms=start,
                    end_ms=end,
                    speaker=speaker,
                    speaker_key=key,
                    speaker_source="manual",
                    speaker_score=0.9,
                    seq=index,
                )
            )
        db.commit()

    print(
        json.dumps(
            {
                "meeting_id": meeting_id,
                "username": USERNAME,
                "password": PASSWORD,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
