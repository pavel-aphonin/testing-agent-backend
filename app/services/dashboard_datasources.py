"""Curated server-side data sources for dashboard widgets.

Every data source has a short code (``runs.by_status``) and a handler
that returns a normalized payload:

    {
      "categories": [...],   # x-axis labels, or pie/donut slice labels
      "series": [            # one or more series — Apex accepts an
        {"name": "Runs",     # array with the "numeric values" shape
         "data": [10, 3, 1]} # for pie/donut (flat numbers) too.
      ],
      "is_tabular": bool,    # if true, frontend renders a Table
      "columns": [...],      # for tabular sources
      "rows": [[...], ...]
    }

Keeping this as a static registry (dict of ``code → async fn``) rather
than a plug-in system is deliberate: data source authors write SQL
that runs inside the application DB, so they can't be sandboxed from
a browser. Widget *presentation* is pluggable via ``widget_type`` and
``chart_options``; *queries* are curated.

Entity prefixes (``runs.*``, ``defects.*``, ``screens.*`` …) double
as group labels in the frontend's datasource dropdown — see
``list_datasource_metadata`` for the grouping rules.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import Run

DatasourcePayload = dict[str, Any]
DatasourceHandler = Callable[
    [AsyncSession, UUID, dict[str, Any]], Awaitable[DatasourcePayload]
]


# ── Registry metadata ────────────────────────────────────────────────────────
# Each entry: code / name / description / kind ("categorical" | "timeseries"
# | "tabular") / group key ("runs" | "defects" | …) / optional params.

_METADATA: list[dict[str, Any]] = [
    # ── Runs ─────────────────────────────────────────────────────────
    {"code": "runs.by_status",           "group": "runs",
     "name": "Запуски по статусам",
     "description": "Количество запусков, сгруппированных по текущему статусу.",
     "kind": "categorical"},
    {"code": "runs.by_mode",             "group": "runs",
     "name": "Запуски по режиму агента",
     "description": "AI / MC / Hybrid — сколько в каждом режиме.",
     "kind": "categorical"},
    {"code": "runs.by_platform",         "group": "runs",
     "name": "Запуски по платформе",
     "description": "iOS vs Android.",
     "kind": "categorical"},
    {"code": "runs.by_device",           "group": "runs",
     "name": "Запуски по устройствам",
     "description": "Top-N устройств, на которых чаще всего запускали.",
     "kind": "categorical",
     "params": [{"code": "limit", "type": "number", "default": 10}]},
    {"code": "runs.by_bundle",           "group": "runs",
     "name": "Запуски по приложениям",
     "description": "Top-N bundle_id — какие приложения тестируют больше всего.",
     "kind": "categorical",
     "params": [{"code": "limit", "type": "number", "default": 10}]},
    {"code": "runs.by_day",              "group": "runs",
     "name": "Запуски за последние N дней",
     "description": "Временной ряд: количество запусков в день.",
     "kind": "timeseries",
     "params": [{"code": "days", "type": "number", "default": 14}]},
    {"code": "runs.by_hour_of_day",      "group": "runs",
     "name": "Запуски по часам суток",
     "description": "В какие часы обычно запускают тесты.",
     "kind": "categorical"},
    {"code": "runs.duration_by_day",     "group": "runs",
     "name": "Средняя длительность запусков по дням",
     "description": "Временной ряд: средняя продолжительность (сек) завершённых запусков.",
     "kind": "timeseries",
     "params": [{"code": "days", "type": "number", "default": 14}]},
    {"code": "runs.duration_distribution", "group": "runs",
     "name": "Распределение длительности",
     "description": "Минимум/квартили/максимум длительности завершённых запусков — для boxplot.",
     "kind": "categorical"},
    {"code": "runs.success_rate_by_day", "group": "runs",
     "name": "Доля успешных запусков по дням",
     "description": "Процент completed от общего числа, день за днём.",
     "kind": "timeseries",
     "params": [{"code": "days", "type": "number", "default": 14}]},
    {"code": "runs.avg_steps_by_day",    "group": "runs",
     "name": "Среднее количество шагов по дням",
     "description": "Сколько шагов в среднем делает агент в день.",
     "kind": "timeseries",
     "params": [{"code": "days", "type": "number", "default": 14}]},
    {"code": "runs.recent",              "group": "runs",
     "name": "Последние запуски (таблица)",
     "description": "Плоский список последних запусков с коротким набором полей.",
     "kind": "tabular",
     "params": [{"code": "limit", "type": "number", "default": 10}]},
    {"code": "runs.by_day_by_status",    "group": "runs",
     "name": "Запуски по дням × статусам (multi-series)",
     "description": "Мультисерия: каждый статус — отдельный ряд. Отлично ложится на bar с stacked=true или на mixed.",
     "kind": "timeseries",
     "params": [{"code": "days", "type": "number", "default": 14}]},
    {"code": "runs.by_day_by_mode",      "group": "runs",
     "name": "Запуски по дням × режимам (multi-series)",
     "description": "Мультисерия: AI / MC / Hybrid — разные линии или столбцы по дням.",
     "kind": "timeseries",
     "params": [{"code": "days", "type": "number", "default": 14}]},

    # ── Defects ─────────────────────────────────────────────────────
    {"code": "defects.by_priority",      "group": "defects",
     "name": "Дефекты по приоритету",
     "description": "Сколько найдено дефектов в разбивке по P0/P1/P2/…",
     "kind": "categorical"},
    {"code": "defects.by_kind",          "group": "defects",
     "name": "Дефекты по типу",
     "description": "Сколько дефектов по типу (crash / validation / …).",
     "kind": "categorical"},
    {"code": "defects.by_day",           "group": "defects",
     "name": "Дефекты по дням",
     "description": "Временной ряд: сколько найдено дефектов в день.",
     "kind": "timeseries",
     "params": [{"code": "days", "type": "number", "default": 14}]},
    {"code": "defects.by_day_by_priority", "group": "defects",
     "name": "Дефекты по дням × приоритетам (multi-series)",
     "description": "Мультисерия: P0/P1/P2/P3 — отдельные ряды. Подходит для stacked bar.",
     "kind": "timeseries",
     "params": [{"code": "days", "type": "number", "default": 14}]},
    {"code": "defects.top_screens",      "group": "defects",
     "name": "Экраны с наибольшим числом дефектов",
     "description": "Top-N экранов, где находят больше всего проблем.",
     "kind": "categorical",
     "params": [{"code": "limit", "type": "number", "default": 10}]},
    {"code": "defects.recent",           "group": "defects",
     "name": "Последние дефекты (таблица)",
     "description": "Плоский список свежих дефектов для быстрого просмотра.",
     "kind": "tabular",
     "params": [{"code": "limit", "type": "number", "default": 10}]},

    # ── Screens ─────────────────────────────────────────────────────
    {"code": "screens.top_by_run",       "group": "screens",
     "name": "Запуски с максимальным числом экранов",
     "description": "Top-N запусков по количеству уникальных экранов.",
     "kind": "categorical",
     "params": [{"code": "limit", "type": "number", "default": 10}]},
    {"code": "screens.top_by_visits",    "group": "screens",
     "name": "Самые посещаемые экраны",
     "description": "Top-N экранов по суммарному числу визитов.",
     "kind": "categorical",
     "params": [{"code": "limit", "type": "number", "default": 10}]},

    # ── Edges (transitions) ─────────────────────────────────────────
    {"code": "edges.by_action_type",     "group": "edges",
     "name": "Переходы по типу действия",
     "description": "Сколько кликов, ввода текста, свайпов агент сделал.",
     "kind": "categorical"},
    {"code": "edges.success_ratio",      "group": "edges",
     "name": "Успешность переходов",
     "description": "Сколько переходов success=true против false.",
     "kind": "categorical"},

    # ── Scenarios ───────────────────────────────────────────────────
    {"code": "scenarios.by_active",      "group": "scenarios",
     "name": "Сценарии: активные и отключённые",
     "description": "Сколько активных / отключённых сценариев в пространстве.",
     "kind": "categorical"},
    {"code": "scenarios.recent",         "group": "scenarios",
     "name": "Последние сценарии (таблица)",
     "description": "Список сценариев с датой обновления.",
     "kind": "tabular",
     "params": [{"code": "limit", "type": "number", "default": 10}]},

    # ── Test data ───────────────────────────────────────────────────
    {"code": "test_data.by_category",    "group": "test_data",
     "name": "Тестовые данные по категориям",
     "description": "Сколько записей тестовых данных в каждой категории.",
     "kind": "categorical"},

    # ── Apps (store) ────────────────────────────────────────────────
    {"code": "apps.by_category",         "group": "apps",
     "name": "Приложения магазина по категориям",
     "description": "Распределение приложений каталога по категориям.",
     "kind": "categorical"},
    {"code": "apps.installs_top",        "group": "apps",
     "name": "Самые устанавливаемые приложения",
     "description": "Top-N приложений по количеству установок в этом пространстве.",
     "kind": "categorical",
     "params": [{"code": "limit", "type": "number", "default": 10}]},

    # ── Users ───────────────────────────────────────────────────────
    {"code": "users.by_role",            "group": "users",
     "name": "Пользователи по ролям",
     "description": "Сколько администраторов / тестировщиков / наблюдателей.",
     "kind": "categorical"},

    # ── Feedback ────────────────────────────────────────────────────
    {"code": "feedback.by_status",       "group": "feedback",
     "name": "Обращения по статусам",
     "description": "Новые / в работе / закрытые.",
     "kind": "categorical"},
    {"code": "feedback.by_kind",         "group": "feedback",
     "name": "Обращения по типу",
     "description": "Баги / вопросы / предложения / прочее.",
     "kind": "categorical"},
]

# Human labels for each group — shown as the optgroup heading in the
# frontend dropdown. Order here = display order.
GROUPS = [
    ("runs",      "Запуски"),
    ("defects",   "Дефекты"),
    ("screens",   "Экраны"),
    ("edges",     "Переходы"),
    ("scenarios", "Сценарии"),
    ("test_data", "Тестовые данные"),
    ("apps",      "Приложения"),
    ("users",     "Пользователи"),
    ("feedback",  "Обращения"),
]


def list_datasource_metadata() -> list[dict[str, Any]]:
    return [dict(m) for m in _METADATA]


def list_datasource_groups() -> list[dict[str, str]]:
    return [{"code": code, "name": name} for code, name in GROUPS]


# ── Handlers ─────────────────────────────────────────────────────────────────


def _densify(rows, days: int) -> tuple[list[str], dict[str, float]]:
    """Turn ``[(date, value), …]`` into a dict + ensure every day in the
    window is present (zero-filled). Returns (ordered_keys, by_day)."""
    buckets = {r[0].date().isoformat(): float(r[1] or 0) for r in rows}
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out_cats: list[str] = []
    day = cutoff.date()
    today = datetime.now(timezone.utc).date()
    while day <= today:
        out_cats.append(day.isoformat())
        day += timedelta(days=1)
    return out_cats, buckets


async def _by_status(session, ws_id, params):
    r = await session.execute(
        select(Run.status, func.count(Run.id))
        .where(Run.workspace_id == ws_id)
        .group_by(Run.status)
        .order_by(Run.status)
    )
    rows = r.all()
    status_ru = {
        "pending": "Ожидают", "running": "В работе", "completed": "Завершено",
        "failed": "Ошибка", "cancelled": "Отменено",
    }
    return {
        "categories": [status_ru.get(s or "", s or "—") for s, _ in rows],
        "series": [{"name": "Запуски", "data": [int(c) for _, c in rows]}],
    }


async def _by_mode(session, ws_id, params):
    r = await session.execute(
        select(Run.mode, func.count(Run.id))
        .where(Run.workspace_id == ws_id)
        .group_by(Run.mode)
    )
    rows = r.all()
    return {
        "categories": [str(m or "—") for m, _ in rows],
        "series": [{"name": "Запуски", "data": [int(c) for _, c in rows]}],
    }


async def _by_platform(session, ws_id, params):
    r = await session.execute(
        select(Run.platform, func.count(Run.id))
        .where(Run.workspace_id == ws_id)
        .group_by(Run.platform)
    )
    rows = r.all()
    platform_ru = {"ios": "iOS", "android": "Android"}
    return {
        "categories": [platform_ru.get(p or "", p or "—") for p, _ in rows],
        "series": [{"name": "Запуски", "data": [int(c) for _, c in rows]}],
    }


async def _by_device(session, ws_id, params):
    limit = max(1, min(int(params.get("limit", 10)), 50))
    r = await session.execute(
        select(Run.device_id, func.count(Run.id))
        .where(Run.workspace_id == ws_id)
        .group_by(Run.device_id)
        .order_by(func.count(Run.id).desc())
        .limit(limit)
    )
    rows = r.all()
    return {
        "categories": [str(d or "—") for d, _ in rows],
        "series": [{"name": "Запуски", "data": [int(c) for _, c in rows]}],
    }


async def _by_bundle(session, ws_id, params):
    limit = max(1, min(int(params.get("limit", 10)), 50))
    r = await session.execute(
        select(Run.bundle_id, func.count(Run.id))
        .where(Run.workspace_id == ws_id)
        .group_by(Run.bundle_id)
        .order_by(func.count(Run.id).desc())
        .limit(limit)
    )
    rows = r.all()
    return {
        "categories": [str(b or "—") for b, _ in rows],
        "series": [{"name": "Запуски", "data": [int(c) for _, c in rows]}],
    }


async def _by_day(session, ws_id, params):
    days = int(params.get("days", 14))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sql = text(
        """
        SELECT DATE_TRUNC('day', started_at AT TIME ZONE 'UTC') AS d,
               COUNT(*) AS c
        FROM runs
        WHERE workspace_id = :ws AND started_at >= :cutoff
        GROUP BY d ORDER BY d
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id), "cutoff": cutoff})).all()
    cats, by = _densify(rows, days)
    return {
        "categories": cats,
        "series": [{"name": "Запуски", "data": [int(by.get(k, 0)) for k in cats]}],
    }


