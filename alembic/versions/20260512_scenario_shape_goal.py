"""scenario_shapes — add "goal" shape (PER-110)

A natural-language node type. The user writes a task like
«Авторизуйся с такими-то данными, сделай перевод 100 рублей на счёт X»
and the worker runs a mini LLM-loop on this node: look at the screen,
pick one action, execute, repeat until the LLM declares the goal done
or ``max_steps`` is reached.

Lets users author scenarios at the level «авторизуйся → переведи →
проверь баланс» (three nodes) instead of «тапни email → введи …
→ тапни password → введи …» (twenty-plus nodes), which is the
useful authoring abstraction for a demo audience.

Revision ID: 20260512_scen_goal
Revises: 20260510_scen_shapes
Create Date: 2026-05-12
"""
from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op


revision = "20260512_scen_goal"
down_revision = "20260510_scen_shapes"
branch_labels = None
depends_on = None


_GOAL_SHAPE = {
    "code": "goal",
    "name": "Цель",
    "description": (
        "Высокоуровневая инструкция на естественном языке. "
        "Агент сам решит как достичь цели, опираясь на текущий экран."
    ),
    "category": "goal",
    "geometry": "rect",
    "color": "#13c2c2",
    "icon": "AimOutlined",
    "action_code": None,
    "attributes": [
        {
            "key": "description",
            "label": "Что должен сделать агент",
            "type": "string",
            "required": True,
            "multiline": True,
            "supports_vars": True,
        },
        {
            "key": "expected_outcome",
            "label": "Признак достижения цели (опционально)",
            "type": "string",
            "multiline": True,
        },
        {
            "key": "max_steps",
            "label": "Лимит шагов",
            "type": "number",
            "default": 15,
        },
    ],
    "sort_order": 9,
}


def upgrade() -> None:
    bind = op.get_bind()
    # Idempotent: if a previous failed migration or admin already
    # inserted the shape we skip the row so the migration is safe to
    # re-run after a rollback.
    exists = bind.execute(
        sa.text("SELECT 1 FROM scenario_shapes WHERE code = :code"),
        {"code": _GOAL_SHAPE["code"]},
    ).first()
    if exists:
        return
    bind.execute(
        sa.text(
            """
            INSERT INTO scenario_shapes
              (id, code, name, description, category, geometry,
               color, icon, action_code, attributes, is_builtin,
               sort_order)
            VALUES
              (:id, :code, :name, :description, :category, :geometry,
               :color, :icon, :action_code,
               CAST(:attributes AS jsonb), :is_builtin, :sort_order)
            """
        ),
        {
            "id": uuid.uuid4(),
            "code": _GOAL_SHAPE["code"],
            "name": _GOAL_SHAPE["name"],
            "description": _GOAL_SHAPE["description"],
            "category": _GOAL_SHAPE["category"],
            "geometry": _GOAL_SHAPE["geometry"],
            "color": _GOAL_SHAPE["color"],
            "icon": _GOAL_SHAPE["icon"],
            "action_code": _GOAL_SHAPE["action_code"],
            "attributes": json.dumps(_GOAL_SHAPE["attributes"]),
            "is_builtin": True,
            "sort_order": _GOAL_SHAPE["sort_order"],
        },
    )


def downgrade() -> None:
    op.execute("DELETE FROM scenario_shapes WHERE code = 'goal'")
