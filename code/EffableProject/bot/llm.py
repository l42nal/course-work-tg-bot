"""
Модуль для взаимодействия с LLM через OpenRouter API.

Хранит историю диалогов в памяти и отправляет сообщения
пользователя в LLM с системным промтом «мягкого психолога».
"""

import os
import logging
from collections import defaultdict
from typing import Dict, List

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 40

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

_histories: Dict[int, List[dict]] = defaultdict(list)


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

    _model = os.getenv("OPENROUTER_MODEL", "openrouter/free")

    _client = AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )
    logger.info("LLM-клиент инициализирован (модель: %s)", _model)


async def get_response(user_id: int, user_text: str) -> str:
    """
    Отправляет сообщение пользователя в LLM и возвращает ответ.
    Поддерживает контекст диалога (до MAX_HISTORY_MESSAGES сообщений).
    """
    if _client is None:
        return (
            "Спасибо за сообщение! К сожалению, AI-ассистент сейчас недоступен. "
            "Я буду спрашивать тебя каждый день в 21:00, как прошел твой день."
        )

    history = _histories[user_id]
    history.append({"role": "user", "content": user_text})

    if len(history) > MAX_HISTORY_MESSAGES:
        history[:] = history[-MAX_HISTORY_MESSAGES:]

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    try:
        response = await _client.chat.completions.create(
            model=_model,
            messages=messages,
        )
        assistant_text = response.choices[0].message.content or ""
        history.append({"role": "assistant", "content": assistant_text})
        return assistant_text

    except Exception:
        logger.exception("Ошибка при запросе к LLM для user_id=%s", user_id)
        if history and history[-1]["role"] == "user":
            history.pop()
        return (
            "Прости, произошла ошибка при обработке сообщения. "
            "Попробуй написать ещё раз чуть позже."
        )
