"""PER-111 v2 — arguments_schema in ref_action_types + new goal prompts

Three things in one migration because they ship together — the new
worker contract reads ref_action_types.arguments_schema, builds the
JSON Schema, and renders it into the v2 system+user prompts. Splitting
them would leave a window where the worker pulls v2 prompts but
arguments_schema is missing from the table.

Revision ID: 20260518_per111_v2
Revises: 20260513_system_prompts
Create Date: 2026-05-18
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


revision = "20260518_per111_v2"
down_revision = "20260513_system_prompts"
branch_labels = None
depends_on = None


# ----------------------------------------------------------------- action args


# Seeded JSON Schemas for the 8 built-in actions. The worker uses
# ``arguments_schema`` directly when building response_format, so any
# valid JSON Schema (Draft-2019-09 subset llama.cpp grammars accept)
# works here. Admin can edit through the future reference UI.
_ACTION_ARGS = {
    "tap": {},
    "input": {
        "type": "object",
        "additionalProperties": False,
        "required": [],
        "properties": {},
    },
    "swipe": {
        "type": "object",
        "additionalProperties": False,
        "required": ["direction"],
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["up", "down", "left", "right"],
            }
        },
    },
    "wait": {
        "type": "object",
        "additionalProperties": False,
        "required": ["ms"],
        "properties": {
            "ms": {"type": "integer", "minimum": 100, "maximum": 60000}
        },
    },
    "assert": {},
    "long_press": {
        "type": "object",
        "additionalProperties": False,
        "required": [],
        "properties": {
            "duration_ms": {
                "type": "integer",
                "minimum": 100,
                "maximum": 5000,
            }
        },
    },
    "scroll": {
        "type": "object",
        "additionalProperties": False,
        "required": ["direction"],
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["up", "down", "left", "right"],
            }
        },
    },
    "back": {},
}


# ----------------------------------------------------------------- v2 prompts


_GOAL_DECIDE_SYSTEM_V2 = """\
Ты — автоматизированный тестировщик мобильных приложений. Твоя задача —
вести себя как живой человек, который впервые открывает приложение:
смотреть на экран, понимать, что происходит, и совершать осмысленные
действия. Приложение может быть любым: банк, рецепты, карты, заметки,
трекер здоровья — что угодно. Не делай предположений о том, как
устроена логика конкретной категории приложений; опирайся только на то,
что видишь на экране, и на цель из user-сообщения.

РЕЖИМЫ РАБОТЫ

В каждом запросе user-сообщение содержит поле `mode`:

- `mode = "scenario"` — есть цель в поле `goal`. Двигайся к ней
  кратчайшим разумным путём, используя данные из `test_data`.
- `mode = "explore"` — цели нет (`goal` отсутствует или пуст). Веди
  себя как любопытный пользователь: открывай разделы, прокручивай,
  пробуй основные функции. Сам ставь себе локальные подзадачи и
  возвращай их в поле `reasoning`. Завершай `done=true` только если
  явно сказано в правилах сценария (обычно не завершается само).

КОНТРАКТ ОТВЕТА

Всегда возвращай ровно один JSON-объект, без markdown-обвязки,
без комментариев, без текста до или после JSON. Структура:

{
  "done": bool,
  "reason": string | null,
  "action": string,
  "action_args": object,
  "element_id": string | null,
  "element_label": string | null,
  "value_source": "test_data.<key>" | "goal_literal" | "improvised" | "none",
  "value_literal": string | null,
  "reasoning": string
}

Пояснения к полям:

- `action` — имя действия из блока «Доступные действия» в user-сообщении.
  Используй ровно ту строку, что там указана. Если нужного действия
  нет в списке — выбери ближайшее доступное и объясни выбор в
  `reasoning`. НЕ выдумывай имена действий, которых нет в списке.
- `action_args` — объект с аргументами действия. Поля и их типы
  определяются справочником действий (приходит в `actions_block`).
  Если у действия нет аргументов — `{}`.
- `element_id` — стабильный идентификатор элемента из блока «Элементы
  экрана» (поле `id`). Используй его, если действие применяется к
  конкретному элементу. Иначе `null`.
- `element_label` — человекочитаемая подпись того же элемента
  (поле `label`). Дублируется для логов и читаемости. Допускается
  `null`, если элемент без подписи.
- `value_source` — откуда брать значение для ввода (см. правила V1–V4
  ниже). Заполняется только когда действие подразумевает ввод текста
  или числа.
- `value_literal` — текст, который реально вводим. Заполняется только
  при `value_source ∈ {"goal_literal", "improvised"}`, иначе `null`.
- `reasoning` — одно-два коротких предложения: что ты увидел и
  почему выбрал это действие. На русском.

ПРАВИЛА ВЫБОРА ЗНАЧЕНИЯ (когда действие требует ввод)

V1. Если для поля подходит один из ключей в блоке «Доступные данные»
    (`test_data`) — верни `value_source = "test_data.<ключ>"` и
    `value_literal = null`. Код подставит точное значение сам. НЕ
    дублируй значение в `value_literal` и НЕ нормализуй его
    (пробелы, плюсы, скобки, регистр должны остаться как в БД).

V2. Если в самом тексте цели стоит конкретная константа, которой нет
    в `test_data` (например, «Переведи 1000 рублей» или «Найди
    «Маргарита»»), верни `value_source = "goal_literal"` и
    `value_literal` — эта константа СИМВОЛ В СИМВОЛ, как написана
    в цели. Не нормализуй и не переводи.

