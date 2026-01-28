"""
Модуль с простой ежедневной задачей (планировщиком).

Задача:
- раз в сутки (в 21:00 по локальному времени сервера)
  отправлять всем известным пользователям сообщение
  "Привет! Как прошел твой день?"
"""

import asyncio
from datetime import datetime, timedelta
from typing import Set

from aiogram import Bot


DAILY_MESSAGE_TEXT = "Привет! Как прошел твой день?"


def _seconds_until_next_21() -> float:
    """
    Вспомогательная функция.

    Считает количество секунд до следующего наступления 21:00
    по локальному времени сервера.
    """
    now = datetime.now()

    # Время сегодня в 21:00
    next_run = now.replace(hour=21, minute=0, second=0, microsecond=0)

    # Если 21:00 уже прошло, берем следующее число (завтра)
    if next_run <= now:
        next_run = next_run + timedelta(days=1)

    delta: timedelta = next_run - now
    return delta.total_seconds()


async def daily_question_scheduler(bot: Bot, user_ids: Set[int]) -> None:
    """
    Простой планировщик для ежедневной рассылки.

    Параметры:
    - bot: объект бота aiogram, через который отправляются сообщения
    - user_ids: множество ID пользователей, которым нужно написать

    Важно:
    - функция работает бесконечно в цикле
    - ее нужно запускать как фоновую задачу (create_task)
    """
    while True:
        # Сколько ждать до следующей отправки (до ближайших 21:00)
        seconds_to_sleep = _seconds_until_next_21()

        # Для минимальной защиты от странных ситуаций
        if seconds_to_sleep < 0:
            seconds_to_sleep = 0

        # Ждем до нужного времени
        await asyncio.sleep(seconds_to_sleep)

        # После ожидания пытаемся отправить сообщение всем пользователям
        # На этом этапе user_ids уже может содержать новых пользователей,
        # которые написали боту после запуска.
        for user_id in list(user_ids):
            try:
                await bot.send_message(chat_id=user_id, text=DAILY_MESSAGE_TEXT)
            except Exception:
                # Для простоты в первой версии не обрабатываем ошибки подробно.
                # В будущем можно добавить логирование или удаление
                # недоступных пользователей.
                continue

        # После рассылки не выходим из цикла, а снова считаем время
        # до следующего дня (следующих 21:00).

