"""多场景声纹模板：voiceprint_template / voiceprint_conflict。

``UNIQUE(voiceprint_id, slot)`` 是「每人 ≤5 条」的库层硬约束：slot 取 0..4，
替换模板时先让旧行退役（slot 取负），再写回同一 slot。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261031_03"
down_revision = "20261031_02"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("voiceprint_template"):
        op.create_table(
            "voiceprint_template",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "voiceprint_id",
                sa.Integer(),
                sa.ForeignKey("voiceprint.id"),
                nullable=False,
            ),
            sa.Column("slot", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("embedding", sa.LargeBinary(), nullable=False),
            sa.Column("dim", sa.Integer(), nullable=False, server_default="192"),
            sa.Column("scenario_device", sa.String(16), server_default=""),
            sa.Column("scenario_distance", sa.String(16), server_default=""),
            sa.Column("scenario_noise", sa.String(16), server_default=""),
            sa.Column("scenario_voice", sa.String(16), server_default=""),
            sa.Column("quality", sa.Float(), nullable=False, server_default="0"),
            sa.Column("sample_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("snr_db", sa.Float(), nullable=False, server_default="0"),
            sa.Column("source_meeting_id", sa.Integer()),
            sa.Column("source_segment_id", sa.String(64)),
            sa.Column(
                "confirmed", sa.Boolean(), nullable=False, server_default=sa.text("0")
            ),
            sa.Column("confirmed_by", sa.Integer()),
            sa.Column("confirmed_at", sa.DateTime()),
            sa.Column("superseded_by", sa.Integer()),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
            sa.UniqueConstraint(
                "voiceprint_id", "slot", name="uq_voiceprint_template_slot"
            ),
        )
        op.create_index(
            "ix_voiceprint_template_voiceprint_id", "voiceprint_template", ["voiceprint_id"]
        )
        op.create_index("ix_voiceprint_template_slot", "voiceprint_template", ["slot"])
        op.create_index(
            "ix_voiceprint_template_confirmed", "voiceprint_template", ["confirmed"]
        )
        op.create_index(
            "ix_voiceprint_template_source_meeting_id",
            "voiceprint_template",
            ["source_meeting_id"],
        )

    if not _has_table("voiceprint_conflict"):
        op.create_table(
            "voiceprint_conflict",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "voiceprint_id",
                sa.Integer(),
                sa.ForeignKey("voiceprint.id"),
                nullable=False,
            ),
            sa.Column("meeting_id", sa.Integer()),
            sa.Column("segment_id", sa.String(64)),
            sa.Column("sample_embedding", sa.LargeBinary(), nullable=False),
            sa.Column("self_score", sa.Float()),
            sa.Column("other_voiceprint_id", sa.Integer()),
            sa.Column("other_score", sa.Float()),
            sa.Column("reason", sa.String(16), server_default=""),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("decided_by", sa.Integer()),
            sa.Column("decided_at", sa.DateTime()),
            sa.Column("created_at", sa.DateTime()),
        )
        op.create_index(
            "ix_voiceprint_conflict_voiceprint_id", "voiceprint_conflict", ["voiceprint_id"]
        )
        op.create_index("ix_voiceprint_conflict_status", "voiceprint_conflict", ["status"])
        op.create_index(
            "ix_voiceprint_conflict_other_voiceprint_id",
            "voiceprint_conflict",
            ["other_voiceprint_id"],
        )


def downgrade() -> None:
    for name in ("voiceprint_conflict", "voiceprint_template"):
        if _has_table(name):
            op.drop_table(name)