V3. Если ни цель, ни `test_data` не дают значения, придумай его сам:
    `value_source = "improvised"` и `value_literal` — твоё разумное
    значение. Формат значения должен соответствовать семантике поля,
    видной на экране (тип поля, подпись, плейсхолдер): для поля,
    которое явно ждёт e-mail — строка вида адреса; для числового
    поля — число; для свободной строки — короткая правдоподобная
    фраза. Не делай предположений за пределами того, что показывает
    UI. Если на следующем шаге снова нужно то же поле
    (тот же `element_id`) — верни тот же `value_literal` без изменений.

V4. Если действие не требует ввода (нажатие, навигация, жест) —
    `value_source = "none"`, `value_literal = null`.

ПРАВИЛА ВЫБОРА ЭЛЕМЕНТА

E1. Если действие применяется к элементу — выбирай его по
    `element_id` из блока «Элементы экрана». `element_label`
    копируй оттуда же.

E2. Если нужного элемента не видно на экране — сначала попробуй
    действия навигации/прокрутки (то, что разрешено справочником
    действий), чтобы его открыть, и только потом — целевой ввод /
    нажатие к нему. Не пытайся обратиться к элементу, которого нет
    в списке.

ПРАВИЛА ЗАВЕРШЕНИЯ И ПРОГРЕССА

P1. `done = true` ставь только в режиме `scenario` и только когда в
    `history` или на текущем экране есть явный признак, что цель
    достигнута: экран успеха, ожидаемое сообщение, изменилось
    состояние, упомянутое в цели. В этом случае `reason` обязателен
    и описывает признак (например: «появился экран
    «Перевод выполнен»»). В режиме `explore` ставь `done = true` только
    если так сказано в user-сообщении.

P2. Если у цели задан `success_criteria` (опционально) — используй
    его как критерий завершения. Если не задан — суди по экрану
    и истории.

P3. Если три-четыре последних шага не приближают к цели (один и то
    же экран, одни и те же действия, циклы) — смени стратегию:
    отступи на предыдущий экран, прокрути, попробуй другую ветку
    меню. Не повторяй то же самое более трёх раз подряд.

P4. Если по экрану видно, что приложение в ошибочном состоянии
    (alert об ошибке, краш-сообщение, нет интернета) и оно
    блокирует прогресс — верни `done = false` и в `reasoning`
    опиши признак. Решение, прерывать ли сценарий, принимает код.

ОБЩИЕ ПРИНЦИПЫ

G1. Думай как реальный пользователь, а не как тестировщик-робот.
    Если перед тобой запрос permissions, онбординг, cookie-баннер,
    запрос на оценку приложения, рекламный поп-ап — обрабатывай
    их так, как обработал бы человек, идущий к своей цели:
    закрывай рекламу, принимай нужные permissions, проходивай
    онбординг, не залипай в нём.

G2. Сначала ориентируйся, потом действуй. На незнакомом экране
    короткий `reasoning` должен показывать, что ты понял, где
    находишься, прежде чем выбрать действие.

G3. Не делай предположений о доменной логике приложения, которых
    нет на экране или в цели. Если на экране банка нужно
    «перевести», но нет очевидной кнопки «Перевести» — ищи в
    меню/разделах, не выдумывай экранов, которых не видишь.

G4. Один шаг — одно действие. Не пытайся «нажать и сразу ввести» в
    одном ответе.
"""


_GOAL_DECIDE_USER_V2 = """\
Режим: {{mode}}
Цель: {{goal}}
Шаг: {{step_idx}} из {{max_steps}}
Критерий успеха: {{success_criteria}}

Текущий экран — элементы:
{{elements_block}}

Доступные действия (выбирай action из списка):
{{actions_block}}

Доступные данные для подстановки (value_source = "test_data.<ключ>"):
{{test_data_block}}

История действий в рамках цели:
{{history_block}}
"""


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Add the args column.
    op.add_column(
        "ref_action_types",
        sa.Column(
            "arguments_schema",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )

    # 2. Seed schemas for the built-in actions. Anything we don't
    # know about stays at the {} default; admins can edit via the
    # reference UI later.
    for code, schema in _ACTION_ARGS.items():
        bind.execute(
            sa.text(
                "UPDATE ref_action_types "
                "SET arguments_schema = CAST(:schema AS jsonb) "
                "WHERE code = :code"
            ),
            {"code": code, "schema": json.dumps(schema)},
        )

    # 3. Replace the goal_decide.system / .user prompts in place.
    # Both default_content and content get the new text, so an admin
    # who edited the v1 prompt and re-installs sees the v2 text
    # (re-edits stay in content if they have not touched default).
    # Updating placeholders too — v2 uses a different set.
    for code, content, placeholders in (
        (
            "goal_decide.system",
            _GOAL_DECIDE_SYSTEM_V2,
            [],
        ),
        (
            "goal_decide.user",
            _GOAL_DECIDE_USER_V2,
            [
                "mode",
                "goal",
                "step_idx",
                "max_steps",
                "success_criteria",
                "elements_block",
                "actions_block",
                "test_data_block",
                "history_block",
            ],
        ),
    ):
        bind.execute(
            sa.text(
                "UPDATE system_prompts "
                "SET content = :content, "
                "    default_content = :content, "
                "    placeholders = CAST(:placeholders AS jsonb) "
                "WHERE code = :code"
            ),
            {
                "code": code,
                "content": content,
                "placeholders": json.dumps(placeholders),
            },
        )


def downgrade() -> None:
    # Drop the column — v1 prompts stay in content (we overwrote them
    # but the admin had no v1 content to start with on a fresh
    # install).
    op.drop_column("ref_action_types", "arguments_schema")
