"""
Главный файл Telegram-бота для отслеживания эмоционального состояния пользователя.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Set

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from dotenv import load_dotenv

from .db import crud
from .llm import get_response, init_llm
from .scheduler import daily_question_scheduler
from .services.scheduler_service import init_scheduler, schedule_message
from .db.session import init_engine, load_known_user_ids, ping_db, shutdown_engine, upsert_user

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

known_users: Set[int] = set()


def _summarize_plan_text(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= 140:
        return normalized
    return normalized[:137].rstrip() + "..."


def _build_followup_comment(answer_text: str) -> str:
    text = answer_text.lower()
    positive_markers = ("получ", "сделал", "успел", "выполнил", "удал", "класс", "хорош")
    negative_markers = ("не успел", "не смог", "не получилось", "провал", "тяжело", "сложно")

    if any(marker in text for marker in negative_markers):
        return "Ничего страшного, так тоже бывает. Главное, что ты двигаешься дальше."
    if any(marker in text for marker in positive_markers):
        return "Здорово, что получилось реализовать задуманное."
    return "Спасибо, что поделился. Это полезно, чтобы видеть свой прогресс."


def _build_followup_question(plan_summary: str) -> str:
    return (
        f"Вчера ты планировал: {plan_summary}\n"
        "Как у тебя получилось это реализовать сегодня?"
    )


async def handle_any_message(message: Message) -> None:
    """
    Обработчик любого входящего сообщения.

    Добавляет пользователя в known_users и отправляет его текст
    в LLM, возвращая ответ «мягкого психолога».
    """
    user_id = message.from_user.id
    await upsert_user(
        telegram_user_id=user_id,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        username=message.from_user.username,
        language_code=message.from_user.language_code,
    )
    known_users.add(user_id)

    user_text = message.text or ""
    if not user_text.strip():
        return

    # Минимальный ручной тест планировщика (не UX фича).
    # Напиши боту: /test_schedule_1m
    if user_text.strip() == "/test_schedule_1m":
        send_at = datetime.now(timezone.utc) + timedelta(minutes=1)
        await schedule_message(
            telegram_user_id=user_id,
            text="Тест: сообщение, запланированное на +1 минуту.",
            send_at=send_at,
        )
        await message.answer("Ок, запланировал сообщение на +1 минуту.")
        return

    # Debug: принудительно запускает "вечерний" вопрос про планы.
    if user_text.strip() == "/debug_evening_now":
        await crud.set_plan_mode(user_id, "awaiting_plan")
        await message.answer("Какие у тебя планы на завтра?")
        return

    # Debug: принудительно запускает follow-up вопрос по последнему плану.
    if user_text.strip() == "/debug_followup_now":
        state = await crud.get_plan_state(user_id)
        if not state.last_plan_summary:
            await message.answer("Пока нет сохраненного плана. Сначала отправь /debug_evening_now и ответь на вопрос.")
            return
        await crud.set_plan_mode(user_id, "awaiting_followup")
        await message.answer(_build_followup_question(state.last_plan_summary))
        return

    state = await crud.get_plan_state(user_id)

    if state.mode == "awaiting_plan":
        today = datetime.now().date()
        plan_for_date = today + timedelta(days=1)
        summary = _summarize_plan_text(user_text)

        await crud.save_latest_plan(
            telegram_user_id=user_id,
            for_date=plan_for_date,
            raw_text=user_text,
            summary_text=summary,
        )
        await crud.set_plan_mode(user_id, "normal")

        followup_text = _build_followup_question(summary)
        await schedule_message(
            telegram_user_id=user_id,
            text=followup_text,
            send_at=datetime.now(timezone.utc) + timedelta(days=1),
            message_kind="plans_followup_question",
        )

        await message.answer("Запомнил. Завтра в это же время спрошу, как это получилось.")
        return

    if state.mode == "awaiting_followup":
        comment = _build_followup_comment(user_text)
        await crud.set_plan_mode(user_id, "awaiting_plan")
        await message.answer(f"{comment}\n\nКстати, какие планы на завтра?")
        return

    reply = await get_response(user_id, user_text)
    await message.answer(reply)


async def main() -> None:
    """Точка входа: инициализация бота, LLM-клиента, БД и планировщика."""
    init_llm()
    init_engine()

    await ping_db()

    # Загружаем ранее известных пользователей из БД, чтобы планировщик
    # работал после перезапуска процесса.
    known_users.update(await load_known_user_ids())

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(handle_any_message)

    scheduler_service = init_scheduler(bot)
    try:
        await scheduler_service.start()
        await scheduler_service.restore_pending_messages()
        asyncio.create_task(daily_question_scheduler(bot, known_users))
        await dp.start_polling(bot)
    finally:
        await scheduler_service.shutdown()
        await shutdown_engine()


if __name__ == "__main__":
    asyncio.run(main())

