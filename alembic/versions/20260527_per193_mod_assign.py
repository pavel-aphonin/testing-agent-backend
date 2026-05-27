"""PER-193 — module_assignments table + LLMModel.supported_roles.

Schema for the 12-module pipeline's «role → model» map. See
``app/models/module_assignment.py`` for the design rationale.

Three changes in one revision:

1. ``llm_models.supported_roles`` — text[] declaring which roles
   this model is eligible to fill. Empty array = legacy (any role).
2. ``module_assignments`` — 11-row table, one per ``ModuleRole``,
   with the current FK to ``llm_models``. Seeded with all
   ``llm_model_id=NULL`` so the admin UI shows "no model picked"
   for every role on a fresh DB.
3. Unique constraint on ``role`` — one-row-per-role invariant.

Revision ID: 20260527_per193_mod_assign
Revises: 20260527_per192_drop_seed
Create Date: 2026-05-27
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260527_per193_mod_assign"
down_revision = "20260527_per192_drop_seed"
branch_labels = None
depends_on = None


# Keep in sync with app/models/module_assignment.py:ModuleRole.
# Listed here too so the migration is self-contained (Alembic
# shouldn't import application enums — they may evolve faster than
# the schema).
_ROLES: tuple[str, ...] = (
    "SCREEN_PARSER",
    "DYNAMIC_PERCEIVER",
    "CONTEXT_IDENTIFIER",
    "PLANNER",
    "GROUNDER",
    "GROUNDING_VERIFIER",
    "MEMORY",
    "REFLECTION",
    "SAFETY_GUARD",
    "REWARD_CRITIC",
    "AMBIGUITY_RESOLVER",
)


def upgrade() -> None:
    # 1. LLMModel.supported_roles — text[] default empty
    op.add_column(
        "llm_models",
        sa.Column(
            "supported_roles",
            postgresql.ARRAY(sa.String(length=50)),
            nullable=False,
            server_default="{}",
        ),
    )

    # 2. module_assignments table
    op.create_table(
        "module_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column(
            "llm_model_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("llm_models.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "assigned_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("role", name="uq_module_assignments_role"),
    )

    # 3. Seed 11 unassigned rows. UUIDs generated in Python via
    # ``uuid.uuid4()`` rather than Postgres ``gen_random_uuid()`` —
    # we don't enable pgcrypto in the bootstrap (only ``vector`` and
    # ``plpgsql``), and adding ``CREATE EXTENSION pgcrypto`` here
    # would require superuser at migration time which we can't
    # assume in managed-Postgres environments. Doing the seed
    # inline (not in app/seed.py) keeps the schema migration
    # self-contained: a fresh DB has all 11 rows after a single
    # ``alembic upgrade head``.
    for role in _ROLES:
        # Cast :id to uuid explicitly — asyncpg can't infer the type
        # from a bound parameter when the column is uuid and the
        # Python value is str. Alternative would be passing a real
        # UUID object via SQLAlchemy's ORM session, but raw text+cast
        # keeps the migration file standalone (no model imports).
        op.execute(
            sa.text(
                "INSERT INTO module_assignments (id, role, llm_model_id) "
                "VALUES (CAST(:id AS uuid), :role, NULL)"
            ).bindparams(
                sa.bindparam("id", value=str(uuid.uuid4())),
                sa.bindparam("role", value=role),
            )
        )


def downgrade() -> None:
    op.drop_table("module_assignments")
    op.drop_column("llm_models", "supported_roles")
