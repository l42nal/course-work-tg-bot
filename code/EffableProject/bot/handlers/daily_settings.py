from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..db import crud
from ..services.scheduler_service import get_scheduler

logger = logging.getLogger(__name__)

@dataclass
class StartSettingsDraft:
    step: str  # awaiting_tz | awaiting_hour
    timezone_offset_hours: Optional[int] = None


_drafts: Dict[int, StartSettingsDraft] = {}


def _tz_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for offset in range(24):
        label = f"UTC + {offset}"
        if offset == 3:
            label += " (MSK)"
        row.append(InlineKeyboardButton(text=label, callback_data=f"start_tz:{offset}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _hour_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for hour in range(24):
        label = f"{hour:02d}:00"
        row.append(InlineKeyboardButton(text=label, callback_data=f"start_hour:{hour}"))
        if len(row) == 6:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def start_daily_settings_flow(message: Message, telegram_user_id: int) -> None:
    _drafts[telegram_user_id] = StartSettingsDraft(step="awaiting_tz")
    await message.answer("Выбери свой часовой пояс", reply_markup=_tz_keyboard())


async def handle_daily_settings_callback(callback: CallbackQuery) -> None:
    data = callback.data or ""
    telegram_user_id = callback.from_user.id

    if data.startswith("start_tz:"):
        try:
            offset = int(data.split("start_tz:", 1)[1])
        except Exception:
            await callback.answer("Не понял выбор. Попробуй ещё раз.", show_alert=False)
            return

        if offset < 0 or offset > 23:
            await callback.answer("Некорректный UTC offset.", show_alert=False)
            return

        _drafts[telegram_user_id] = StartSettingsDraft(step="awaiting_hour", timezone_offset_hours=offset)
        await callback.answer("Ок", show_alert=False)
        if callback.message:
            await callback.message.answer("Теперь выбери время ежедневного опроса:", reply_markup=_hour_keyboard())
        return

    if data.startswith("start_hour:"):
        draft = _drafts.get(telegram_user_id)
        if draft is None or draft.step != "awaiting_hour" or draft.timezone_offset_hours is None:
            await callback.answer("Сначала выбери часовой пояс через /start.", show_alert=False)
            return

        try:
            hour_local = int(data.split("start_hour:", 1)[1])
        except Exception:
            await callback.answer("Не понял время. Попробуй ещё раз.", show_alert=False)
            return

        if hour_local < 0 or hour_local > 23:
            await callback.answer("Некорректный час.", show_alert=False)
            return

        await crud.upsert_user_daily_settings(
            telegram_user_id,
            timezone_offset_hours=int(draft.timezone_offset_hours),
            daily_checkin_hour_local=int(hour_local),
        )

        # Регистрируем/обновляем cron job сразу (без перезапуска).
        get_scheduler().register_user_daily_checkin_job(
            telegram_user_id=telegram_user_id,
            timezone_offset_hours=int(draft.timezone_offset_hours),
            daily_checkin_hour_local=int(hour_local),
        )

        logger.info(
            "Daily settings saved telegram_user_id=%s tz_offset=%s local_hour=%s",
            telegram_user_id,
            draft.timezone_offset_hours,
            hour_local,
        )

        _drafts.pop(telegram_user_id, None)
        await callback.answer("Сохранено", show_alert=False)
        if callback.message:
            await callback.message.answer(
                f"Готово! Буду спрашивать каждый день в {hour_local:02d}:00 (UTC + {draft.timezone_offset_hours})."
            )
        return