def _densify_multi(
    rows, days: int, series_order: list[str], labels: dict[str, str] | None = None
) -> dict[str, Any]:
    """Zero-fill daily bins for a 2-D ``(date, group, count)`` pivot.

    Returns the standard dashboard payload: ``{categories, series}`` with
    one series per ``group`` in the order given by ``series_order``.
    Unknown groups (not in ``series_order``) are dropped — the caller is
    expected to supply the full allowlist.
    """
    # Bucket → {day_iso → {group → value}}
    buckets: dict[str, dict[str, float]] = {}
    for r in rows:
        day = r[0].date().isoformat()
        group = r[1] or "—"
        buckets.setdefault(day, {})[group] = float(r[2] or 0)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out_cats: list[str] = []
    day_cur = cutoff.date()
    today = datetime.now(timezone.utc).date()
    while day_cur <= today:
        out_cats.append(day_cur.isoformat())
        day_cur += timedelta(days=1)

    series: list[dict[str, Any]] = []
    for g in series_order:
        data = [int(buckets.get(d, {}).get(g, 0)) for d in out_cats]
        name = (labels or {}).get(g, g)
        series.append({"name": name, "data": data})
    return {"categories": out_cats, "series": series}


async def _by_day_by_status(session, ws_id, params):
    days = int(params.get("days", 14))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sql = text(
        """
        SELECT DATE_TRUNC('day', started_at AT TIME ZONE 'UTC') AS d,
               status AS g,
               COUNT(*) AS c
        FROM runs
        WHERE workspace_id = :ws AND started_at >= :cutoff
        GROUP BY d, status ORDER BY d
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id), "cutoff": cutoff})).all()
    status_order = ["running", "completed", "failed", "cancelled", "pending"]
    status_ru = {
        "pending": "Ожидают", "running": "В работе", "completed": "Завершено",
        "failed": "Ошибка", "cancelled": "Отменено",
    }
    return _densify_multi(rows, days, status_order, status_ru)


async def _by_day_by_mode(session, ws_id, params):
    days = int(params.get("days", 14))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sql = text(
        """
        SELECT DATE_TRUNC('day', started_at AT TIME ZONE 'UTC') AS d,
               COALESCE(mode, '—') AS g,
               COUNT(*) AS c
        FROM runs
        WHERE workspace_id = :ws AND started_at >= :cutoff
        GROUP BY d, mode ORDER BY d
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id), "cutoff": cutoff})).all()
    mode_order = ["ai", "mc", "hybrid", "—"]
    mode_labels = {"ai": "AI", "mc": "MC (MCTS)", "hybrid": "Hybrid", "—": "Без режима"}
    return _densify_multi(rows, days, mode_order, mode_labels)


