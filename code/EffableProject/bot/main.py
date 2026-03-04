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
    known_users.add(user_id)

    user_text = message.text or ""
    if not user_text.strip():
        return

    reply = await get_response(user_id, user_text)
    await message.answer(reply)


async def main() -> None:
    """Точка входа: инициализация бота, LLM-клиента и планировщика."""
    init_llm()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(handle_any_message)

    asyncio.create_task(daily_question_scheduler(bot, known_users))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

