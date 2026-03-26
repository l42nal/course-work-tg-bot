"""
Главный файл Telegram-бота для отслеживания эмоционального состояния пользователя.
"""

import asyncio
import logging
import os
from typing import Set

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from dotenv import load_dotenv

from .llm import get_response, init_llm
from .scheduler import daily_question_scheduler
from .db.session import init_engine, load_known_user_ids, ping_db, shutdown_engine, upsert_user

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

    try:
        asyncio.create_task(daily_question_scheduler(bot, known_users))
        await dp.start_polling(bot)
    finally:
        await shutdown_engine()


if __name__ == "__main__":
    asyncio.run(main())

