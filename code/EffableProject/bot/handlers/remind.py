from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..services.scheduler_service import schedule_message

logger = logging.getLogger(__name__)


@dataclass
class RemindDraft:
    step: str  # awaiting_text | awaiting_interval
    text: Optional[str] = None
    created_at_utc: datetime = datetime.now(timezone.utc)


_drafts: Dict[int, RemindDraft] = {}


def _interval_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="1 минута", callback_data="remind:1m"),
            InlineKeyboardButton(text="1 день", callback_data="remind:1d"),
        ],
        [
            InlineKeyboardButton(text="1 неделя", callback_data="remind:1w"),
            InlineKeyboardButton(text="1 месяц", callback_data="remind:1mo"),
        ],
        [
            InlineKeyboardButton(text="6 месяцев", callback_data="remind:6mo"),
            InlineKeyboardButton(text="1 год", callback_data="remind:1y"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _format_future_text(user_text: str) -> str:
    return f"Тебе сообщение из прошлого 😊\n\n{user_text}"


def _parse_interval(code: str) -> timedelta | None:
    # Без внешних зависимостей (dateutil) используем предсказуемые приближения для месяца/года.
    mapping: dict[str, timedelta] = {
        "1m": timedelta(minutes=1),
        "1d": timedelta(days=1),
        "1w": timedelta(weeks=1),
        "1mo": timedelta(days=30),
        "6mo": timedelta(days=30 * 6),
        "1y": timedelta(days=365),
    }
    return mapping.get(code)


def _interval_label(code: str) -> str:
    labels = {
        "1m": "1 минуту",
        "1d": "1 день",
        "1w": "1 неделю",
        "1mo": "1 месяц",
        "6mo": "6 месяцев",
        "1y": "1 год",
    }
    return labels.get(code, code)

def _format_msk_datetime(dt_utc: datetime) -> str:
    """
    Для вывода пользователю отображаем время как UTC+3 (MSK),
    просто прибавляя 3 часа к UTC-времени.
    Пример: 2026-05-06 12:34 -> 2026-05-06 15:34
    """
    dt_msk = dt_utc + timedelta(hours=3)
    return dt_msk.strftime("%Y-%m-%d %H:%M")


async def start_remind_flow(message: Message, telegram_user_id: int) -> None:
    _drafts[telegram_user_id] = RemindDraft(step="awaiting_text")
    await message.answer("Напиши сообщение, которое хочешь отправить себе в будущее")


async def try_handle_remind_message(message: Message, telegram_user_id: int) -> bool:
    """
    Перехватывает текстовые сообщения, если пользователь находится в сценарии /remind.
    Возвращает True, если сообщение было обработано и дальше его обрабатывать не нужно.
    """
    draft = _drafts.get(telegram_user_id)
    if draft is None:
        return False

    user_text = (message.text or "").strip()
    if not user_text:
        await message.answer("Я жду текст сообщения. Пришли текст одним сообщением 🙂")
        return True

    # Если пользователь ушёл в другую команду — выходим из сценария /remind, не мешаем другим сценариям.
    if user_text.startswith("/"):
        _drafts.pop(telegram_user_id, None)
        return False

    if draft.step == "awaiting_text":
        draft.text = user_text
        draft.step = "awaiting_interval"
        await message.answer("Через сколько прислать тебе?")
        await message.answer("Выбери вариант:", reply_markup=_interval_keyboard())
        return True

    if draft.step == "awaiting_interval":
        await message.answer("Нажми одну из кнопок ниже:", reply_markup=_interval_keyboard())
        return True

    _drafts.pop(telegram_user_id, None)
    return False


async def handle_remind_callback(callback: CallbackQuery) -> None:
    if not callback.data or not callback.data.startswith("remind:"):
        return

    telegram_user_id = callback.from_user.id
    draft = _drafts.get(telegram_user_id)
    if draft is None or draft.step != "awaiting_interval" or not draft.text:
        await callback.answer("Сначала отправь /remind и введи текст сообщения.", show_alert=False)
        return

    code = callback.data.split("remind:", 1)[1]
    delta = _parse_interval(code)
    if delta is None:
        await callback.answer("Не понял интервал. Попробуй ещё раз.", show_alert=False)
        return

    send_at = datetime.now(timezone.utc) + delta
    scheduled_text = _format_future_text(draft.text)

    msg_id = await schedule_message(
        telegram_user_id=telegram_user_id,
        text=scheduled_text,
        send_at=send_at,
        message_kind="generic",
    )

    logger.info(
        "Remind scheduled: telegram_user_id=%s message_id=%s send_at_utc=%s interval=%s",
        telegram_user_id,
        msg_id,
        send_at.isoformat(),
        code,
    )

    _drafts.pop(telegram_user_id, None)

    await callback.answer("Готово!", show_alert=False)
    if callback.message:
        await callback.message.answer(
            f"Ок! Пришлю через {_interval_label(code)} (примерно в {_format_msk_datetime(send_at)})."
        )

