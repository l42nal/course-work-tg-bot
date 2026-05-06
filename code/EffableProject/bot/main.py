"""
Главный файл Telegram-бота для отслеживания эмоционального состояния пользователя.
"""

import asyncio
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
#подсказка типов
from typing import Set

#импорты aiogram, dispatcher - регистриует обработчики сообщений и команд
from aiogram import Bot, Dispatcher
from aiogram.types import FSInputFile, Message
from dotenv import load_dotenv

from .db import crud
from .llm import get_response, init_llm
from .handlers.commands import try_handle_command
from .handlers.remind import handle_remind_callback, try_handle_remind_message
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

load_dotenv() #загружаем переменные окружения из файла .env

logging.basicConfig( #настраиваем логирование
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

known_users: Set[int] = set() #множество для хранения известных пользователей

async def handle_any_message(message: Message) -> None: #асинхронная функция для обработки любого входящего сообщения
    """
    Обработчик любого входящего сообщения.

    Добавляет пользователя в known_users и отправляет его текст
    в LLM, возвращая ответ «мягкого психолога».
    """
    user_id = message.from_user.id

    await upsert_user( #добавляем пользователя в базу данных
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

    # Если пользователь в сценарии /remind — перехватываем сообщение здесь,
    # чтобы оно не ушло в другие сценарии (check-in/LLM).
    if await try_handle_remind_message(message, telegram_user_id=user_id):
        return

    if await try_handle_command(message, telegram_user_id=user_id, known_users=known_users, today=today):
        return

    checkin = await handle_checkin_and_plans_flow( #обработка daily check-in и планов
        telegram_user_id=user_id,
        user_text=user_text,
        today=today,
    )
    if checkin.handled: #если сообщение обработано (то есть это daily check-in или планы)
        if checkin.schedule_followup and checkin.followup_text and checkin.followup_send_at: 
            await schedule_message( #планируем отправку сообщения через scheduler_service
                telegram_user_id=user_id,
                text=checkin.followup_text,
                send_at=checkin.followup_send_at,
                message_kind=checkin.followup_kind,
            )
        if checkin.reply_text: #отправляем ответ пользователю
            await message.answer(checkin.reply_text)
        return

    reply = await get_response(user_id, user_text) #получаем ответ от LLM
    await message.answer(reply)


async def main() -> None: #точка входа: инициализация бота, LLM-клиента, БД и планировщика
    """Точка входа: инициализация бота, LLM-клиента, БД и планировщика."""
    init_llm()
    init_engine() #инициализируем движок БД

    await ping_db()
    await ensure_users_telegram_id_bigint() #убеждаемся, что ID пользователей в базе данных BIGINT (связано с возникавшим багом)

    # Загружаем ранее известных пользователей из БД, чтобы планировщик
    # работал после перезапуска процесса.
    known_users.update(await load_known_user_ids())

    bot = Bot(token=BOT_TOKEN) #инициализируем бота
    dp = Dispatcher() #инициализируем диспетчер

    dp.message.register(handle_any_message) #регистрируем обработчик любого входящего сообщения
    dp.callback_query.register(handle_remind_callback)

    scheduler_service = init_scheduler(bot) #инициализируем планировщик
    try:
        await scheduler_service.start()
        scheduler_service.register_daily_checkin_job() #регистрируем ежедневный check-in
        await scheduler_service.restore_pending_messages()
        await dp.start_polling(bot) #запускаем бота
    finally:
        await scheduler_service.shutdown() #останавливаем планировщик
        await shutdown_engine() #останавливаем движок БД


if __name__ == "__main__":  
    asyncio.run(main())

