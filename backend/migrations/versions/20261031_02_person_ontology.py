"""人员本体：person / person_alias / person_attribute / meeting_participant.

建表本身由 ``SQLModel.metadata.create_all`` 在启动时完成，这版迁移负责：
- 记录版本；
- 给已经存在但不完整的库补表（幂等）；
- 补必要索引。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261031_02"
down_revision = "20261031_01"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("person"):
        op.create_table(
            "person",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
            sa.Column("display_name", sa.String(64), nullable=False),
            sa.Column("pinyin", sa.String(128), nullable=False, server_default=""),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("merged_into_id", sa.Integer(), sa.ForeignKey("person.id")),
            sa.Column("ambiguous", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
            sa.UniqueConstraint("owner_id", "display_name", name="uq_person_owner_name"),
        )
        op.create_index("ix_person_owner_id", "person", ["owner_id"])
        op.create_index("ix_person_display_name", "person", ["display_name"])
        op.create_index("ix_person_pinyin", "person", ["pinyin"])
        op.create_index("ix_person_status", "person", ["status"])

    if not _has_table("person_alias"):
        op.create_table(
            "person_alias",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "person_id", sa.Integer(), sa.ForeignKey("person.id"), nullable=False
            ),
            sa.Column("alias", sa.String(64), nullable=False),
            sa.Column("source", sa.String(16), nullable=False, server_default="oral"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime()),
            sa.UniqueConstraint("person_id", "alias", name="uq_person_alias"),
        )
        op.create_index("ix_person_alias_person_id", "person_alias", ["person_id"])
        op.create_index("ix_person_alias_alias", "person_alias", ["alias"])

    if not _has_table("person_attribute"):
        op.create_table(
            "person_attribute",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "person_id", sa.Integer(), sa.ForeignKey("person.id"), nullable=False
            ),
            sa.Column("meeting_id", sa.Integer(), sa.ForeignKey("meeting.id")),
            sa.Column("category", sa.String(16), nullable=False),
            sa.Column("key", sa.String(32), nullable=False),
            sa.Column("value", sa.String(128), nullable=False, server_default=""),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("source", sa.String(16), nullable=False, server_default="llm"),
            sa.Column("evidence", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("updated_at", sa.DateTime()),
            sa.UniqueConstraint(
                "person_id", "category", "key", name="uq_person_attribute"
            ),
        )
        op.create_index(
            "ix_person_attribute_person_id", "person_attribute", ["person_id"]
        )
        op.create_index(
            "ix_person_attribute_meeting_id", "person_attribute", ["meeting_id"]
        )

    if not _has_table("meeting_participant"):
        op.create_table(
            "meeting_participant",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "meeting_id", sa.Integer(), sa.ForeignKey("meeting.id"), nullable=False
            ),
            sa.Column(
                "person_id", sa.Integer(), sa.ForeignKey("person.id"), nullable=False
            ),
            sa.Column("speaker_key", sa.String(32)),
            sa.Column("role", sa.String(16), nullable=False, server_default=""),
            sa.Column("importance", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("evidence", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
            sa.UniqueConstraint(
                "meeting_id", "person_id", name="uq_participant_meeting_person"
            ),
            sa.UniqueConstraint(
                "meeting_id", "speaker_key", name="uq_participant_meeting_key"
            ),
        )
        op.create_index(
            "ix_meeting_participant_meeting_id", "meeting_participant", ["meeting_id"]
        )
        op.create_index(
            "ix_meeting_participant_person_id", "meeting_participant", ["person_id"]
        )
        op.create_index(
            "ix_meeting_participant_state", "meeting_participant", ["state"]
        )


def downgrade() -> None:
    for name in (
        "meeting_participant",
        "person_attribute",
        "person_alias",
        "person",
    ):
        if _has_table(name):
            op.drop_table(name)
