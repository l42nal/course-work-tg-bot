"""
Главный файл Telegram-бота для отслеживания эмоционального состояния пользователя.
"""

import asyncio
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Set

from aiogram import Bot, Dispatcher
from aiogram.types import FSInputFile, Message
from dotenv import load_dotenv

from .db import crud
from .llm import get_response, init_llm
from .handlers.commands import try_handle_command
from .services.checkin_service import handle_checkin_and_plans_flow
from .services.scheduler_service import init_scheduler, schedule_message
from .db.session import (
    ensure_users_telegram_id_bigint,
    init_engine,
    load_known_user_ids,
    ping_db,
    shutdown_engine,
    upsert_user,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

known_users: Set[int] = set()

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

    today = datetime.now().date()

    if await try_handle_command(message, telegram_user_id=user_id, known_users=known_users, today=today):
        return

    checkin = await handle_checkin_and_plans_flow(
        telegram_user_id=user_id,
        user_text=user_text,
        today=today,
    )
    if checkin.handled:
        if checkin.schedule_followup and checkin.followup_text and checkin.followup_send_at:
            await schedule_message(
                telegram_user_id=user_id,
                text=checkin.followup_text,
                send_at=checkin.followup_send_at,
                message_kind=checkin.followup_kind,
            )
        if checkin.reply_text:
            await message.answer(checkin.reply_text)
        return

    reply = await get_response(user_id, user_text)
    await message.answer(reply)


async def main() -> None:
    """Точка входа: инициализация бота, LLM-клиента, БД и планировщика."""
    init_llm()
    init_engine()

    await ping_db()
    await ensure_users_telegram_id_bigint()

    # Загружаем ранее известных пользователей из БД, чтобы планировщик
    # работал после перезапуска процесса.
    known_users.update(await load_known_user_ids())

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(handle_any_message)

    scheduler_service = init_scheduler(bot)
    try:
        await scheduler_service.start()
        scheduler_service.register_daily_checkin_job()
        await scheduler_service.restore_pending_messages()
        await dp.start_polling(bot)
    finally:
        await scheduler_service.shutdown()
        await shutdown_engine()


if __name__ == "__main__":
    asyncio.run(main())

