from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from ..db import crud
from .checkin_service import DAILY_MESSAGE_TEXT

logger = logging.getLogger(__name__)


@dataclass(frozen=True) #класс для хранения информации о запланированном сообщении
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

    def __init__(self, bot: Bot) -> None: #инициализирует сервис планирования
        self._bot = bot
        # Сервер бота живёт в UTC+0 (по условию). Работаем в UTC для предсказуемости.
        self._wall_clock_tz = timezone.utc
        self._scheduler = AsyncIOScheduler(timezone=self._wall_clock_tz)

    async def start(self) -> None: #запускает планировщик
        self._scheduler.start()

    async def register_daily_checkin_jobs(self) -> int:
        """
        Регистрирует персональные ежедневные check-in задачи по настройкам пользователей.
        Возвращает количество зарегистрированных задач.
        """
        settings = await crud.list_user_daily_settings()
        for telegram_user_id, tz_offset_hours, daily_hour_local in settings:
            self.register_user_daily_checkin_job(
                telegram_user_id=telegram_user_id,
                timezone_offset_hours=tz_offset_hours,
                daily_checkin_hour_local=daily_hour_local,
            )
        logger.info("Registered %s per-user daily check-in jobs", len(settings))
        return len(settings)

    def register_user_daily_checkin_job(
        self,
        *,
        telegram_user_id: int,
        timezone_offset_hours: int,
        daily_checkin_hour_local: int,
    ) -> None:
        """
        Регистрирует/обновляет cron-задачу для конкретного пользователя.

        Серверное время: UTC.
        Пользователь выбирает:
        - timezone_offset_hours (0..23)
        - daily_checkin_hour_local (0..23) — "локальный" час пользователя

        Тогда серверный час (utc_hour) вычисляется так:
        utc_hour = (local_hour - offset) mod 24
        """
        utc_hour = (int(daily_checkin_hour_local) - int(timezone_offset_hours)) % 24
        job_id = f"daily_checkin_user:{telegram_user_id}"

        job = self._scheduler.add_job(
            func=self._run_daily_checkin_for_user,
            trigger=CronTrigger(hour=utc_hour, minute=0, timezone=timezone.utc),
            args=[telegram_user_id, int(timezone_offset_hours)],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=6 * 60 * 60,  # 6 часов
        )
        logger.info(
            "Daily check-in job registered telegram_user_id=%s job_id=%s utc_hour=%s local_hour=%s tz_offset=%s next_run=%s",
            telegram_user_id,
            job_id,
            utc_hour,
            daily_checkin_hour_local,
            timezone_offset_hours,
            job.next_run_time,
        )

    async def shutdown(self) -> None: #останавливает планировщик
        # APScheduler async shutdown is sync method in most versions.
        try:
            self._scheduler.shutdown(wait=False)
        except Exception:
            logger.exception("Failed to shutdown scheduler")

    async def restore_pending_messages(self) -> int: #восстанавливает запланированные сообщения
        """
        Загружает из БД все сообщения со статусом `scheduled`
        и регистрирует их в APScheduler заново.
        """
        pending = await crud.list_pending_future_messages()
        for msg in pending: #регистрирует каждое запланированное сообщение в планировщик
            self._register_message_job(msg.id, msg.send_at)
        logger.info("Restored %s scheduled messages", len(pending))
        return len(pending)

    async def schedule_message( #планирует отправку сообщения
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
        logger.info(
            "Scheduled message created id=%s telegram_user_id=%s kind=%s send_at_utc=%s",
            msg_id,
            telegram_user_id,
            message_kind,
            send_at_utc.isoformat(),
        )
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
        logger.info("Scheduler job registered id=%s run_date_utc=%s", job_id, send_at_utc.isoformat())

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
        logger.info("Scheduled message sent id=%s telegram_user_id=%s", message_id, msg.telegram_user_id)

    async def _run_daily_checkin_for_user(self, telegram_user_id: int, tz_offset_hours: int) -> None:
        """
        Исполнитель check-in для конкретного пользователя.

        `checkin_date` считаем как "локальная дата пользователя" на момент отправки:
        utc_now + tz_offset_hours.
        """
        checkin_date = (datetime.now(timezone.utc) + timedelta(hours=int(tz_offset_hours))).date()

        daily = await crud.get_daily_checkin(telegram_user_id, checkin_date)
        if daily is not None and daily.status in {"sent", "answered", "graded"}:
            return

        await crud.ensure_daily_checkin_exists(
            telegram_user_id,
            checkin_date,
            question_text=DAILY_MESSAGE_TEXT,
        )

        try:
            await self._bot.send_message(chat_id=telegram_user_id, text=DAILY_MESSAGE_TEXT)
        except Exception:
            logger.exception(
                "Failed to send daily check-in to telegram_user_id=%s",
                telegram_user_id,
            )
            return

        await crud.set_daily_checkin_status(telegram_user_id, checkin_date, "sent")


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

