"""Track per-speaker review state so confirmed/deleted speakers stay hidden.

`state`: 0 = 未处理（面板显示）、1 = 已确认、2 = 已删除。确认与删除都只影响
参会人面板的显示，不改转写片段本身。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261031_01"
down_revision = "20261029_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Startup also supports pre-Alembic databases via the idempotent migrator.
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("speaker_role")
    }
    if "state" not in columns:
        op.add_column(
            "speaker_role",
            sa.Column("state", sa.Integer(), nullable=False, server_default="0"),
        )
        op.create_index(
            "ix_speaker_role_state", "speaker_role", ["state"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("speaker_role") as batch:
        batch.drop_index("ix_speaker_role_state")
        batch.drop_column("state")