async def _by_hour_of_day(session, ws_id, params):
    sql = text(
        """
        SELECT EXTRACT(HOUR FROM started_at AT TIME ZONE 'UTC')::int AS h,
               COUNT(*) AS c
        FROM runs
        WHERE workspace_id = :ws AND started_at IS NOT NULL
        GROUP BY h ORDER BY h
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id)})).all()
    by = {int(h): int(c) for h, c in rows}
    return {
        "categories": [f"{h:02d}:00" for h in range(24)],
        "series": [{"name": "Запуски", "data": [by.get(h, 0) for h in range(24)]}],
    }


async def _duration_by_day(session, ws_id, params):
    days = int(params.get("days", 14))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sql = text(
        """
        SELECT DATE_TRUNC('day', started_at AT TIME ZONE 'UTC') AS d,
               AVG(EXTRACT(EPOCH FROM (finished_at - started_at))) AS sec
        FROM runs
        WHERE workspace_id = :ws AND started_at >= :cutoff
          AND finished_at IS NOT NULL
        GROUP BY d ORDER BY d
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id), "cutoff": cutoff})).all()
    cats, by = _densify(rows, days)
    return {
        "categories": cats,
        "series": [{
            "name": "Секунды",
            "data": [round(by.get(k, 0), 1) for k in cats],
        }],
    }


