"""/api/assistant/chat — context-aware AI helper for the QA workflow.

Not a help system. The assistant analyses what the user has in front of
them — current run results, defects, screens, scenarios — and helps with
real QA tasks: triage, prioritization, suggesting next actions.

How context flows in:
  1. Frontend posts {messages, context: {route, run_id?, scenario_id?}}.
  2. Backend looks up the relevant data from Postgres (run summary + top
     defects + screen counts when run_id is set, scenario steps when
     scenario_id is set).
  3. We render the data into a structured context block prepended to the
     system prompt. The LLM then has actual numbers to reason about, not
     just "imagine there are some defects".

We deliberately don't load EVERYTHING into the context (Qwen3 has 32K but
defects/screens can blow that). For runs with >20 defects we send only the
top-20 by priority — the assistant can ask for more by getting the user
to filter the Defects panel.

For pure how-do-I questions there's a separate /docs static page (the
"Справка" section) — we tell users about it and don't try to be one.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user
from app.config import settings
from app.db import get_async_session
from app.models.defect import DefectModel
from app.models.run import Run
from app.models.scenario import Scenario
from app.models.user import User

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


SYSTEM_PROMPT = """Ты — встроенный AI-помощник QA-инженера в системе \
тестирования «Марков». Твоя главная задача — помогать пользователю \
анализировать результаты работы агента-исследователя и принимать \
решения по найденным дефектам.

Что ты должен делать:
- Анализировать результаты исследования: какие экраны охвачены, какие \
дефекты найдены, что осталось не исследовано
- Расставлять приоритеты дефектов: какие критичны, какие можно отложить, \
какие — false positive
- Предлагать дальнейшие действия: какие сценарии стоит написать, какие \
тестовые данные добавить, что отдать в TestOps в первую очередь
- Помогать формулировать описания багов для тикетов
- Кратко резюмировать большие списки (например, «10 главных проблем \
этого запуска»)

Стиль общения:
- На русском, по-деловому, без «воды»
- Если у тебя есть данные о текущем запуске — опирайся ТОЛЬКО на них, \
не выдумывай
- Предлагай конкретные шаги, а не общие фразы
- Если данных недостаточно — попроси пользователя уточнить или открыть \
нужный раздел
- Если вопрос не про текущую работу, а про «как пользоваться системой» \
— скажи: «Это в разделе „Справка“ слева» и не отвечай длинно

НЕ используй теги <think>. Отвечай готовым ответом."""


# ---------- Schemas ----------

class AssistantMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AssistantContext(BaseModel):
    """What the user is currently looking at. All fields optional."""

    route: str | None = Field(default=None, max_length=200)
    run_id: UUID | None = None
    scenario_id: UUID | None = None


class AssistantChatRequest(BaseModel):
    messages: list[AssistantMessage] = Field(min_length=1, max_length=20)
    context: AssistantContext | None = None


class AssistantChatResponse(BaseModel):
    answer: str


# ---------- Context loaders ----------

async def _load_run_context(run_id: UUID, session: AsyncSession) -> str:
    """Render run + defects summary as plain Russian text for the prompt."""
    run = (
        await session.execute(select(Run).where(Run.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        return ""

    # Top 20 defects, P0 first.
    defects = list(
        (
            await session.execute(
                select(DefectModel)
                .where(DefectModel.run_id == run_id)
                .order_by(DefectModel.priority.asc(), DefectModel.step_idx.asc())
                .limit(20)
            )
        ).scalars().all()
    )

    # Counts by priority for the summary line.
    counts: dict[str, int] = {}
    for d in defects:
        counts[d.priority] = counts.get(d.priority, 0) + 1

    parts: list[str] = []
    parts.append(f"### Текущий запуск {run.id}")
    parts.append(f"- Bundle: {run.bundle_id}")
    parts.append(f"- Платформа: {run.platform}, режим: {run.mode}, статус: {run.status}")
    parts.append(f"- Шагов выполнено: до {run.max_steps}")
    if run.error_message:
        parts.append(f"- Ошибка: {run.error_message}")

    if defects:
        summary = ", ".join(f"{p}={n}" for p, n in sorted(counts.items()))
        parts.append(f"\n### Найденные дефекты (показано {len(defects)}, всего: {summary})")
        for d in defects:
            head = f"[{d.priority}/{d.kind}]"
            screen = f" на «{d.screen_name}»" if d.screen_name else ""
            step = f" (шаг {d.step_idx})" if d.step_idx is not None else ""
            parts.append(f"- {head} {d.title}{screen}{step}")
            # First line of description for context; full text is in the UI.
            first_line = (d.description or "").split("\n", 1)[0][:200]
            if first_line:
                parts.append(f"  {first_line}")
    else:
        parts.append("\n### Дефектов не найдено")

    return "\n".join(parts)


async def _load_scenario_context(scenario_id: UUID, session: AsyncSession) -> str:
    """Render scenario steps for the prompt."""
    sc = (
        await session.execute(select(Scenario).where(Scenario.id == scenario_id))
    ).scalar_one_or_none()
    if sc is None:
        return ""

    steps = (sc.steps_json or {}).get("steps", [])
    parts = [f"### Текущий сценарий «{sc.title}» ({len(steps)} шагов)"]
    if sc.description:
        parts.append(sc.description)
    for i, step in enumerate(steps[:30], 1):
        screen = step.get("screen_name", "")
        action = step.get("action", "tap")
        elem = step.get("element_label", "")
        val = step.get("value", "")
        line = f"{i}. [{screen}] {action} «{elem}»"
        if val:
            line += f" = {val}"
        parts.append(line)
    if len(steps) > 30:
        parts.append(f"… ещё {len(steps) - 30} шагов")
    return "\n".join(parts)


async def _build_context_block(
    ctx: AssistantContext | None, session: AsyncSession,
) -> str:
    """Combine all context loaders into one prompt section. Empty if no context."""
    if ctx is None:
        return ""

    blocks: list[str] = []
    if ctx.run_id is not None:
        blocks.append(await _load_run_context(ctx.run_id, session))
    if ctx.scenario_id is not None:
        blocks.append(await _load_scenario_context(ctx.scenario_id, session))
    if ctx.route:
        blocks.append(f"Пользователь сейчас на странице: {ctx.route}")

    return "\n\n".join(b for b in blocks if b)


# ---------- Endpoint ----------

def _clean(text: str) -> str:
    if not text:
        return text
    import re
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<\|im_end\|>|<\|im_start\|>|<turn\|>|</s>", "", text)
    return text.strip()


@router.post("/chat", response_model=AssistantChatResponse)
async def chat(
    payload: AssistantChatRequest,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> AssistantChatResponse:
    """Multi-turn chat with the helper. Context is hydrated server-side."""
    llm_url = (settings.rag_llm_base_url or settings.llm_base_url).rstrip("/")

    context_block = await _build_context_block(payload.context, session)
    system = SYSTEM_PROMPT
    if context_block:
        system = (
            SYSTEM_PROMPT
            + "\n\n## Контекст того, что пользователь сейчас видит\n\n"
            + context_block
        )

    messages: list[dict] = [{"role": "system", "content": system}]
    for m in payload.messages:
        messages.append({"role": m.role, "content": m.content})

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{llm_url}/v1/chat/completions",
                json={
                    "model": "rag-chat",
                    "messages": messages,
                    "max_tokens": 1024,
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            msg = data["choices"][0]["message"]
            content = _clean(msg.get("content") or "")
            if not content:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Модель вернула пустой ответ",
                )
            return AssistantChatResponse(answer=content)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM недоступен: {exc}",
        )
