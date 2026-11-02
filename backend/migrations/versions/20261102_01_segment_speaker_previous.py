"""Keep the pre-unbind speaker label on transcript segments.

解绑/改绑一位参会人时，旧 speaker_key 名下的片段原本会被就地改写成「未确认说话人」，
原标注（例如「郭睿」）从此不存在于任何地方，反复改标定会抹掉文本证据
（2026-11-01 声纹机制审查第 5 节第 3 条）。新增 `speaker_previous` 只做记录，不改变
任何判定：解绑后仍然显示「未确认说话人」，但原标注可查、可展示。
"""

import sqlalchemy as sa
from alembic import op

revision = "20261102_01"
down_revision = "20261031_07"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "segment" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("segment")}
    if "speaker_previous" not in columns:
        op.add_column(
            "segment",
            sa.Column("speaker_previous", sa.String(length=32), nullable=False, server_default=""),
        )


def downgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "segment" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("segment")}
    if "speaker_previous" in columns:
        with op.batch_alter_table("segment") as batch:
            batch.drop_column("speaker_previous")
