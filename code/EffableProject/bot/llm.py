"""
Модуль для взаимодействия с LLM через OpenRouter API.

Хранит историю диалогов в памяти: summary (сжатое резюме старых сообщений)
и recent (последние сообщения). Периодически сжимает историю через LLM.
"""

import os
import logging
import json
from dataclasses import dataclass, field
from typing import Dict, List
import time

_last_request_time: Dict[int, float] = {}
MIN_REQUEST_INTERVAL = 3

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
    now = time.time()
    last = _last_request_time.get(user_id, 0)

    if now - last < MIN_REQUEST_INTERVAL:
        return "Подожди пару секунд перед следующим сообщением 🙂"

    _last_request_time[user_id] = now
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


async def generate_plan_summary_and_followup(user_id: int, raw_plan_text: str) -> tuple[str, str]:
    """
    Один вызов LLM:
    1) сжимает планы пользователя до 1-3 ключевых действий
    2) формирует готовый follow-up вопрос на завтра.
    """
    fallback_summary = " ".join(raw_plan_text.split())[:140].strip() or "твой план"
    fallback_followup = (
        f"Вчера ты планировал: {fallback_summary}\n"
        "Как у тебя получилось это реализовать сегодня?"
    )

    if _client is None:
        return fallback_summary, fallback_followup

    prompt = (
        "Ниже сообщение пользователя с планами на завтра.\n"
        "Сделай ДВА результата и верни строго JSON-объект.\n\n"
        "Требования:\n"
        "1) plan_summary: 1-3 ключевых действия, кратко, без воды.\n"
        "2) followup_message: одно готовое сообщение, которое бот отправит завтра.\n"
        "   Формат followup_message:\n"
        "   - в начале фраза вида 'Вчера ты планировал: ...'\n"
        "   - в конце вопрос о том, как прошла реализация планов.\n"
        "   - дружелюбный тон, коротко.\n\n"
        "Верни ТОЛЬКО JSON такого вида:\n"
        '{"plan_summary":"...","followup_message":"..."}\n\n'
        f"Текст пользователя:\n{raw_plan_text}"
    )

    try:
        response = await _client.chat.completions.create(
            model=_model,
            messages=[
                {"role": "system", "content": "Ты пишешь только валидный JSON без пояснений."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=250,
        )
        content = (response.choices[0].message.content or "").strip()
        data = json.loads(content)
        summary = str(data.get("plan_summary", "")).strip()
        followup = str(data.get("followup_message", "")).strip()
        if not summary or not followup:
            return fallback_summary, fallback_followup
        return summary[:240], followup[:700]
    except Exception:
        logger.exception("Ошибка генерации plan summary/followup для user_id=%s", user_id)
        return fallback_summary, fallback_followup


async def generate_followup_reaction(user_id: int, followup_answer_text: str) -> str:
    """
    Один вызов LLM:
    - короткая реакция на то, как прошли планы (поддержка/похвала)
    - в конце обязательно вопрос про планы на завтра.
    """
    fallback = (
        "Спасибо, что поделился. Это полезно, чтобы видеть свой прогресс.\n\n"
        "Кстати, какие планы на завтра?"
    )
    if _client is None:
        return fallback

    prompt = (
        "Пользователь ответил, как прошла реализация планов.\n"
        "Сформируй короткий человеческий ответ на русском (2-4 предложения):\n"
        "- если получилось, похвали;\n"
        "- если не получилось, поддержи без давления;\n"
        "- в конце обязательно отдельной фразой спроси: 'Кстати, какие планы на завтра?'\n"
        "- без морализаторства и без пунктов.\n\n"
        f"Ответ пользователя:\n{followup_answer_text}"
    )
    try:
        response = await _client.chat.completions.create(
            model=_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=180,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            return fallback
        if "Кстати, какие планы на завтра?" not in text:
            text = f"{text}\n\nКстати, какие планы на завтра?"
        return text
    except Exception:
        logger.exception("Ошибка генерации followup reaction для user_id=%s", user_id)
        return fallback


async def convert_mood_text_to_score(user_id: int, mood_text: str) -> int | None:
    """
    Используется только если пользователь не прислал число 1..10.
    Модель должна вернуть ТОЛЬКО число от 1 до 10.
    """
    if _client is None:
        return None

    prompt = (
        "Преобразуй текстовую оценку дня в число от 1 до 10.\n"
        "Верни строго ОДНО число (1..10) без слов и знаков.\n\n"
        f"Текст пользователя: {mood_text}"
    )
    try:
        response = await _client.chat.completions.create(
            model=_model,
            messages=[
                {"role": "system", "content": "Ответ: только число 1..10."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=5,
        )
        content = (response.choices[0].message.content or "").strip()
        score = int("".join(ch for ch in content if ch.isdigit()) or "0")
        if 1 <= score <= 10:
            return score
        return None
    except Exception:
        logger.exception("Ошибка convert_mood_text_to_score для user_id=%s", user_id)
        return None


async def generate_plans_today_reaction_and_ask_tomorrow(
    user_id: int,
    plans_today_text: str,
    today_plan_summary: str | None,
) -> str:
    """
    Реакция на "как прошли планы" + в конце спросить планы на завтра.
    """
    fallback = "Спасибо, что поделился.\n\nКакие планы на завтра?"
    if _client is None:
        return fallback

    plan_part = f"Планы на сегодня: {today_plan_summary}\n" if today_plan_summary else ""
    prompt = (
        "Пользователь рассказал, как прошли его планы на сегодня.\n"
        "Сформируй короткий человеческий ответ на русском (2-4 предложения):\n"
        "- если получилось, похвали;\n"
        "- если не получилось, поддержи;\n"
        "- в конце ОБЯЗАТЕЛЬНО отдельной строкой спроси: 'Какие планы на завтра?'\n\n"
        f"{plan_part}"
        f"Ответ пользователя:\n{plans_today_text}"
    )
    try:
        response = await _client.chat.completions.create(
            model=_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=200,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            return fallback
        if "Какие планы на завтра?" not in text:
            text = f"{text}\n\nКакие планы на завтра?"
        return text
    except Exception:
        logger.exception("Ошибка generate_plans_today_reaction для user_id=%s", user_id)
        return fallback