async def _duration_distribution(session, ws_id, params):
    """Returns a single boxplot point: [min, q1, median, q3, max]."""
    sql = text(
        """
        SELECT
          MIN(d) AS minv,
          PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY d) AS q1,
          PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY d) AS med,
          PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY d) AS q3,
          MAX(d) AS maxv
        FROM (
          SELECT EXTRACT(EPOCH FROM (finished_at - started_at)) AS d
          FROM runs
          WHERE workspace_id = :ws AND finished_at IS NOT NULL
        ) t
        """
    )
    row = (await session.execute(sql, {"ws": str(ws_id)})).first()
    if not row or row[0] is None:
        return {"categories": ["Все запуски"], "series": [{"name": "Длительность (сек)", "data": []}]}
    stats = [round(float(v), 1) for v in row]
    return {
        "categories": ["Все запуски"],
        "series": [
            {
                "name": "Длительность (сек)",
                "data": [{"x": "Все запуски", "y": stats}],
            }
        ],
    }


async def _success_rate_by_day(session, ws_id, params):
    days = int(params.get("days", 14))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sql = text(
        """
        SELECT DATE_TRUNC('day', started_at AT TIME ZONE 'UTC') AS d,
               100.0 * COUNT(*) FILTER (WHERE status='completed') / NULLIF(COUNT(*), 0) AS pct
        FROM runs
        WHERE workspace_id = :ws AND started_at >= :cutoff
        GROUP BY d ORDER BY d
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id), "cutoff": cutoff})).all()
    cats, by = _densify(rows, days)
    return {
        "categories": cats,
        "series": [{"name": "% успешных", "data": [round(by.get(k, 0), 1) for k in cats]}],
    }


async def _avg_steps_by_day(session, ws_id, params):
    days = int(params.get("days", 14))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sql = text(
        """
        SELECT DATE_TRUNC('day', r.started_at AT TIME ZONE 'UTC') AS d,
               AVG(step_counts.c) AS avg_steps
        FROM runs r
        LEFT JOIN LATERAL (
          SELECT COUNT(*) AS c FROM edges e WHERE e.run_id = r.id
        ) step_counts ON TRUE
        WHERE r.workspace_id = :ws AND r.started_at >= :cutoff
        GROUP BY d ORDER BY d
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id), "cutoff": cutoff})).all()
    cats, by = _densify(rows, days)
    return {
        "categories": cats,
        "series": [{"name": "Среднее шагов", "data": [round(by.get(k, 0), 1) for k in cats]}],
    }


