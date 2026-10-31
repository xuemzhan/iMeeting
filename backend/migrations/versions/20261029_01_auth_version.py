"""Version login tokens so password changes revoke previous credentials."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261029_01"
down_revision = "20261017_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Startup also supports pre-Alembic databases via the idempotent migrator.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("user")}
    if "auth_version" not in columns:
        op.add_column("user", sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.drop_column("auth_version")
