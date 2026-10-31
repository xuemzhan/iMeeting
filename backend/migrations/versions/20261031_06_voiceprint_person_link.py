"""Permanent voiceprint -> person link (REQ-001 identity unify).

SQLite cannot add a foreign-key constraint to an existing table, so on upgraded
databases this adds a plain indexed column and relies on the service layer to
keep it consistent (bind / merge / delete). Fresh databases get the model's FK.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261031_06"
down_revision = "20261031_05"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {c["name"] for c in inspector.get_columns("voiceprint")}
    if "person_id" not in columns:
        op.add_column("voiceprint", sa.Column("person_id", sa.Integer(), nullable=True))
    indexes = {i["name"] for i in sa.inspect(connection).get_indexes("voiceprint")}
    if "ix_voiceprint_person_id" not in indexes:
        op.create_index("ix_voiceprint_person_id", "voiceprint", ["person_id"])
    # Only backfill unambiguous links: one active person, same owner, matching key.
    # Ambiguous or cross-owner cases stay NULL for human triage instead of guessing.
    if sa.inspect(connection).has_table("meeting_participant") and sa.inspect(
        connection
    ).has_table("person"):
        op.execute(
            sa.text(
                """
                UPDATE voiceprint
                SET person_id = (
                    SELECT mp.person_id
                    FROM meeting_participant mp
                    JOIN person p ON p.id = mp.person_id
                    WHERE mp.meeting_id = voiceprint.meeting_id
                      AND mp.speaker_key = voiceprint.speaker_key
                      AND p.status = 'active'
                      AND (voiceprint.owner_id IS NULL
                           OR voiceprint.owner_id = p.owner_id)
                    GROUP BY mp.meeting_id, mp.speaker_key
                    HAVING count(*) = 1
                )
                WHERE person_id IS NULL
                  AND meeting_id IS NOT NULL
                  AND speaker_key IS NOT NULL
                """
            )
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    indexes = {i["name"] for i in inspector.get_indexes("voiceprint")}
    if "ix_voiceprint_person_id" in indexes:
        op.drop_index("ix_voiceprint_person_id", table_name="voiceprint")
    if "person_id" in {c["name"] for c in inspector.get_columns("voiceprint")}:
        op.drop_column("voiceprint", "person_id")
