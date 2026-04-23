"""System branding — logo + product-name customization.

Singleton table. One row, seeded empty on first start; fields left NULL
make the frontend fall back to the built-in Markov branding.

Revision ID: 20260423_branding
Revises: 20260423_app_audit
Create Date: 2026-04-23
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "20260423_branding"
down_revision = "20260423_app_audit"
branch_labels = None
depends_on = None


_SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-0000000b4a47")


def upgrade() -> None:
    op.create_table(
        "system_branding",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("product_name", sa.String(80), nullable=True),
        sa.Column("short_name", sa.String(40), nullable=True),
        sa.Column("logo_path", sa.Text, nullable=True),
        sa.Column("logo_back_path", sa.Text, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Seed the singleton row up-front so the API never has to upsert.
    op.execute(
        sa.text(
            "INSERT INTO system_branding (id) VALUES (cast(:id as uuid))"
        ).bindparams(id=str(_SINGLETON_ID))
    )


def downgrade() -> None:
    op.drop_table("system_branding")
