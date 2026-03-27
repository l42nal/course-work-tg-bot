from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from ..db import crud

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduledMessage:
    id: uuid.UUID
    telegram_user_id: int
    text: str
    send_at: datetime
    status: str
    kind: str


class SchedulerService:
    """
    Минимальный сервис планирования задач.

    Источник истины: таблица `future_messages` в БД.
    APScheduler используется только как in-process исполнитель.
    """

    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        self._scheduler = AsyncIOScheduler(timezone=timezone.utc)

    async def start(self) -> None:
        self._scheduler.start()

    async def shutdown(self) -> None:
        # APScheduler async shutdown is sync method in most versions.
        try:
            self._scheduler.shutdown(wait=False)
        except Exception:
            logger.exception("Failed to shutdown scheduler")

    async def restore_pending_messages(self) -> int:
        """
        Загружает из БД все сообщения со статусом `scheduled`
        и регистрирует их в APScheduler заново.
        """
        pending = await crud.list_pending_future_messages()
        for msg in pending:
            self._register_message_job(msg.id, msg.send_at)
        logger.info("Restored %s scheduled messages", len(pending))
        return len(pending)

    async def schedule_message(
        self,
        telegram_user_id: int,
        text: str,
        send_at: datetime,
        message_kind: str = "generic",
    ) -> uuid.UUID:
        """
        Публичный API: сохраняет сообщение в БД и регистрирует выполнение в APScheduler.
        """
        send_at_utc = _ensure_aware_utc(send_at)
        msg_id = await crud.create_future_message(
            telegram_user_id=telegram_user_id,
            message_text=text,
            scheduled_for=send_at_utc,
            message_kind=message_kind,
        )
        self._register_message_job(msg_id, send_at_utc)
        return msg_id

    def _register_message_job(self, message_id: uuid.UUID, send_at: datetime) -> None:
        send_at_utc = _ensure_aware_utc(send_at)
        job_id = str(message_id)

        # replace_existing=True важен для восстановления после перезапуска:
        # если кто-то повторно вызвал restore(), не будет дублей джобов.
        self._scheduler.add_job(
            func=self._execute_scheduled_message,
            trigger=DateTrigger(run_date=send_at_utc),
            args=[message_id],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=60 * 60,  # 1 час: если бот был выключен, отправим при старте
        )

    async def _execute_scheduled_message(self, message_id: uuid.UUID) -> None:
        """
        Исполнитель джобы.
        Берём данные из БД, проверяем статус, отправляем, помечаем `sent`.
        """
        msg = await crud.get_future_message_for_sending(message_id)
        if msg is None:
            return
        if msg.status != "scheduled":
            return

        try:
            await self._bot.send_message(chat_id=msg.telegram_user_id, text=msg.text)
        except Exception:
            logger.exception("Failed to send scheduled message id=%s", message_id)
            return

        if msg.kind == "plans_followup_question":
            await crud.set_plan_mode(msg.telegram_user_id, "awaiting_followup")

        await crud.mark_future_message_sent(message_id)


_service: Optional[SchedulerService] = None


def init_scheduler(bot: Bot) -> SchedulerService:
    """
    Инициализирует singleton-сервис, чтобы из кода можно было вызывать schedule_message().
    Вызывать один раз при старте приложения.
    """
    global _service
    _service = SchedulerService(bot)
    return _service


def get_scheduler() -> SchedulerService:
    if _service is None:
        raise RuntimeError("Scheduler is not initialized. Call init_scheduler(bot) on startup.")
    return _service


async def schedule_message(
    telegram_user_id: int,
    text: str,
    send_at: datetime,
    message_kind: str = "generic",
) -> uuid.UUID:
    """
    Удобная функция под требуемый интерфейс:
    schedule_message(user_id, text, send_at)
    """
    return await get_scheduler().schedule_message(
        telegram_user_id=telegram_user_id,
        text=text,
        send_at=send_at,
        message_kind=message_kind,
    )


def _ensure_aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        # Минимальное и предсказуемое поведение: если пришёл naive-datetime,
        # считаем, что это UTC.
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

