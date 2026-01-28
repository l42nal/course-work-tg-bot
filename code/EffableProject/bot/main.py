"""
Главный файл Telegram-бота для отслеживания эмоционального состояния пользователя.
"""

import asyncio
from typing import Set

from aiogram import Bot, Dispatcher
from aiogram.types import Message

from .config import BOT_TOKEN
from .scheduler import daily_question_scheduler


# Простое множество для хранения ID пользователей,
# которые хотя бы раз написали боту.
#
# Важно: при перезапуске бота это множество очищается,
# так как оно хранится только в памяти.
known_users: Set[int] = set()


async def handle_any_message(message: Message) -> None:
    """
    Обработчик любого входящего сообщения.

    Здесь мы:
    - добавляем пользователя в множество known_users
    - отправляем простой ответ, подтверждающий получение сообщения
    """
    user_id = message.from_user.id
    known_users.add(user_id)

    # Простой ответ пользователю, чтобы он понимал, что бот работает.
    await message.answer(
        "Спасибо за сообщение! Я учту твой ответ.\n"
        "Я буду спрашивать тебя каждый день в 21:00: как прошел твой день."
    )


async def main() -> None:
    """
    Точка входа в приложение.

    Здесь мы:
    - создаем объекты Bot и Dispatcher
    - регистрируем обработчик сообщений
    - запускаем фоновую задачу с планировщиком ежедневных сообщений
    - запускаем бота (долгий опрос Telegram)
    """
    # Создаем объект бота с токеном из конфигурации.
    bot = Bot(token=BOT_TOKEN)

    # Dispatcher в aiogram отвечает за маршрутизацию сообщений к обработчикам.
    dp = Dispatcher()

    # Регистрируем обработчик, который будет реагировать на любые сообщения.
    dp.message.register(handle_any_message)

    # Запускаем фоновую задачу с планировщиком.
    # Она будет работать параллельно с основным опросом Telegram.
    asyncio.create_task(daily_question_scheduler(bot, known_users))

    # Запускаем бесконечный опрос Telegram-серверов
    # для получения обновлений (сообщений).
    await dp.start_polling(bot)


if __name__ == "__main__":
    # Запускаем асинхронную функцию main.
    asyncio.run(main())

