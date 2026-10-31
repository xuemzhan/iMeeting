"""Primary (pinned) template per voiceprint (REQ-002 FR-5).

At most one pinned template per voiceprint. SQLite enforces it with a partial
unique index; other dialects keep the service-layer rule only, because a plain
unique index on voiceprint_id would forbid the other four templates.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261031_07"
down_revision = "20261031_06"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {c["name"] for c in inspector.get_columns("voiceprint_template")}
    if "pinned" not in columns:
        op.add_column(
            "voiceprint_template",
            sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )
    indexes = {i["name"] for i in sa.inspect(connection).get_indexes("voiceprint_template")}
    if "ix_voiceprint_template_pinned" not in indexes:
        op.create_index("ix_voiceprint_template_pinned", "voiceprint_template", ["pinned"])
    if connection.dialect.name == "sqlite":
        op.execute(
            sa.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_voiceprint_template_pinned "
                "ON voiceprint_template(voiceprint_id) WHERE pinned = 1"
            )
        )


def downgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if connection.dialect.name == "sqlite":
        op.execute(sa.text("DROP INDEX IF EXISTS uq_voiceprint_template_pinned"))
    indexes = {i["name"] for i in inspector.get_indexes("voiceprint_template")}
    if "ix_voiceprint_template_pinned" in indexes:
        op.drop_index("ix_voiceprint_template_pinned", table_name="voiceprint_template")
    if "pinned" in {c["name"] for c in inspector.get_columns("voiceprint_template")}:
        op.drop_column("voiceprint_template", "pinned")