async def _runs_recent(session, ws_id, params):
    limit = max(1, min(int(params.get("limit", 10)), 200))
    r = await session.execute(
        select(Run.id, Run.status, Run.mode, Run.bundle_id, Run.started_at, Run.finished_at)
        .where(Run.workspace_id == ws_id)
        .order_by(Run.started_at.desc().nulls_last())
        .limit(limit)
    )
    rows = r.all()
    return {
        "is_tabular": True,
        "columns": [
            {"code": "started_at", "name": "Начало"},
            {"code": "status",     "name": "Статус"},
            {"code": "mode",       "name": "Режим"},
            {"code": "bundle_id",  "name": "Приложение"},
            {"code": "duration",   "name": "Длительность"},
        ],
        "rows": [
            [
                row.started_at.isoformat() if row.started_at else None,
                row.status, row.mode, row.bundle_id,
                (round((row.finished_at - row.started_at).total_seconds(), 1)
                 if (row.started_at and row.finished_at) else None),
            ]
            for row in rows
        ],
    }


async def _defects_by_priority(session, ws_id, params):
    sql = text(
        """
        SELECT d.priority, COUNT(*) FROM defects d
        JOIN runs r ON r.id = d.run_id
        WHERE r.workspace_id = :ws
        GROUP BY d.priority ORDER BY d.priority
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id)})).all()
    return {
        "categories": [str(p or "—") for p, _ in rows],
        "series": [{"name": "Дефекты", "data": [int(c) for _, c in rows]}],
    }


async def _defects_by_kind(session, ws_id, params):
    sql = text(
        """
        SELECT d.kind, COUNT(*) FROM defects d
        JOIN runs r ON r.id = d.run_id
        WHERE r.workspace_id = :ws
        GROUP BY d.kind ORDER BY COUNT(*) DESC
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id)})).all()
    return {
        "categories": [str(k or "—") for k, _ in rows],
        "series": [{"name": "Дефекты", "data": [int(c) for _, c in rows]}],
    }


