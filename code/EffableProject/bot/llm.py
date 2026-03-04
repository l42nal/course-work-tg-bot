"""
Модуль для взаимодействия с LLM через OpenRouter API.

Хранит историю диалогов в памяти: summary (сжатое резюме старых сообщений)
и recent (последние сообщения). Периодически сжимает историю через LLM.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Dict, List

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

RECENT_MESSAGES = 18
SUMMARY_MAX_CHARS = 1200

SUMMARY_PROMPT = (
    "Сожми следующий фрагмент диалога в краткое резюме.\n"
    "Требования:\n"
    "- 5–10 пунктов (можно списком)\n"
    "- Сохрани факты и эмоции пользователя\n"
    "- Не добавляй выдуманную информацию\n"
    "- Только факты из диалога, без советов\n"
    "- Максимум {max_chars} символов.\n\n"
    "Фрагмент диалога:\n"
)

SYSTEM_PROMPT = (
    "Ты — тёплый и внимательный собеседник, похожий на чуткого психолога. "
    "Твоя главная задача — выслушать человека, дать ему почувствовать, "
    "что его эмоции важны и что он не один.\n\n"
    "Правила, которым ты следуешь:\n"
    "- Никогда не давай прямых советов и не говори, что человеку «нужно» или «следует» делать.\n"
    "- Не оценивай чувства как правильные или неправильные.\n"
    "- Задавай мягкие уточняющие вопросы, чтобы помочь человеку самому разобраться в своих переживаниях.\n"
    "- Используй фразы поддержки: «Я понимаю», «Это звучит непросто», "
    "«Спасибо, что делишься этим».\n"
    "- Отвечай на том же языке, на котором пишет пользователь.\n"
    "- Будь лаконичен — отвечай в 2–5 предложений, если ситуация не требует большего.\n"
    "- Если человек делится чем-то радостным, искренне раздели его радость.\n"
    "- Если человек молчит или пишет мало — мягко предложи рассказать подробнее, "
    "но не дави.\n"
    "- Помни: ты не врач и не даёшь медицинских рекомендаций. "
    "Если человек описывает кризисную ситуацию, мягко предложи обратиться "
    "к специалисту или на горячую линию психологической помощи."
)

_client: AsyncOpenAI | None = None
_model: str = ""


@dataclass
class Memory:
    """Память пользователя: резюме старых сообщений + последние сообщения."""
    summary: str = ""
    recent: List[dict] = field(default_factory=list)


_memories: Dict[int, Memory] = {}


def _get_memory(user_id: int) -> Memory:
    if user_id not in _memories:
        _memories[user_id] = Memory()
    return _memories[user_id]


def _format_messages_for_summary(messages: List[dict]) -> str:
    parts = []
    for m in messages:
        role = "Пользователь" if m["role"] == "user" else "Ассистент"
        parts.append(f"{role}: {m['content']}")
    return "\n\n".join(parts)


async def _summarize(
    messages: List[dict], existing_summary: str, user_id: int = 0
) -> str:
    """Сжимает сообщения в краткое резюме через LLM."""
    text = _format_messages_for_summary(messages)
    if existing_summary:
        prompt = (
            f"Текущее резюме разговора:\n{existing_summary}\n\n"
            f"Дополнительные сообщения для включения в резюме:\n{text}"
        )
    else:
        prompt = text

    full_prompt = SUMMARY_PROMPT.format(max_chars=SUMMARY_MAX_CHARS) + prompt

    try:
        response = await _client.chat.completions.create(
            model=_model,
            messages=[{"role": "user", "content": full_prompt}],
        )
        summary = (response.choices[0].message.content or "").strip()
        return summary[:SUMMARY_MAX_CHARS]
    except Exception:
        logger.exception("Ошибка при суммаризации для user_id=%s", user_id)
        return existing_summary


def init_llm() -> None:
    """Инициализирует клиент OpenRouter. Вызывать один раз при старте бота."""
    global _client, _model

    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        logger.warning(
            "OPENROUTER_API_KEY не задан — LLM-ответы будут недоступны"
        )
        _client = None
        return

    _model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat")

    _client = AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )
    logger.info("LLM-клиент инициализирован (модель: %s)", _model)


async def get_response(user_id: int, user_text: str) -> str:
    """
    Отправляет сообщение пользователя в LLM и возвращает ответ.
    Использует summary + recent для управления контекстом.
    """
    if _client is None:
        return (
            "Спасибо за сообщение! К сожалению, AI-ассистент сейчас недоступен. "
            "Я буду спрашивать тебя каждый день в 21:00, как прошел твой день."
        )

    mem = _get_memory(user_id)
    mem.recent.append({"role": "user", "content": user_text})

    if len(mem.recent) > RECENT_MESSAGES:
        to_summarize = mem.recent[: len(mem.recent) - RECENT_MESSAGES]
        mem.recent = mem.recent[-RECENT_MESSAGES:]
        mem.summary = await _summarize(to_summarize, mem.summary, user_id)
        mem.summary = mem.summary[:SUMMARY_MAX_CHARS]

    system_content = SYSTEM_PROMPT
    if mem.summary:
        system_content += f"\n\nКонтекст предыдущего разговора:\n{mem.summary}"

    messages = [{"role": "system", "content": system_content}] + mem.recent

    try:
        response = await _client.chat.completions.create(
            model=_model,
            messages=messages,
            temperature=0.7,
            max_tokens=200,
        )
        assistant_text = response.choices[0].message.content or ""
        mem.recent.append({"role": "assistant", "content": assistant_text})
        return assistant_text

    except Exception:
        logger.exception("Ошибка при запросе к LLM для user_id=%s", user_id)
        if mem.recent and mem.recent[-1]["role"] == "user":
            mem.recent.pop()
        return (
            "Прости, произошла ошибка при обработке сообщения. "
            "Попробуй написать ещё раз чуть позже."
        )
