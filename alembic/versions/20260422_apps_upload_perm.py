"""Grant apps.upload / apps.moderate to the admin role.

New permissions introduced for the extension store:
  - apps.upload   — upload a ZIP bundle to the catalog
  - apps.moderate — approve/reject uploaded bundles

Admin is the only system role that receives them by default. Tester and
Viewer roles are left alone; give them explicitly if desired.

``User.permissions`` is a runtime property that reads ``Role.permissions``,
so updating the role's JSONB is enough — no user-table backfill needed.

Revision ID: 20260422_apps_perm
Revises: 20260422_cover
Create Date: 2026-04-22
"""

from alembic import op
from sqlalchemy import text

revision = "20260422_apps_perm"
down_revision = "20260422_cover"
branch_labels = None
depends_on = None


_NEW_PERMS = ["apps.upload", "apps.moderate"]


def upgrade() -> None:
    conn = op.get_bind()
    # Append each new perm if it's not already in the admin role's list.
    # We dedupe through DISTINCT to stay idempotent if this runs twice.
    for perm in _NEW_PERMS:
        conn.execute(
            text(
                """
                UPDATE roles
                SET permissions = (
                    SELECT jsonb_agg(DISTINCT p)
                    FROM jsonb_array_elements_text(permissions || to_jsonb(cast(:perm as text))) AS p
                )
                WHERE code = 'admin'
                """
            ),
            {"perm": perm},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for perm in _NEW_PERMS:
        conn.execute(
            text(
                """
                UPDATE roles
                SET permissions = coalesce((
                    SELECT jsonb_agg(p)
                    FROM jsonb_array_elements_text(permissions) AS p
                    WHERE p <> cast(:perm as text)
                ), '[]'::jsonb)
                WHERE code = 'admin'
                """
            ),
            {"perm": perm},
        )