async def _defects_by_day(session, ws_id, params):
    days = int(params.get("days", 14))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sql = text(
        """
        SELECT DATE_TRUNC('day', d.created_at AT TIME ZONE 'UTC') AS day, COUNT(*)
        FROM defects d JOIN runs r ON r.id = d.run_id
        WHERE r.workspace_id = :ws AND d.created_at >= :cutoff
        GROUP BY day ORDER BY day
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id), "cutoff": cutoff})).all()
    cats, by = _densify(rows, days)
    return {
        "categories": cats,
        "series": [{"name": "Дефекты", "data": [int(by.get(k, 0)) for k in cats]}],
    }


async def _defects_by_day_by_priority(session, ws_id, params):
    days = int(params.get("days", 14))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sql = text(
        """
        SELECT DATE_TRUNC('day', d.created_at AT TIME ZONE 'UTC') AS day,
               COALESCE(d.priority, '—') AS g,
               COUNT(*) AS c
        FROM defects d JOIN runs r ON r.id = d.run_id
        WHERE r.workspace_id = :ws AND d.created_at >= :cutoff
        GROUP BY day, d.priority ORDER BY day
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id), "cutoff": cutoff})).all()
    prio_order = ["P0", "P1", "P2", "P3", "—"]
    labels = {"P0": "P0 (критично)", "P1": "P1 (важно)", "P2": "P2 (обычно)", "P3": "P3 (мелочь)", "—": "Без приоритета"}
    return _densify_multi(rows, days, prio_order, labels)


async def _defects_top_screens(session, ws_id, params):
    limit = max(1, min(int(params.get("limit", 10)), 50))
    sql = text(
        """
        SELECT COALESCE(d.screen_name, '—') AS screen, COUNT(*) AS c
        FROM defects d JOIN runs r ON r.id = d.run_id
        WHERE r.workspace_id = :ws
        GROUP BY screen ORDER BY c DESC LIMIT :lim
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id), "lim": limit})).all()
    return {
        "categories": [str(n) for n, _ in rows],
        "series": [{"name": "Дефекты", "data": [int(c) for _, c in rows]}],
    }


async def _defects_recent(session, ws_id, params):
    limit = max(1, min(int(params.get("limit", 10)), 200))
    sql = text(
        """
        SELECT d.created_at, d.priority, d.kind, d.title, d.screen_name
        FROM defects d JOIN runs r ON r.id = d.run_id
        WHERE r.workspace_id = :ws
        ORDER BY d.created_at DESC LIMIT :lim
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id), "lim": limit})).all()
    return {
        "is_tabular": True,
        "columns": [
            {"code": "created_at", "name": "Когда"},
            {"code": "priority",   "name": "Приоритет"},
            {"code": "kind",       "name": "Тип"},
            {"code": "title",      "name": "Заголовок"},
            {"code": "screen",     "name": "Экран"},
        ],
        "rows": [
            [
                row[0].isoformat() if row[0] else None,
                row[1], row[2], row[3], row[4],
            ]
            for row in rows
        ],
    }


