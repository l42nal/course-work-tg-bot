"""
Модуль с простой ежедневной задачей (планировщиком).

Задача:
- раз в сутки (в 21:00 по локальному времени сервера)
  отправлять всем известным пользователям сообщение
  "Какие у тебя планы на завтра?"
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Set

from aiogram import Bot

from sqlalchemy import select

from .db import crud
from .db.models import DailyCheckIn, User
from .db.session import session_scope


DAILY_MESSAGE_TEXT = "Какие у тебя планы на завтра?"


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

        checkin_date = datetime.now().date()

        # После ожидания пытаемся отправить сообщение всем пользователям
        # На этом этапе user_ids уже может содержать новых пользователей,
        # которые написали боту после запуска.
        for user_id in list(user_ids):
            # Минимальная защита от дублей: если уже отправляли на эту дату,
            # повторно не пишем.
            async with session_scope() as session:
                user = (
                    await session.execute(
                        select(User).where(User.telegram_user_id == user_id)
                    )
                ).scalar_one_or_none()
                if user is None:
                    continue

                daily = (
                    await session.execute(
                        select(DailyCheckIn).where(
                            DailyCheckIn.user_id == user.id,
                            DailyCheckIn.checkin_date == checkin_date,
                        )
                    )
                ).scalar_one_or_none()

                if daily is not None and daily.status in {"sent", "answered", "graded"}:
                    continue

                if daily is None:
                    daily = DailyCheckIn(
                        user_id=user.id,
                        checkin_date=checkin_date,
                        status="scheduled",
                        question_text=DAILY_MESSAGE_TEXT,
                    )
                    session.add(daily)
                    await session.commit()

            # Отправляем вопрос пользователю. Если отправка успешна — отмечаем `sent`.
            try:
                await bot.send_message(chat_id=user_id, text=DAILY_MESSAGE_TEXT)
                await crud.set_plan_mode(user_id, "awaiting_plan")
            except Exception:
                continue

            async with session_scope() as session:
                user = (
                    await session.execute(
                        select(User).where(User.telegram_user_id == user_id)
                    )
                ).scalar_one_or_none()
                if user is None:
                    continue

                daily = (
                    await session.execute(
                        select(DailyCheckIn).where(
                            DailyCheckIn.user_id == user.id,
                            DailyCheckIn.checkin_date == checkin_date,
                        )
                    )
                ).scalar_one_or_none()
                if daily is None:
                    continue

                daily.status = "sent"
                daily.question_sent_at = datetime.now(timezone.utc)
                await session.commit()

        # После рассылки не выходим из цикла, а снова считаем время
        # до следующего дня (следующих 21:00).

