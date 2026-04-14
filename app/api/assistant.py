"""/api/assistant/chat — in-app AI assistant powered by the RAG LLM.

Lightweight chat endpoint for the question-bubble in the top header. The
assistant knows about the Марков system itself (sections, settings, how to
write scenarios, etc.) — NOT about the user's tested apps. For app-related
Q&A, users open the Knowledge Base and run a query there.

Conversation state is client-side. The frontend keeps the message history
and posts the full thread on each turn. We're using Qwen3-8B Instruct (the
RAG LLM) because it handles Russian well and is already loaded.
"""

from __future__ import annotations

from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth.users import current_active_user
from app.config import settings
from app.models.user import User

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


SYSTEM_PROMPT = """Ты — встроенный ассистент в системе тестирования \
«Марков». Отвечаешь на вопросы пользователей о том, как пользоваться \
системой. Отвечай кратко и по существу, на русском.

Ключевые разделы интерфейса:
- Запуски — список всех запусков исследования. Кнопка «Новый запуск» \
открывает модалку, где загружается сборка приложения, выбирается \
устройство, режим (AI/MC/Hybrid), сценарии (опционально), PBT-режим.
- База знаний — документы (PDF, DOCX, MD, etc.), агент может к ним \
обращаться через RAG. Embedding: Qwen3-Embedding-8B (4096 dim). \
Reranker: Qwen3-Reranker-8B. LLM ответов: Qwen3-8B Instruct.
- Сценарии — пошаговые планы для агента. Поддерживают подстановку \
{{test_data.key}}. Три режима редактирования: Конструктор / Блок-схема \
/ JSON.
- Тестовые данные — пары ключ-значение (email, password, account_no, \
…), которые агент использует при заполнении форм. Категории: auth, \
payment, personal, general.
- Устройства — администратор задаёт доступные комбинации устройство+ОС.
- LLM модели — каталог моделей, можно скачать с HuggingFace.
- Дефекты — на странице результатов запуска. LLM-классификатор после \
каждого шага решает, дефект это или infra-шум, и присваивает приоритет \
(P0 блокер / P1 критический / P2 существенный / P3 косметический).
- Пользователи — три роли: admin (всё), tester (запуски + сценарии + \
данные), viewer (только просмотр).

Режимы работы агента:
- AI: LLM решает каждое действие (Gemma 4 E4B с vision)
- MC: случайный обход через Monte-Carlo (без LLM)
- Hybrid: LLM подсказывает, MC проверяет

Если пользователь спрашивает про что-то, чего нет в системе, честно скажи \
что не знаешь. Не выдумывай функции.

НЕ используй теги <think>. Отвечай готовым ответом."""


class AssistantMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AssistantChatRequest(BaseModel):
    messages: list[AssistantMessage] = Field(min_length=1, max_length=20)


class AssistantChatResponse(BaseModel):
    answer: str


@router.post("/chat", response_model=AssistantChatResponse)
async def chat(
    payload: AssistantChatRequest,
    _user: Annotated[User, Depends(current_active_user)],
) -> AssistantChatResponse:
    """Single-turn (with history) chat with the in-app assistant."""
    llm_url = (settings.rag_llm_base_url or settings.llm_base_url).rstrip("/")

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in payload.messages:
        messages.append({"role": m.role, "content": m.content})

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{llm_url}/v1/chat/completions",
                json={
                    "model": "rag-chat",
                    "messages": messages,
                    "max_tokens": 800,
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            msg = data["choices"][0]["message"]
            content = (msg.get("content") or "").strip()
            # Strip artifacts
            if content:
                import re
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
                content = re.sub(
                    r"<\|im_end\|>|<\|im_start\|>|<turn\|>|</s>", "", content,
                )
                content = content.strip()
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
