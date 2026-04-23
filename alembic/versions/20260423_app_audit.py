"""Audit log for app installations + help articles & feedback + app_events flag.

Adds three independent but related pieces:

1. ``app_installation_audit`` — who installed/upgraded/uninstalled what,
   shown under the «История» tab of the workspace apps page.

2. ``help_articles`` and ``help_article_views`` — store of knowledge base
   articles rendered on the Справка page (Apple/Google-help style). Views
   are tracked per (article, day) so we can show truly-popular articles
   based on the last week/month.

3. ``feedback_tickets`` — user submissions from the help page's feedback
   form. Admin view shows the inbox; later a scheduled app can sync to
   Jira.

Revision ID: 20260423_app_audit
Revises: 20260422_app_cats
Create Date: 2026-04-23
"""

import sqlalchemy as sa
from alembic import op

revision = "20260423_app_audit"
down_revision = "20260422_app_cats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. App installation audit log ────────────────────────────────
    op.create_table(
        "app_installation_audit",
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
            sa.ForeignKey("app_packages.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "installation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            # intentionally no FK — keep history rows after uninstall
            nullable=True,
            index=True,
        ),
        sa.Column("package_name", sa.String(200), nullable=True),
        sa.Column("action", sa.String(30), nullable=False, index=True),
        sa.Column("from_version", sa.String(30), nullable=True),
        sa.Column("to_version", sa.String(30), nullable=True),
        sa.Column("details", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("user_email", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
    )

    # ── 2. Help articles ─────────────────────────────────────────────
    op.create_table(
        "help_articles",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("section", sa.String(50), nullable=False, index=True),
        # Full markdown body; rendered on the frontend with react-markdown.
        sa.Column("body_md", sa.Text, nullable=False),
        # Short lead shown in search results & the "Популярные" list.
        sa.Column("excerpt", sa.Text, nullable=True),
        # Sort within a section + a cached 28-day view count used to rank
        # the "popular" list without a JOIN every time.
        sa.Column("sort_order", sa.Integer, server_default="0", nullable=False),
        sa.Column("views_28d", sa.Integer, server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_table(
        "help_article_views",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "article_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("help_articles.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "viewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
    )

    # ── 3. Feedback inbox ────────────────────────────────────────────
    op.create_table(
        "feedback_tickets",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("user_email", sa.Text, nullable=True),
        # bug | question | proposal | other
        sa.Column("kind", sa.String(30), nullable=False, index=True),
        sa.Column("subject", sa.String(300), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        # Free-form: URL the user was on, article slug they came from, etc.
        sa.Column("context", sa.dialects.postgresql.JSONB, nullable=True),
        # new | in_progress | closed
        sa.Column(
            "status",
            sa.String(20),
            server_default="new",
            nullable=False,
            index=True,
        ),
        # Set once we sync it to Jira / other trackers.
        sa.Column("external_id", sa.String(50), nullable=True),
        sa.Column("admin_notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("feedback_tickets")
    op.drop_table("help_article_views")
    op.drop_table("help_articles")
    op.drop_table("app_installation_audit")
