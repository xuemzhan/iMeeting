"""Template rebuild progress and per-sample embedding cache."""

import sqlalchemy as sa
from alembic import op

revision = "20261031_05"
down_revision = "20261031_04"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    # Definitions stay frozen here: later model edits must not change what
    # upgrading an older database does.
    if not inspector.has_table("template_sample_embedding"):
        op.create_table(
            "template_sample_embedding",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "meeting_id",
                sa.Integer(),
                sa.ForeignKey("meeting.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("segment_id", sa.String(64), nullable=False),
            sa.Column("audio_signature", sa.String(64), nullable=False, server_default=""),
            sa.Column("dim", sa.Integer(), nullable=False, server_default="192"),
            sa.Column("embedding", sa.LargeBinary(), nullable=False),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
            sa.UniqueConstraint("meeting_id", "segment_id", name="uq_template_sample_segment"),
        )
        op.create_index(
            "ix_template_sample_embedding_meeting_id",
            "template_sample_embedding",
            ["meeting_id"],
        )
        op.create_index(
            "ix_template_sample_embedding_segment_id",
            "template_sample_embedding",
            ["segment_id"],
        )
    if not inspector.has_table("template_rebuild_state"):
        op.create_table(
            "template_rebuild_state",
            sa.Column(
                "meeting_id",
                sa.Integer(),
                sa.ForeignKey("meeting.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("job_id", sa.Integer()),
            sa.Column("stage", sa.String(24), nullable=False, server_default="idle"),
            sa.Column("speakers_total", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("speakers_done", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("samples_done", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("samples_cached", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("samples_extracted", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("templates_created", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("templates_updated", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("note", sa.String(200), nullable=False, server_default=""),
            sa.Column("last_error", sa.String(500), nullable=False, server_default=""),
            sa.Column("started_at", sa.DateTime()),
            sa.Column("finished_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
        )
        op.create_index(
            "ix_template_rebuild_state_job_id", "template_rebuild_state", ["job_id"]
        )
        op.create_index(
            "ix_template_rebuild_state_stage", "template_rebuild_state", ["stage"]
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("template_rebuild_state"):
        op.drop_table("template_rebuild_state")
    if inspector.has_table("template_sample_embedding"):
        op.drop_table("template_sample_embedding")
