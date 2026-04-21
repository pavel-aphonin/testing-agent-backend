"""Apps / extensions subsystem.

Creates catalog / version / installation / review tables. No seed — a
Hello World sample is registered by app.seed instead so we don't have
to manage bundle files from a migration.

Revision ID: 20260421_apps
Revises: 20260420_polish
Create Date: 2026-04-21
"""

import sqlalchemy as sa
from alembic import op

revision = "20260421_apps"
down_revision = "20260420_polish"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_packages",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(100), unique=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("category", sa.String(50), nullable=False, server_default="utility"),
        sa.Column("author", sa.String(200), nullable=True),
        sa.Column("logo_path", sa.Text, nullable=True),
        sa.Column("is_public", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "owner_workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("approval_status", sa.String(20), nullable=False, server_default="draft", index=True),
        sa.Column(
            "approved_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "app_package_versions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "app_package_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_packages.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("version", sa.String(30), nullable=False),
        sa.Column("manifest", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column("bundle_path", sa.Text, nullable=False),
        sa.Column("changelog", sa.Text, nullable=True),
        sa.Column("size_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_deprecated", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("app_package_id", "version", name="uq_app_pkg_version"),
    )

    op.create_table(
        "app_installations",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "app_package_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_packages.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "version_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_package_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("settings", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "installed_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("installed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workspace_id", "app_package_id", name="uq_app_install_ws_pkg"),
    )

    op.create_table(
        "app_reviews",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "app_package_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_packages.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("rating", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("app_package_id", "user_id", name="uq_app_review"),
    )


def downgrade() -> None:
    op.drop_table("app_reviews")
    op.drop_table("app_installations")
    op.drop_table("app_package_versions")
    op.drop_table("app_packages")
