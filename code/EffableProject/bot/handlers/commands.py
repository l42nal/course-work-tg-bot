from __future__ import annotations

import logging
import os
import re

import tempfile
from datetime import date, datetime, timedelta, timezone
from typing import Set

from aiogram.types import FSInputFile, Message #импорты FSInputFile, Message - для работы с файлами и сообщениями

from ..db import crud
from ..services.export_service import build_user_export_payload, dumps_user_export
from ..services.mood_plot import MonthMoodPoint, build_month_mood_plot_png
from ..services.stats_service import format_mood_stats_text, get_user_mood_stats
from ..services.user_data_service import reset_user_data
from ..services.scheduler_service import schedule_message
from .remind import start_remind_flow

logger = logging.getLogger(__name__) #инициализируем логгер


async def try_handle_command( #асинхронная функция для обработки команд
    message: Message,
    *,
    telegram_user_id: int,
    known_users: Set[int],
    today: date,
) -> bool: #возвращает True, если команда была обработана и дальнейшая обработка не нужна
    """
    Минимальный роутер команд.

    Возвращает True, если команда была обработана и дальнейшая обработка не нужна.
    """
    user_text = (message.text or "").strip()
    if not user_text:
        return False

    # /start — начало диалога + справка по возможностям
    if user_text == "/start":
        await message.answer(
            "Привет! Я бот для отслеживания твоего эмоционального состояния.\n\n"
            "Помогаю отмечать настроение, а потом показываю статистику с графиком по отмеченным дням.\n\n"
            "А ещё каждый день спрашиваю, как прошёл день и что получилось из планов.\n\n"
            "Команды:\n"
            "/stat — статистика и график\n"
            "/export — выгрузить данные (JSON)\n"
            "/reset — удалить данные и начать заново\n"
            "/remind — отправить сообщение себе в будущее"
        )
        return True

    # /reset — полностью удалить данные пользователя из БД
    if user_text == "/reset":
        existed = await reset_user_data(telegram_user_id) #удаляем данные пользователя из БД
        known_users.discard(telegram_user_id) #удаляем пользователя из множества известных пользователей
        if existed:
            await message.answer("Все твои данные удалены. Можем начать заново 🙂")
        else:
            await message.answer("Похоже, у тебя ещё нет данных в базе. Можем начать заново 🙂")
        return True

    # /export — выгрузить все данные пользователя из БД в JSON
    if user_text == "/export":
        payload = await build_user_export_payload(telegram_user_id) #получаем данные пользователя из БД
        json_text = dumps_user_export(payload) #преобразуем данные в JSON

        fd, path = tempfile.mkstemp(prefix=f"export_{telegram_user_id}_", suffix=".json") #создаем временный файл для экспорта
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f: #открываем файл для записи
                f.write(json_text)
            await message.answer_document( #отправляем файл пользователю
                FSInputFile(path),
                caption="Твой экспорт данных (JSON).",
            )
        finally:
            try:
                os.remove(path) #удаляем временный файл
            except Exception:
                logger.exception("Failed to remove temp export file: %s", path)
        return True

    # /stat — статистика настроения + график текущего месяца
    if user_text == "/stat":
        stats = await get_user_mood_stats(telegram_user_id, today=today) #получаем статистику настроения пользователя
        await message.answer(format_mood_stats_text(stats)) #отправляем статистику пользователю

        if stats.total_days == 0:
            return True

        entries = await crud.list_mood_scores(telegram_user_id) #получаем оценки настроения пользователя из БД
        month_points = [
            MonthMoodPoint(day=e.day, score=e.score) #создаем объект MonthMoodPoint для каждой оценки настроения
            for e in entries
            if e.day.year == today.year and e.day.month == today.month
        ]

        png_path = build_month_mood_plot_png(month_points, year=today.year, month=today.month) #строим график настроения
        if png_path is None:
            await message.answer("За текущий месяц пока нет оценок — график построить не из чего.")
            return True

        try:
            await message.answer_photo(FSInputFile(png_path)) #отправляем график пользователю
        finally:
            try:
                os.remove(png_path) #удаляем временный график
            except Exception:
                logger.exception("Failed to remove temp plot file: %s", png_path)
        return True

    # /remind — диалог "сообщение себе в будущее"
    if user_text == "/remind":
        await start_remind_flow(message, telegram_user_id)
        return True

    # Минимальный ручной тест планировщика (для отладки).
    if user_text == "/test_schedule_1m":
        send_at = datetime.now(timezone.utc) + timedelta(minutes=1)
        await schedule_message(
            telegram_user_id=telegram_user_id,
            text="Тест: сообщение, запланированное на +1 минуту.",
            send_at=send_at,
        )
        await message.answer("Ок, запланировал сообщение на +1 минуту.")
        return True

    # Debug: добавить/перезаписать оценку за конкретный день (для отладки).
    if user_text.startswith("/debug_add_mood"):
        m = re.match(r"^/debug_add_mood\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2})\s*$", user_text)
        if not m:
            await message.answer("Формат: /debug_add_mood YYYY-MM-DD SCORE (0..10)")
            return True
        day_s, score_s = m.group(1), m.group(2)
        try:
            day = datetime.strptime(day_s, "%Y-%m-%d").date()
            score = int(score_s)
            if score < 0 or score > 10:
                raise ValueError("score out of range")
        except Exception:
            await message.answer("Не смог разобрать дату/оценку. Пример: /debug_add_mood 2026-03-01 8")
            return True

        await crud.upsert_mood_score_for_date(telegram_user_id, day, score)
        await message.answer(f"Ок. Записал {score} на {day.isoformat()}.")
        return True

    # Debug: быстро заполнить тестовые данные за последние N дней (по умолчанию 21) (для отладки).
    if user_text.startswith("/debug_seed_moods"):
        m = re.match(r"^/debug_seed_moods(?:\s+(\d{1,3}))?\s*$", user_text)
        if not m:
            await message.answer("Формат: /debug_seed_moods [N] (например: /debug_seed_moods 30)")
            return True
        n_s = m.group(1)
        n = int(n_s) if n_s else 21
        n = max(1, min(n, 120))

        import random as _random

        base = _random.randint(4, 7)
        wrote = 0
        for i in range(n - 1, -1, -1): #заполняем тестовые данные за последние N дней
            day = today - timedelta(days=i)
            if _random.random() < 0.2: #20% случаев не записываем оценку
                continue
            drift = int(round((n - 1 - i) / max(1, n / 6)))
            score = base + (drift % 3) - 1 + _random.randint(-1, 1) #генерируем случайную оценку
            score = max(0, min(10, score))
            await crud.upsert_mood_score_for_date(telegram_user_id, day, score) #записываем оценку в БД
            wrote += 1

        await message.answer(f"Ок. Добавил тестовые оценки: {wrote} записей за последние {n} дней.")
        return True

    # Debug: принудительно запускает "вечерний" вопрос про планы (для отладки).
    if user_text == "/debug_evening_now":
        await crud.set_plan_mode(telegram_user_id, "awaiting_plan")
        await message.answer("Какие у тебя планы на завтра?")
        return True

    # Debug: принудительно запускает "вечерний" daily check-in (оценка дня) (для отладки).
    if user_text == "/debug_checkin_now":
        await crud.reset_daily_checkin_for_date(
            telegram_user_id,
            today,
            question_text="Как прошёл твой день? Оцени его от 1 до 10.",
        )
        await message.answer("Как прошёл твой день? Оцени его от 1 до 10.")
        return True

    # Debug: принудительно запускает follow-up вопрос по последнему плану (для отладки).
    if user_text == "/debug_followup_now":
        state = await crud.get_plan_state(telegram_user_id)
        if not state.last_plan_summary:
            await message.answer(
                "Пока нет сохраненного плана. Сначала отправь /debug_evening_now и ответь на вопрос."
            )
            return True
        await crud.set_plan_mode(telegram_user_id, "awaiting_followup")
        await message.answer(
            f"Вчера ты планировал: {state.last_plan_summary}\n"
            "Как у тебя получилось это реализовать сегодня?"
        )
        return True

    return False

