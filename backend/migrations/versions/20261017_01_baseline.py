"""Establish the production migration baseline.

Revision ID: 20261017_01
Revises:
Create Date: 2026-10-17

The application previously created tables and incremental columns idempotently
at startup. This baseline records that schema in Alembic so later changes are
versioned and rollbackable.
"""

from __future__ import annotations

revision = "20261017_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
