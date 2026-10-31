"""Template lifecycle, source contributions and persistent person extraction state."""

import sqlalchemy as sa
from alembic import op

revision = "20261031_04"
down_revision = "20261031_03"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {c["name"] for c in inspector.get_columns("voiceprint_template")}
    if "disabled" not in columns:
        op.add_column("voiceprint_template", sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    if "ix_voiceprint_template_disabled" not in {index["name"] for index in sa.inspect(connection).get_indexes("voiceprint_template")}:
        op.create_index("ix_voiceprint_template_disabled", "voiceprint_template", ["disabled"])
    # Retired history is intentionally allowed to have negative slots. Refuse
    # ambiguous legacy active data rather than deleting it during migration.
    if connection.execute(sa.text("SELECT count(*) FROM voiceprint_template WHERE slot > 4")).scalar():
        raise RuntimeError("存在超出五槽范围的旧模板，请先备份并人工整理后重试迁移")
    # Triggers protect existing SQLite tables without a foreign-key-sensitive
    # table rebuild; fresh tables also have the model CHECK constraint.
    for action in ("INSERT", "UPDATE OF slot"):
        suffix = "insert" if action == "INSERT" else "update"
        op.execute(sa.text(f"CREATE TRIGGER IF NOT EXISTS template_slot_{suffix} BEFORE {action} ON voiceprint_template WHEN NEW.slot > 4 BEGIN SELECT RAISE(ABORT, 'template slot must be <= 4'); END"))
    # Freeze migration definitions: future application model changes must not
    # alter what upgrading an older database does.
    if not sa.inspect(connection).has_table("person_extraction_state"):
        op.create_table("person_extraction_state",
            sa.Column("meeting_id", sa.Integer(), sa.ForeignKey("meeting.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("manual_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("auto_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_manual_at", sa.DateTime()),
            sa.Column("last_auto_at", sa.DateTime()),
            sa.Column("cursor_id", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lease_token", sa.String(64)),
            sa.Column("lease_until", sa.DateTime()))
    if not sa.inspect(connection).has_table("voiceprint_template_source"):
        op.create_table("voiceprint_template_source",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("voiceprint_id", sa.Integer(), sa.ForeignKey("voiceprint.id"), nullable=False),
            sa.Column("template_id", sa.Integer(), sa.ForeignKey("voiceprint_template.id"), nullable=False),
            sa.Column("source_key", sa.String(200), nullable=False),
            sa.Column("embedding", sa.LargeBinary(), nullable=False),
            sa.Column("weight", sa.Float(), nullable=False, server_default="1"),
            sa.Column("sample_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.UniqueConstraint("voiceprint_id", "source_key", name="uq_template_source"))
        op.create_index("ix_voiceprint_template_source_voiceprint_id", "voiceprint_template_source", ["voiceprint_id"])
        op.create_index("ix_voiceprint_template_source_template_id", "voiceprint_template_source", ["template_id"])


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS template_slot_insert")
    op.execute("DROP TRIGGER IF EXISTS template_slot_update")
    op.drop_table("person_extraction_state")
    op.drop_table("voiceprint_template_source")
    if "ix_voiceprint_template_disabled" in {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("voiceprint_template")}:
        op.drop_index("ix_voiceprint_template_disabled", table_name="voiceprint_template")
    op.drop_column("voiceprint_template", "disabled")
