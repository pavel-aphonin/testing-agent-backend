"""Central permissions registry — CRUD-style per section.

Every section gets up to four actions: **view · create · edit · delete**.
Not every section uses all four (e.g. the Graph has only ``view``, the
AI assistant has only ``use``), but the UI renders the full CRUD grid
and just disables the irrelevant cells.

Three system roles are seeded by migration and marked ``is_system``.
Custom roles can cherry-pick any subset of ``ALL_PERMISSIONS``.
"""

from __future__ import annotations

from typing import TypedDict


class PermissionMeta(TypedDict):
    ru: str
    en: str


class SectionMeta(TypedDict):
    label_ru: str
    label_en: str
    permissions: dict[str, PermissionMeta]


# ── Section → permission codes ───────────────────────────────────────────────
# The four standard CRUD actions. Some sections omit actions that make no
# sense (you can't "create" a graph, you can't "delete" settings). The
# frontend reads the registry via GET /api/permissions and only renders
# checkboxes for actions that exist.

PERMISSION_SECTIONS: dict[str, SectionMeta] = {
    "runs": {
        "label_ru": "Запуски",
        "label_en": "Runs",
        "permissions": {
            "runs.view":   {"ru": "Просматривать", "en": "View"},
            "runs.create": {"ru": "Создавать", "en": "Create"},
            "runs.edit":   {"ru": "Редактировать", "en": "Edit"},
            "runs.delete": {"ru": "Удалять", "en": "Delete"},
            "runs.cancel": {"ru": "Отменять", "en": "Cancel"},
        },
    },
    "scenarios": {
        "label_ru": "Сценарии",
        "label_en": "Scenarios",
        "permissions": {
            "scenarios.view":   {"ru": "Просматривать", "en": "View"},
            "scenarios.create": {"ru": "Создавать", "en": "Create"},
            "scenarios.edit":   {"ru": "Редактировать", "en": "Edit"},
            "scenarios.delete": {"ru": "Удалять", "en": "Delete"},
        },
    },
    "test_data": {
        "label_ru": "Тестовые данные",
        "label_en": "Test data",
        "permissions": {
            "test_data.view":   {"ru": "Просматривать", "en": "View"},
            "test_data.create": {"ru": "Создавать", "en": "Create"},
            "test_data.edit":   {"ru": "Редактировать", "en": "Edit"},
            "test_data.delete": {"ru": "Удалять", "en": "Delete"},
        },
    },
    "defects": {
        "label_ru": "Дефекты",
        "label_en": "Defects",
        "permissions": {
            "defects.view":   {"ru": "Просматривать", "en": "View"},
            "defects.create": {"ru": "Создавать", "en": "Create"},
            "defects.edit":   {"ru": "Редактировать", "en": "Edit"},
            "defects.delete": {"ru": "Удалять", "en": "Delete"},
        },
    },
    "graph": {
        "label_ru": "Граф",
        "label_en": "Graph",
        "permissions": {
            "graph.view": {"ru": "Просматривать", "en": "View"},
        },
    },
    "knowledge": {
        "label_ru": "База знаний",
        "label_en": "Knowledge base",
        "permissions": {
            "knowledge.view":   {"ru": "Просматривать", "en": "View"},
            "knowledge.create": {"ru": "Создавать", "en": "Create"},
            "knowledge.edit":   {"ru": "Редактировать", "en": "Edit"},
            "knowledge.delete": {"ru": "Удалять", "en": "Delete"},
            "knowledge.reembed": {"ru": "Переиндексировать", "en": "Re-embed"},
        },
    },
    "models": {
        "label_ru": "Модели LLM",
        "label_en": "LLM models",
        "permissions": {
            "models.view":     {"ru": "Просматривать", "en": "View"},
            "models.create":   {"ru": "Создавать", "en": "Create"},
            "models.edit":     {"ru": "Редактировать", "en": "Edit"},
            "models.delete":   {"ru": "Удалять", "en": "Delete"},
            "models.download": {"ru": "Скачивать с HF", "en": "Download from HF"},
        },
    },
    "devices": {
        "label_ru": "Устройства",
        "label_en": "Devices",
        "permissions": {
            "devices.view":   {"ru": "Просматривать", "en": "View"},
            "devices.create": {"ru": "Создавать", "en": "Create"},
            "devices.edit":   {"ru": "Редактировать", "en": "Edit"},
            "devices.delete": {"ru": "Удалять", "en": "Delete"},
        },
    },
    "users": {
        "label_ru": "Пользователи",
        "label_en": "Users",
        "permissions": {
            "users.view":   {"ru": "Просматривать", "en": "View"},
            "users.create": {"ru": "Создавать", "en": "Create"},
            "users.edit":   {"ru": "Редактировать", "en": "Edit"},
            "users.delete": {"ru": "Удалять", "en": "Delete"},
        },
    },
    "settings": {
        "label_ru": "Настройки",
        "label_en": "Settings",
        "permissions": {
            "settings.view": {"ru": "Просматривать", "en": "View"},
            "settings.edit": {"ru": "Редактировать", "en": "Edit"},
        },
    },
    "dictionaries": {
        "label_ru": "Справочники",
        "label_en": "Dictionaries",
        "permissions": {
            "dictionaries.view":   {"ru": "Просматривать", "en": "View"},
            "dictionaries.create": {"ru": "Создавать", "en": "Create"},
            "dictionaries.edit":   {"ru": "Редактировать", "en": "Edit"},
            "dictionaries.delete": {"ru": "Удалять", "en": "Delete"},
        },
    },
    "assistant": {
        "label_ru": "AI-ассистент",
        "label_en": "AI assistant",
        "permissions": {
            "assistant.use": {"ru": "Использовать", "en": "Use"},
        },
    },
}

# Flat set for fast membership checks in guards.
ALL_PERMISSIONS: frozenset[str] = frozenset(
    perm
    for section in PERMISSION_SECTIONS.values()
    for perm in section["permissions"]
)


# ── System role definitions ──────────────────────────────────────────────────

SYSTEM_ROLES: list[dict] = [
    {
        "code": "viewer",
        "name": "Наблюдатель",
        "description": "Только просмотр запусков и результатов",
        "permissions": [
            "runs.view",
            "defects.view",
            "graph.view",
            "settings.view",
        ],
    },
    {
        "code": "tester",
        "name": "Тестировщик",
        "description": "Создание и запуск тестов, управление сценариями и данными",
        "permissions": [
            "runs.view", "runs.create", "runs.edit", "runs.cancel",
            "scenarios.view", "scenarios.create", "scenarios.edit", "scenarios.delete",
            "test_data.view", "test_data.create", "test_data.edit", "test_data.delete",
            "defects.view",
            "graph.view",
            "knowledge.view",
            "devices.view",
            "models.view",
            "settings.view", "settings.edit",
            "assistant.use",
        ],
    },
    {
        "code": "admin",
        "name": "Администратор",
        "description": "Полный доступ ко всем разделам и настройкам",
        "permissions": sorted(ALL_PERMISSIONS),
    },
]
