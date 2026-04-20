from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from ..db import crud
from ..llm import (
    convert_mood_text_to_score,
    generate_followup_reaction,
    generate_plan_summary_and_followup,
    generate_plans_today_reaction_and_ask_tomorrow,
)
from ..db.session import load_known_user_ids


DAILY_MESSAGE_TEXT = "Как прошёл твой день? Оцени его от 1 до 10."


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


@dataclass(frozen=True)
class CheckinResult:
    handled: bool
    reply_text: str | None = None
    schedule_followup: bool = False
    followup_text: str | None = None
    followup_send_at: datetime | None = None
    followup_kind: str = "generic"


async def handle_checkin_and_plans_flow(
    *,
    telegram_user_id: int,
    user_text: str,
    today: date,
) -> CheckinResult:
    """
    Основная бизнес-логика сценария:
    - daily check-in: sent -> mood_score -> plans_today -> answered
    - планы на завтра / follow-up

    Возвращает:
    - handled=False если этот текст не относится к сценарию и его нужно отдать "обычному" LLM-диалогу.
    - handled=True + reply_text если сценарий обработал сообщение.
    - опционально schedule_followup=True для планирования follow-up сообщения через scheduler_service.
    """
    text = user_text.strip()
    if not text:
        return CheckinResult(handled=False)

    daily = await crud.get_daily_checkin(telegram_user_id, today)

    # 1) Пользователь отвечает на вопрос "Оцени день 1..10"
    if daily is not None and daily.status == "sent" and daily.mood_score is None:
        score: int | None = None
        if text.isdigit():
            n = int(text)
            if 1 <= n <= 10:
                score = n
        if score is None:
            score = await convert_mood_text_to_score(telegram_user_id, text)
        if score is None:
            return CheckinResult(handled=True, reply_text="Не совсем понял оценку. Напиши число от 1 до 10.")

        await crud.save_daily_checkin_mood_score(telegram_user_id, today, score, response_text=None)
        await crud.set_daily_checkin_status(telegram_user_id, today, "plans_today")

        if score <= 4:
            mood_reply = "Похоже, день был непростой. Ничего страшного, такие дни бывают."
        elif score <= 7:
            mood_reply = "Неплохой день 🙂"
        else:
            mood_reply = "Отлично! Рад, что день прошёл хорошо."

        plan_state = await crud.get_plan_state(telegram_user_id)
        plans_today_line = ""
        if plan_state.last_plan_for_date == today and plan_state.last_plan_summary:
            plans_today_line = f"\n\nТвои планы на сегодня были: {plan_state.last_plan_summary}"

        return CheckinResult(
            handled=True,
            reply_text=f"{mood_reply}\n\nКак прошли твои планы на сегодня?{plans_today_line}",
        )

    # 2) Пользователь отвечает "как прошли планы на сегодня?"
    if daily is not None and daily.status == "plans_today":
        plan_state = await crud.get_plan_state(telegram_user_id)
        today_plan_summary = (
            plan_state.last_plan_summary
            if plan_state.last_plan_for_date == today
            else None
        )
        reply = await generate_plans_today_reaction_and_ask_tomorrow(
            user_id=telegram_user_id,
            plans_today_text=text,
            today_plan_summary=today_plan_summary,
        )
        await crud.save_daily_checkin_answer(telegram_user_id, today, response_text=text)
        await crud.set_daily_checkin_status(telegram_user_id, today, "answered")
        await crud.set_plan_mode(telegram_user_id, "awaiting_plan")
        return CheckinResult(handled=True, reply_text=reply)

    # 3) Ветка планов: awaiting_plan / awaiting_followup
    state = await crud.get_plan_state(telegram_user_id)

    if state.mode == "awaiting_plan":
        plan_for_date = today + timedelta(days=1)
        summary, followup_text = await generate_plan_summary_and_followup(telegram_user_id, text)
        if not summary:
            summary = _summarize_plan_text(text)
        if not followup_text:
            followup_text = _build_followup_question(summary)

        await crud.save_latest_plan(
            telegram_user_id=telegram_user_id,
            for_date=plan_for_date,
            raw_text=text,
            summary_text=summary,
        )
        await crud.set_plan_mode(telegram_user_id, "normal")

        send_at = datetime.now(timezone.utc) + timedelta(days=1)
        return CheckinResult(
            handled=True,
            reply_text=(
                "Запомнил. Завтра в это же время спрошу, как это получилось.\n"
                "А пока можем просто поговорить, если хочешь)"
            ),
            schedule_followup=True,
            followup_text=followup_text,
            followup_send_at=send_at,
            followup_kind="plans_followup_question",
        )

    if state.mode == "awaiting_followup":
        comment = await generate_followup_reaction(telegram_user_id, text)
        await crud.set_plan_mode(telegram_user_id, "awaiting_plan")
        return CheckinResult(handled=True, reply_text=comment)

    return CheckinResult(handled=False)


async def list_target_user_ids_for_daily_checkin() -> set[int]:
    """
    Кого пинговать в daily check-in.

    Сейчас критерий "писал хотя бы раз" == присутствует в таблице users,
    поэтому берём всех пользователей из БД.
    """
    return await load_known_user_ids()