async def _screens_top_by_run(session, ws_id, params):
    limit = max(1, min(int(params.get("limit", 10)), 50))
    sql = text(
        """
        SELECT r.id, COUNT(s.id) AS c
        FROM runs r LEFT JOIN screens s ON s.run_id = r.id
        WHERE r.workspace_id = :ws
        GROUP BY r.id ORDER BY c DESC LIMIT :lim
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id), "lim": limit})).all()
    return {
        "categories": [f"Run {str(rid)[:8]}" for rid, _ in rows],
        "series": [{"name": "Экранов", "data": [int(c) for _, c in rows]}],
    }


async def _screens_top_by_visits(session, ws_id, params):
    limit = max(1, min(int(params.get("limit", 10)), 50))
    sql = text(
        """
        SELECT COALESCE(s.name, '—') AS name, SUM(s.visit_count) AS v
        FROM screens s JOIN runs r ON r.id = s.run_id
        WHERE r.workspace_id = :ws
        GROUP BY name ORDER BY v DESC NULLS LAST LIMIT :lim
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id), "lim": limit})).all()
    return {
        "categories": [str(n) for n, _ in rows],
        "series": [{"name": "Визитов", "data": [int(v or 0) for _, v in rows]}],
    }


async def _edges_by_action_type(session, ws_id, params):
    sql = text(
        """
        SELECT e.action_type, COUNT(*) AS c
        FROM edges e JOIN runs r ON r.id = e.run_id
        WHERE r.workspace_id = :ws
        GROUP BY e.action_type ORDER BY c DESC
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id)})).all()
    return {
        "categories": [str(a or "—") for a, _ in rows],
        "series": [{"name": "Переходы", "data": [int(c) for _, c in rows]}],
    }


async def _edges_success_ratio(session, ws_id, params):
    sql = text(
        """
        SELECT COALESCE(e.success, false) AS ok, COUNT(*) AS c
        FROM edges e JOIN runs r ON r.id = e.run_id
        WHERE r.workspace_id = :ws
        GROUP BY ok
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id)})).all()
    by = {bool(k): int(v) for k, v in rows}
    return {
        "categories": ["Успешные", "Неудачные"],
        "series": [{"name": "Переходы", "data": [by.get(True, 0), by.get(False, 0)]}],
    }


async def _scenarios_by_active(session, ws_id, params):
    sql = text(
        """
        SELECT COALESCE(is_active, false) AS active, COUNT(*) AS c
        FROM scenarios WHERE workspace_id = :ws GROUP BY active
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id)})).all()
    by = {bool(k): int(v) for k, v in rows}
    return {
        "categories": ["Активные", "Отключённые"],
        "series": [{"name": "Сценарии", "data": [by.get(True, 0), by.get(False, 0)]}],
    }


async def _scenarios_recent(session, ws_id, params):
    limit = max(1, min(int(params.get("limit", 10)), 200))
    sql = text(
        """
        SELECT title, is_active, COALESCE(updated_at, created_at) AS touched
        FROM scenarios WHERE workspace_id = :ws
        ORDER BY touched DESC NULLS LAST LIMIT :lim
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id), "lim": limit})).all()
    return {
        "is_tabular": True,
        "columns": [
            {"code": "title",   "name": "Название"},
            {"code": "active",  "name": "Активен"},
            {"code": "touched", "name": "Изменён"},
        ],
        "rows": [
            [row[0], "да" if row[1] else "нет", row[2].isoformat() if row[2] else None]
            for row in rows
        ],
    }


