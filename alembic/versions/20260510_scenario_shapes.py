"""scenario_shapes — admin-extensible palette of node types (PER-90)

Creates the table + seeds the original nine built-in shapes that
match the existing NodeType literal values, so old scenarios keep
resolving to the same renderer / runtime behaviour.

Revision ID: 20260510_scen_shapes
Revises: 20260510_scen_graph
Create Date: 2026-05-10
"""
from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260510_scen_shapes"
down_revision = "20260510_scen_graph"
branch_labels = None
depends_on = None


_BUILTINS = [
    {
        "code": "start",
        "name": "Начало",
        "description": "Точка входа в сценарий. Должна быть одна.",
        "category": "start",
        "geometry": "circle",
        "color": "#52c41a",
        "icon": "PlayCircleOutlined",
        "action_code": None,
        "attributes": [],
        "sort_order": 0,
    },
    {
        "code": "end",
        "name": "Конец",
        "description": "Точка выхода. Может быть несколько.",
        "category": "end",
        "geometry": "circle",
        "color": "#ff4d4f",
        "icon": "PoweroffOutlined",
        "action_code": None,
        "attributes": [],
        "sort_order": 1,
    },
    {
        "code": "action",
        "name": "Действие",
        "description": "Тап / ввод / свайп / проверка элемента на текущем экране.",
        "category": "action",
        "geometry": "rect",
        "color": "#1677ff",
        "icon": "ThunderboltOutlined",
        "action_code": "tap",
        "attributes": [
            {"key": "action", "label": "Тип действия", "type": "action_verb", "required": True, "default": "tap"},
            {"key": "element_label", "label": "Элемент", "type": "string", "required": True, "supports_dict": "ui_elements"},
            {"key": "value", "label": "Значение", "type": "string", "supports_vars": True},
            {"key": "expected_result", "label": "Ожидаемый результат", "type": "string", "multiline": True},
            {"key": "screen_description", "label": "Описание экрана", "type": "string", "multiline": True},
        ],
        "sort_order": 2,
    },
    {
        "code": "decision",
        "name": "Условие",
        "description": "Ветвление по выражению на исходящих стрелках.",
        "category": "decision",
        "geometry": "diamond",
        "color": "#faad14",
        "icon": "BranchesOutlined",
        "action_code": None,
        "attributes": [
            {"key": "label", "label": "Подпись узла", "type": "string"},
        ],
        "sort_order": 3,
    },
    {
        "code": "wait",
        "name": "Пауза",
        "description": "Ждать заданное число миллисекунд.",
        "category": "wait",
        "geometry": "pill",
        "color": "#1677ff",
        "icon": "ClockCircleOutlined",
        "action_code": None,
        "attributes": [
            {"key": "ms", "label": "Длительность (мс)", "type": "number", "required": True, "default": 1000},
        ],
        "sort_order": 4,
    },
    {
        "code": "screen_check",
        "name": "Проверить экран",
        "description": "Сверить текущий экран с описанием через ИИ.",
        "category": "screen_check",
        "geometry": "trapezoid",
        "color": "#1677ff",
        "icon": "EyeOutlined",
        "action_code": None,
        "attributes": [
            {"key": "screen_description", "label": "Описание экрана", "type": "string", "required": True, "multiline": True},
        ],
        "sort_order": 5,
    },
    {
        "code": "sub_scenario",
        "name": "Связанный сценарий",
        "description": "Запустить другой сценарий и вернуться сюда.",
        "category": "sub_scenario",
        "geometry": "hexagon",
        "color": "#722ed1",
        "icon": "LinkOutlined",
        "action_code": None,
        "attributes": [
            {"key": "linked_scenario_id", "label": "Сценарий", "type": "scenario_link", "required": True},
        ],
        "sort_order": 6,
    },
    {
        "code": "loop_back",
        "name": "Возврат",
        "description": "«Вернуться в начало цикла». Используется в паре с back-edge.",
        "category": "loop_back",
        "geometry": "rect",
        "color": "#faad14",
        "icon": "ArrowLeftOutlined",
        "action_code": None,
        "attributes": [
            {"key": "max_iterations", "label": "Максимум итераций", "type": "number", "default": 10},
        ],
        "sort_order": 7,
    },
    {
        "code": "group",
        "name": "Группа",
        "description": "Визуальный контейнер. Перетащите внутрь узлы, чтобы объединить смысловой блок.",
        "category": "group",
        "geometry": "container",
        "color": "#8c8c8c",
        "icon": "BlockOutlined",
        "action_code": None,
        "attributes": [
            {"key": "label", "label": "Название группы", "type": "string"},
        ],
        "sort_order": 8,
    },
]


def upgrade() -> None:
    op.create_table(
        "scenario_shapes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("geometry", sa.String(32), nullable=False),
        sa.Column("color", sa.String(16), nullable=False, server_default="#1677ff"),
        sa.Column("icon", sa.String(64), nullable=True),
        sa.Column("action_code", sa.String(64), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
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
            nullable=True,
        ),
    )

    bind = op.get_bind()
    for shape in _BUILTINS:
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
                "code": shape["code"],
                "name": shape["name"],
                "description": shape["description"],
                "category": shape["category"],
                "geometry": shape["geometry"],
                "color": shape["color"],
                "icon": shape["icon"],
                "action_code": shape["action_code"],
                "attributes": json.dumps(shape["attributes"]),
                "is_builtin": True,
                "sort_order": shape["sort_order"],
            },
        )


def downgrade() -> None:
    op.drop_table("scenario_shapes")
