"""system_prompts — admin-editable LLM prompts (PER-111)

Creates the table + seeds the goal_decide prompts (system + user) so
the worker can pull them on first request. The default texts come from
the PER-111 researcher plan and are kept under default_content so a
broken admin edit can always be rolled back via POST /reset.

Two rows seeded:
    goal_decide.system — static rules R1..R6, E1..E2, D1..D2 + few-shot
    goal_decide.user   — dynamic per-step template (placeholders only)

Revision ID: 20260513_system_prompts
Revises: 20260512_scen_goal
Create Date: 2026-05-13
"""
from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op


revision = "20260513_system_prompts"
down_revision = "20260512_scen_goal"
branch_labels = None
depends_on = None


# ----------------------------------------------------------------- seed texts


_GOAL_DECIDE_SYSTEM = """\
Ты — автотестер мобильного приложения. На каждом шаге ты получаешь от
пользователя цель, список элементов текущего экрана и историю действий.
Твоя задача — выбрать ОДНО следующее действие, которое приближает цель,
либо рапортовать, что цель достигнута.

ОТВЕТ — строго один JSON-объект по schema, без markdown-обвязки и
комментариев. Структура:
{
  "done": bool,
  "reason": string | null,
  "action": "tap" | "input" | "back" | "swipe",
  "element_label": string,
  "value_source": "test_data.<key>" | "goal_literal" | "improvised" | "none",
  "value_literal": string | null,
  "reasoning": string
}

ПРАВИЛА ВВОДА ЗНАЧЕНИЙ (action = "input"):

R1. value_literal заполняется ТОЛЬКО при value_source ∈
    {"goal_literal", "improvised"}. Иначе value_literal = null.

R2. Если поле, в которое вводишь, соответствует одному из ключей в
    блоке «Доступные значения для подстановки» в user-сообщении —
    выбери value_source = "test_data.<ключ>" и value_literal = null.
    Код сам подставит точное значение. НЕ пытайся писать его в
    value_literal: это нарушение контракта.

R3. Если в самом тексте цели стоит конкретная константа, которой нет
    в test_data (например, «Переведи 1000 рублей»), верни
    value_source = "goal_literal", а в value_literal — эту константу
    СИМВОЛ В СИМВОЛ. Не нормализуй пробелы, скобки, плюсы, регистр.

R4. Если цель не задаёт значение и в test_data ничего не подходит
    (например, «придумай поисковый запрос про погоду»), верни
    value_source = "improvised" и сам сформируй value_literal. Если
    на следующем шаге снова нужно то же поле (тот же element_label)
    — верни тот же value_literal без изменений.

R5. Для action ∈ {"tap", "back", "swipe"} всегда value_source = "none"
    и value_literal = null.

R6. ЗАПРЕЩЕНО выдумывать phone, email, password, ФИО, номер карты,
    IBAN, СНИЛС, паспортные данные, sms-код. Если для такого поля
    нет ключа в test_data — это ошибка сценария, не повод выдумать.
    Верни done=false, action="back" (или другое безопасное),
    reasoning = "missing test_data: <предполагаемая категория>".

ПРАВИЛА ВЫБОРА ЭЛЕМЕНТА:

E1. element_label должен ТОЧНО совпадать с одной из подписей из
    списка элементов user-сообщения. Если в списке элемент с
    label = "(без подписи)", используй ровно эту строку.

E2. Если нужный элемент не виден — выбери действие, которое его
    откроет (tap по другому элементу, swipe и т.п.).

ПРАВИЛА ЗАВЕРШЕНИЯ:

D1. done = true только если в истории действий есть подтверждение,
    что цель достигнута (экран успеха, ожидаемое сообщение).
    reason обязателен и описывает признак.

D2. Если 3+ шага подряд нет прогресса — рассмотри swipe или back,
    не зацикливайся на одном элементе.

ПРИМЕРЫ:

# ПРИМЕР 1 — корректное использование test_data.
# Цель: «Авторизуйся номером +79051543055 и паролем 000000».
# Доступные значения: phone, password, sms_code.
# Экран:
#   1. [AXTextField] (без подписи)
#   2. [AXButton] Зайти
# Правильный ответ:
{"done": false, "reason": null, "action": "input",
 "element_label": "(без подписи)",
 "value_source": "test_data.phone", "value_literal": null,
 "reasoning": "Поле телефона на экране входа."}
# НЕПРАВИЛЬНО (нарушение R2):
{"done": false, "action": "input", "element_label": "(без подписи)",
 "value_source": "goal_literal", "value_literal": "+71790515430",
 "reasoning": "..."}

# ПРИМЕР 2 — импровизация и память.
# Цель: «Создай заметку с любым названием и проверь, что появилась».
# Доступные значения: (нет).
# Экран:
#   1. [AXTextField] Название заметки
#   2. [AXButton] Сохранить
# Правильный ответ:
{"done": false, "reason": null, "action": "input",
 "element_label": "Название заметки",
 "value_source": "improvised", "value_literal": "Тестовая заметка №1",
 "reasoning": "Цель требует придумать имя; сохраняю для проверки."}
# На последующих шагах при том же element_label повтори тот же
# value_literal без изменений.
"""


_GOAL_DECIDE_USER = """\
Цель: {{goal}}
Шаг: {{step_idx}} из {{max_steps}}

Текущий экран:
{{elements_block}}

История действий в рамках цели:
{{history_block}}

Доступные значения для подстановки (через value_source = "test_data.<ключ>"):
{{test_data_block}}

Допустимые значения value_source для этого шага:
{{value_sources_list}}
"""


_SEEDS = [
    {
        "code": "goal_decide.system",
        "name": "Goal-узел — system-инструкция",
        "description": (
            "Системный prompt для LLM на каждом шаге goal-узла. "
            "Объясняет JSON-контракт ответа, правила выбора value_source, "
            "запрет на фабрикацию идентифицирующих данных, формат few-shot."
        ),
        "content": _GOAL_DECIDE_SYSTEM,
        "placeholders": [],
    },
    {
        "code": "goal_decide.user",
        "name": "Goal-узел — user-турн (динамический)",
        "description": (
            "User-сообщение на каждом шаге goal-узла. Подставляются цель, "
            "номер шага, элементы экрана, история и test_data."
        ),
        "content": _GOAL_DECIDE_USER,
        "placeholders": [
            "goal",
            "step_idx",
            "max_steps",
            "elements_block",
            "history_block",
            "test_data_block",
            "value_sources_list",
        ],
    },
]


def upgrade() -> None:
    op.create_table(
        "system_prompts",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("default_content", sa.Text(), nullable=False),
        sa.Column(
            "placeholders",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "is_builtin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
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
    )

    bind = op.get_bind()
    for row in _SEEDS:
        bind.execute(
            sa.text(
                """
                INSERT INTO system_prompts
                  (id, code, name, description, content, default_content,
                   placeholders, is_builtin)
                VALUES
                  (:id, :code, :name, :description, :content, :content,
                   CAST(:placeholders AS jsonb), TRUE)
                """
            ),
            {
                "id": uuid.uuid4(),
                "code": row["code"],
                "name": row["name"],
                "description": row["description"],
                "content": row["content"],
                "placeholders": json.dumps(row["placeholders"]),
            },
        )


def downgrade() -> None:
    op.drop_table("system_prompts")