async def _test_data_by_category(session, ws_id, params):
    sql = text(
        """
        SELECT COALESCE(category, '—'), COUNT(*)
        FROM test_data WHERE workspace_id = :ws
        GROUP BY category ORDER BY COUNT(*) DESC
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id)})).all()
    return {
        "categories": [str(c) for c, _ in rows],
        "series": [{"name": "Записи", "data": [int(c) for _, c in rows]}],
    }


async def _apps_by_category(session, ws_id, params):
    # Apps are global; no workspace filter. ``ws_id`` is accepted for
    # signature compat.
    sql = text(
        """
        SELECT category, COUNT(*) FROM app_packages
        WHERE approval_status = 'approved'
        GROUP BY category ORDER BY COUNT(*) DESC
        """
    )
    rows = (await session.execute(sql)).all()
    return {
        "categories": [str(c or "—") for c, _ in rows],
        "series": [{"name": "Приложений", "data": [int(c) for _, c in rows]}],
    }


async def _apps_installs_top(session, ws_id, params):
    limit = max(1, min(int(params.get("limit", 10)), 50))
    sql = text(
        """
        SELECT p.name, COUNT(i.id) AS installs
        FROM app_packages p
        LEFT JOIN app_installations i ON i.app_package_id = p.id
          AND i.workspace_id = :ws
        GROUP BY p.name ORDER BY installs DESC LIMIT :lim
        """
    )
    rows = (await session.execute(sql, {"ws": str(ws_id), "lim": limit})).all()
    return {
        "categories": [str(n) for n, _ in rows],
        "series": [{"name": "Установок", "data": [int(c) for _, c in rows]}],
    }


async def _users_by_role(session, ws_id, params):
    sql = text(
        """
        SELECT COALESCE(r.name, u.role) AS role_name, COUNT(u.id)
        FROM users u LEFT JOIN roles r ON r.id = u.role_id
        GROUP BY role_name ORDER BY COUNT(u.id) DESC
        """
    )
    rows = (await session.execute(sql)).all()
    return {
        "categories": [str(n or "—") for n, _ in rows],
        "series": [{"name": "Пользователи", "data": [int(c) for _, c in rows]}],
    }


async def _feedback_by_status(session, ws_id, params):
    sql = text("SELECT status, COUNT(*) FROM feedback_tickets GROUP BY status")
    rows = (await session.execute(sql)).all()
    status_ru = {"new": "Новые", "in_progress": "В работе", "closed": "Закрытые"}
    return {
        "categories": [status_ru.get(s or "", s or "—") for s, _ in rows],
        "series": [{"name": "Обращения", "data": [int(c) for _, c in rows]}],
    }


async def _feedback_by_kind(session, ws_id, params):
    sql = text("SELECT kind, COUNT(*) FROM feedback_tickets GROUP BY kind")
    rows = (await session.execute(sql)).all()
    kind_ru = {"bug": "Баг", "question": "Вопрос", "proposal": "Предложение", "other": "Другое"}
    return {
        "categories": [kind_ru.get(s or "", s or "—") for s, _ in rows],
        "series": [{"name": "Обращения", "data": [int(c) for _, c in rows]}],
    }


HANDLERS: dict[str, DatasourceHandler] = {
    "runs.by_status":             _by_status,
    "runs.by_mode":               _by_mode,
    "runs.by_platform":           _by_platform,
    "runs.by_device":             _by_device,
    "runs.by_bundle":             _by_bundle,
    "runs.by_day":                _by_day,
    "runs.by_day_by_status":      _by_day_by_status,
    "runs.by_day_by_mode":        _by_day_by_mode,
    "runs.by_hour_of_day":        _by_hour_of_day,
    "runs.duration_by_day":       _duration_by_day,
    "runs.duration_distribution": _duration_distribution,
    "runs.success_rate_by_day":   _success_rate_by_day,
    "runs.avg_steps_by_day":      _avg_steps_by_day,
    "runs.recent":                _runs_recent,
    "defects.by_priority":        _defects_by_priority,
    "defects.by_kind":            _defects_by_kind,
    "defects.by_day":             _defects_by_day,
    "defects.by_day_by_priority": _defects_by_day_by_priority,
    "defects.top_screens":        _defects_top_screens,
    "defects.recent":             _defects_recent,
    "screens.top_by_run":         _screens_top_by_run,
    "screens.top_by_visits":      _screens_top_by_visits,
    "edges.by_action_type":       _edges_by_action_type,
    "edges.success_ratio":        _edges_success_ratio,
    "scenarios.by_active":        _scenarios_by_active,
    "scenarios.recent":           _scenarios_recent,
    "test_data.by_category":      _test_data_by_category,
    "apps.by_category":           _apps_by_category,
    "apps.installs_top":          _apps_installs_top,
    "users.by_role":              _users_by_role,
    "feedback.by_status":         _feedback_by_status,
    "feedback.by_kind":           _feedback_by_kind,
}


async def resolve(
    code: str,
    workspace_id: UUID,
    params: dict[str, Any] | None,
    session: AsyncSession,
) -> DatasourcePayload:
    handler = HANDLERS.get(code)
    if handler is None:
        return {"categories": [], "series": [{"name": "—", "data": []}]}
    try:
        return await handler(session, workspace_id, params or {})
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception(
            "datasource %s failed for ws=%s: %s", code, workspace_id, e
        )
        return {
            "categories": [],
            "series": [{"name": "ошибка", "data": []}],
            "error": str(e),
        }
