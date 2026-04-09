"""
Главный файл Telegram-бота для отслеживания эмоционального состояния пользователя.
"""

import asyncio
import logging
import os
import random
import re
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Set

from aiogram import Bot, Dispatcher
from aiogram.types import FSInputFile, Message
from dotenv import load_dotenv

from .db import crud
from .llm import (
    convert_mood_text_to_score,
    generate_followup_reaction,
    generate_plan_summary_and_followup,
    generate_plans_today_reaction_and_ask_tomorrow,
    get_response,
    init_llm,
)
from .scheduler import daily_question_scheduler
from .services.scheduler_service import init_scheduler, schedule_message
from .services.stats_service import format_mood_stats_text, get_user_mood_stats
from .services.mood_plot import MonthMoodPoint, build_month_mood_plot_png
from .services.export_service import build_user_export_payload, dumps_user_export
from .services.user_data_service import reset_user_data
from .db.session import (
    ensure_users_telegram_id_bigint,
    init_engine,
    load_known_user_ids,
    ping_db,
    shutdown_engine,
    upsert_user,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

known_users: Set[int] = set()


def _summarize_plan_text(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= 140:
        return normalized
    return normalized[:137].rstrip() + "..."


def _build_followup_question(plan_summary: str) -> str:
    return (
        f"Вчера ты планировал: {plan_summary}\n"
        "Как у тебя получилось это реализовать сегодня?"
    )


async def handle_any_message(message: Message) -> None:
    """
    Обработчик любого входящего сообщения.

    Добавляет пользователя в known_users и отправляет его текст
    в LLM, возвращая ответ «мягкого психолога».
    """
    user_id = message.from_user.id

    await upsert_user(
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

    # /reset — полностью удалить данные пользователя из БД
    if user_text.strip() == "/reset":
        existed = await reset_user_data(user_id)
        known_users.discard(user_id)
        if existed:
            await message.answer("Все твои данные удалены. Можем начать заново 🙂")
        else:
            await message.answer("Похоже, у тебя ещё нет данных в базе. Можем начать заново 🙂")
        return

    # /export — выгрузить все данные пользователя из БД в JSON
    if user_text.strip() == "/export":
        payload = await build_user_export_payload(user_id)
        json_text = dumps_user_export(payload)

        fd, path = tempfile.mkstemp(prefix=f"export_{user_id}_", suffix=".json")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(json_text)
            await message.answer_document(
                FSInputFile(path),
                caption="Твой экспорт данных (JSON).",
            )
        finally:
            try:
                os.remove(path)
            except Exception:
                logging.getLogger(__name__).exception("Failed to remove temp export file: %s", path)
        return

    # /stat — статистика настроения + график текущего месяца
    if user_text.strip() == "/stat":
        stats = await get_user_mood_stats(user_id, today=today)
        await message.answer(format_mood_stats_text(stats))

        if stats.total_days == 0:
            return

        # График текущего месяца по реальным записям (пропуски не показываем).
        entries = await crud.list_mood_scores(user_id)
        month_points = [
            MonthMoodPoint(day=e.day, score=e.score)
            for e in entries
            if e.day.year == today.year and e.day.month == today.month
        ]

        png_path = build_month_mood_plot_png(month_points, year=today.year, month=today.month)
        if png_path is None:
            await message.answer("За текущий месяц пока нет оценок — график построить не из чего.")
            return

        try:
            await message.answer_photo(FSInputFile(png_path))
        finally:
            try:
                os.remove(png_path)
            except Exception:
                logging.getLogger(__name__).exception("Failed to remove temp plot file: %s", png_path)
        return

    # Минимальный ручной тест планировщика (не UX фича).
    # Напиши боту: /test_schedule_1m
    if user_text.strip() == "/test_schedule_1m":
        send_at = datetime.now(timezone.utc) + timedelta(minutes=1)
        await schedule_message(
            telegram_user_id=user_id,
            text="Тест: сообщение, запланированное на +1 минуту.",
            send_at=send_at,
        )
        await message.answer("Ок, запланировал сообщение на +1 минуту.")
        return

    # Debug: добавить/перезаписать оценку за конкретный день.
    # Пример: /debug_add_mood 2026-03-01 8
    if user_text.strip().startswith("/debug_add_mood"):
        m = re.match(r"^/debug_add_mood\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2})\s*$", user_text.strip())
        if not m:
            await message.answer("Формат: /debug_add_mood YYYY-MM-DD SCORE (0..10)")
            return
        day_s, score_s = m.group(1), m.group(2)
        try:
            day = datetime.strptime(day_s, "%Y-%m-%d").date()
            score = int(score_s)
            if score < 0 or score > 10:
                raise ValueError("score out of range")
        except Exception:
            await message.answer("Не смог разобрать дату/оценку. Пример: /debug_add_mood 2026-03-01 8")
            return

        await crud.upsert_mood_score_for_date(user_id, day, score)
        await message.answer(f"Ок. Записал {score} на {day.isoformat()}.")
        return

    # Debug: быстро заполнить тестовые данные за последние N дней (по умолчанию 21).
    # Пример: /debug_seed_moods 30
    if user_text.strip().startswith("/debug_seed_moods"):
        m = re.match(r"^/debug_seed_moods(?:\s+(\d{1,3}))?\s*$", user_text.strip())
        if not m:
            await message.answer("Формат: /debug_seed_moods [N] (например: /debug_seed_moods 30)")
            return
        n_s = m.group(1)
        n = int(n_s) if n_s else 21
        n = max(1, min(n, 120))

        # Сгенерируем «похожую на жизнь» серию: плавный тренд + шум, с пропусками.
        base = random.randint(4, 7)
        wrote = 0
        for i in range(n - 1, -1, -1):
            day = today - timedelta(days=i)
            # 20% пропусков
            if random.random() < 0.2:
                continue
            drift = int(round((n - 1 - i) / max(1, n / 6)))  # лёгкий тренд
            score = base + (drift % 3) - 1 + random.randint(-1, 1)
            score = max(0, min(10, score))
            await crud.upsert_mood_score_for_date(user_id, day, score)
            wrote += 1

        await message.answer(f"Ок. Добавил тестовые оценки: {wrote} записей за последние {n} дней.")
        return

    # --- Daily check-in сценарий (оценка дня -> планы на сегодня -> планы на завтра) ---
    daily = await crud.get_daily_checkin(user_id, today)
    if daily is not None and daily.status == "sent" and daily.mood_score is None:
        text = user_text.strip()
        score: int | None = None
        if text.isdigit():
            n = int(text)
            if 1 <= n <= 10:
                score = n
        if score is None:
            score = await convert_mood_text_to_score(user_id, text)
        if score is None:
            await message.answer("Не совсем понял оценку. Напиши число от 1 до 10.")
            return

        await crud.save_daily_checkin_mood_score(user_id, today, score, response_text=None)
        await crud.set_daily_checkin_status(user_id, today, "plans_today")

        if score <= 4:
            mood_reply = "Похоже, день был непростой. Ничего страшного, такие дни бывают."
        elif score <= 7:
            mood_reply = "Неплохой день 🙂"
        else:
            mood_reply = "Отлично! Рад, что день прошёл хорошо."

        plan_state = await crud.get_plan_state(user_id)
        plans_today_line = ""
        if plan_state.last_plan_for_date == today and plan_state.last_plan_summary:
            plans_today_line = f"\n\nТвои планы на сегодня были: {plan_state.last_plan_summary}"

        await message.answer(
            f"{mood_reply}\n\nКак прошли твои планы на сегодня?{plans_today_line}"
        )
        return

    if daily is not None and daily.status == "plans_today":
        plan_state = await crud.get_plan_state(user_id)
        today_plan_summary = (
            plan_state.last_plan_summary
            if plan_state.last_plan_for_date == today
            else None
        )
        reply = await generate_plans_today_reaction_and_ask_tomorrow(
            user_id=user_id,
            plans_today_text=user_text,
            today_plan_summary=today_plan_summary,
        )
        await crud.save_daily_checkin_answer(user_id, today, response_text=user_text)
        await crud.set_daily_checkin_status(user_id, today, "answered")
        await crud.set_plan_mode(user_id, "awaiting_plan")
        await message.answer(reply)
        return

    # Debug: принудительно запускает "вечерний" вопрос про планы.
    if user_text.strip() == "/debug_evening_now":
        await crud.set_plan_mode(user_id, "awaiting_plan")
        await message.answer("Какие у тебя планы на завтра?")
        return

    # Debug: принудительно запускает "вечерний" daily check-in (оценка дня).
    if user_text.strip() == "/debug_checkin_now":
        await crud.reset_daily_checkin_for_date(
            user_id,
            today,
            question_text="Как прошёл твой день? Оцени его от 1 до 10.",
        )
        await message.answer("Как прошёл твой день? Оцени его от 1 до 10.")
        return

    # Debug: принудительно запускает follow-up вопрос по последнему плану.
    if user_text.strip() == "/debug_followup_now":
        state = await crud.get_plan_state(user_id)
        if not state.last_plan_summary:
            await message.answer("Пока нет сохраненного плана. Сначала отправь /debug_evening_now и ответь на вопрос.")
            return
        await crud.set_plan_mode(user_id, "awaiting_followup")
        await message.answer(_build_followup_question(state.last_plan_summary))
        return

    state = await crud.get_plan_state(user_id)

    if state.mode == "awaiting_plan":
        today = datetime.now().date()
        plan_for_date = today + timedelta(days=1)
        summary, followup_text = await generate_plan_summary_and_followup(user_id, user_text)
        if not summary:
            summary = _summarize_plan_text(user_text)
        if not followup_text:
            followup_text = _build_followup_question(summary)

        await crud.save_latest_plan(
            telegram_user_id=user_id,
            for_date=plan_for_date,
            raw_text=user_text,
            summary_text=summary,
        )
        await crud.set_plan_mode(user_id, "normal")

        await schedule_message(
            telegram_user_id=user_id,
            text=followup_text,
            send_at=datetime.now(timezone.utc) + timedelta(days=1),
            message_kind="plans_followup_question",
        )

        await message.answer(
            "Запомнил. Завтра в это же время спрошу, как это получилось.\n"
            "А пока можем просто поговорить, если хочешь)"
        )
        return

    if state.mode == "awaiting_followup":
        comment = await generate_followup_reaction(user_id, user_text)
        await crud.set_plan_mode(user_id, "awaiting_plan")
        await message.answer(comment)
        return

    reply = await get_response(user_id, user_text)
    await message.answer(reply)


async def main() -> None:
    """Точка входа: инициализация бота, LLM-клиента, БД и планировщика."""
    init_llm()
    init_engine()

    await ping_db()
    await ensure_users_telegram_id_bigint()

    # Загружаем ранее известных пользователей из БД, чтобы планировщик
    # работал после перезапуска процесса.
    known_users.update(await load_known_user_ids())

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(handle_any_message)

    scheduler_service = init_scheduler(bot)
    try:
        await scheduler_service.start()
        await scheduler_service.restore_pending_messages()
        asyncio.create_task(daily_question_scheduler(bot, known_users))
        await dp.start_polling(bot)
    finally:
        await scheduler_service.shutdown()
        await shutdown_engine()


if __name__ == "__main__":
    asyncio.run(main())

