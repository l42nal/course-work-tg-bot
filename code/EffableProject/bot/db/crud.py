from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select

from .models import DailyCheckIn, FutureMessage, Plan, PlanFollowUp, User, UserDailySettings, UserPlanState
from .session import session_scope


@dataclass(frozen=True)
class PendingFutureMessage:
    id: uuid.UUID
    telegram_user_id: int
    text: str
    send_at: datetime
    status: str
    kind: str


@dataclass(frozen=True)
class PlanStateData:
    mode: str
    last_plan_for_date: Optional[date]
    last_plan_summary: Optional[str]


@dataclass(frozen=True)
class DailyCheckInData:
    status: str
    mood_score: Optional[int]


@dataclass(frozen=True)
class MoodScoreRow:
    day: date
    score: int


@dataclass(frozen=True)
class DailySettingsData:
    timezone_offset_hours: int
    daily_checkin_hour_local: int


async def list_mood_scores(telegram_user_id: int) -> list[MoodScoreRow]:
    """
    Возвращает все реальные оценки настроения пользователя (mood_score IS NOT NULL),
    отсортированные по дате (asc).
    """
    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found: telegram_user_id={telegram_user_id}")

        rows = (
            await session.execute(
                select(DailyCheckIn.checkin_date, DailyCheckIn.mood_score)
                .where(
                    DailyCheckIn.user_id == user.id,
                    DailyCheckIn.mood_score.is_not(None),
                )
                .order_by(DailyCheckIn.checkin_date.asc())
            )
        ).all()

    return [MoodScoreRow(day=r[0], score=int(r[1])) for r in rows]


async def upsert_mood_score_for_date(
    telegram_user_id: int,
    checkin_date: date,
    mood_score: int,
) -> None:
    """
    Debug/helper: записывает/перезаписывает mood_score на конкретную дату.
    Не трогает основную логику сценария, это служебный метод.
    """
    await save_daily_checkin_mood_score(
        telegram_user_id=telegram_user_id,
        checkin_date=checkin_date,
        mood_score=mood_score,
        response_text=None,
    )


async def create_future_message(
    telegram_user_id: int,
    message_text: str,
    scheduled_for: datetime,
    message_kind: str = "generic",
) -> uuid.UUID:
    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found: telegram_user_id={telegram_user_id}")

        fm = FutureMessage(
            user_id=user.id,
            message_text=message_text,
            scheduled_for=scheduled_for,
            status="scheduled",
            kind=message_kind,
        )
        session.add(fm)
        await session.commit()
        return fm.id


async def list_pending_future_messages(now: Optional[datetime] = None) -> list[PendingFutureMessage]:
    """
    Возвращает все сообщения, которые ещё не отправлены (status='scheduled').

    Важно: это именно источник истины для восстановления задач после перезапуска.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(
                    FutureMessage.id,
                    User.telegram_user_id,
                    FutureMessage.message_text,
                    FutureMessage.scheduled_for,
                    FutureMessage.status,
                    FutureMessage.kind,
                )
                .join(User, User.id == FutureMessage.user_id)
                .where(FutureMessage.status == "scheduled")
                # Берём и прошедшие тоже: если бот был выключен, мы хотим отправить при старте
                .order_by(FutureMessage.scheduled_for.asc())
            )
        ).all()

    return [
        PendingFutureMessage(
            id=row[0],
            telegram_user_id=int(row[1]),
            text=str(row[2]),
            send_at=row[3],
            status=str(row[4]),
            kind=str(row[5]),
        )
        for row in rows
    ]


async def get_future_message_for_sending(message_id: uuid.UUID) -> PendingFutureMessage | None:
    async with session_scope() as session:
        row = (
            await session.execute(
                select(
                    FutureMessage.id,
                    User.telegram_user_id,
                    FutureMessage.message_text,
                    FutureMessage.scheduled_for,
                    FutureMessage.status,
                    FutureMessage.kind,
                )
                .join(User, User.id == FutureMessage.user_id)
                .where(FutureMessage.id == message_id)
            )
        ).one_or_none()

        if row is None:
            return None

        # Для select(col1, col2, ...) SQLAlchemy возвращает Row с колонками по индексам.
        # row[0] уже содержит UUID id, а не "вложенный кортеж".
        r = row
        return PendingFutureMessage(
            id=uuid.UUID(str(r[0])),
            telegram_user_id=int(r[1]),
            text=str(r[2]),
            send_at=r[3],
            status=str(r[4]),
            kind=str(r[5]),
        )


async def mark_future_message_sent(message_id: uuid.UUID, sent_at: Optional[datetime] = None) -> None:
    if sent_at is None:
        sent_at = datetime.now(timezone.utc)

    async with session_scope() as session:
        fm = (
            await session.execute(select(FutureMessage).where(FutureMessage.id == message_id))
        ).scalar_one_or_none()
        if fm is None:
            return

        # Идемпотентно: если уже sent/cancelled — не трогаем.
        if fm.status != "scheduled":
            return

        fm.status = "sent"
        fm.sent_at = sent_at
        await session.commit()


async def upsert_user_daily_settings(
    telegram_user_id: int,
    *,
    timezone_offset_hours: int,
    daily_checkin_hour_local: int,
) -> None:
    if timezone_offset_hours < 0 or timezone_offset_hours > 23:
        raise ValueError("timezone_offset_hours must be in range 0..23")
    if daily_checkin_hour_local < 0 or daily_checkin_hour_local > 23:
        raise ValueError("daily_checkin_hour_local must be in range 0..23")

    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found: telegram_user_id={telegram_user_id}")

        settings = (
            await session.execute(select(UserDailySettings).where(UserDailySettings.user_id == user.id))
        ).scalar_one_or_none()

        if settings is None:
            settings = UserDailySettings(
                user_id=user.id,
                timezone_offset_hours=int(timezone_offset_hours),
                daily_checkin_hour_local=int(daily_checkin_hour_local),
            )
            session.add(settings)
        else:
            settings.timezone_offset_hours = int(timezone_offset_hours)
            settings.daily_checkin_hour_local = int(daily_checkin_hour_local)

        await session.commit()


async def get_user_daily_settings(telegram_user_id: int) -> DailySettingsData | None:
    async with session_scope() as session:
        row = (
            await session.execute(
                select(
                    UserDailySettings.timezone_offset_hours,
                    UserDailySettings.daily_checkin_hour_local,
                )
                .join(User, User.id == UserDailySettings.user_id)
                .where(User.telegram_user_id == telegram_user_id)
            )
        ).one_or_none()

        if row is None:
            return None

        return DailySettingsData(
            timezone_offset_hours=int(row[0]),
            daily_checkin_hour_local=int(row[1]),
        )


async def list_user_daily_settings() -> list[tuple[int, int, int]]:
    """
    Возвращает список настроек в формате:
    (telegram_user_id, timezone_offset_hours, daily_checkin_hour_local)
    """
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(
                    User.telegram_user_id,
                    UserDailySettings.timezone_offset_hours,
                    UserDailySettings.daily_checkin_hour_local,
                )
                .join(UserDailySettings, UserDailySettings.user_id == User.id)
                .order_by(User.telegram_user_id.asc())
            )
        ).all()

    return [(int(r[0]), int(r[1]), int(r[2])) for r in rows]


async def get_plan_state(telegram_user_id: int) -> PlanStateData:
    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found: telegram_user_id={telegram_user_id}")

        state = (
            await session.execute(select(UserPlanState).where(UserPlanState.user_id == user.id))
        ).scalar_one_or_none()
        if state is None:
            state = UserPlanState(user_id=user.id, mode="normal")
            session.add(state)
            await session.commit()
            return PlanStateData(mode="normal", last_plan_for_date=None, last_plan_summary=None)

        return PlanStateData(
            mode=state.mode,
            last_plan_for_date=state.last_plan_for_date,
            last_plan_summary=state.last_plan_summary,
        )


async def set_plan_mode(telegram_user_id: int, mode: str) -> None:
    if mode not in {"normal", "awaiting_plan", "awaiting_followup"}:
        raise ValueError(f"Unsupported plan mode: {mode}")

    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found: telegram_user_id={telegram_user_id}")

        state = (
            await session.execute(select(UserPlanState).where(UserPlanState.user_id == user.id))
        ).scalar_one_or_none()
        if state is None:
            state = UserPlanState(user_id=user.id, mode=mode)
            session.add(state)
        else:
            state.mode = mode
        await session.commit()


async def save_latest_plan(
    telegram_user_id: int,
    for_date: date,
    raw_text: str,
    summary_text: str,
) -> None:
    """
    Хранит только последний план пользователя (перезаписывает предыдущее значение).
    """
    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found: telegram_user_id={telegram_user_id}")

        state = (
            await session.execute(select(UserPlanState).where(UserPlanState.user_id == user.id))
        ).scalar_one_or_none()
        if state is None:
            state = UserPlanState(
                user_id=user.id,
                mode="normal",
                last_plan_for_date=for_date,
                last_plan_raw_text=raw_text,
                last_plan_summary=summary_text,
            )
            session.add(state)
        else:
            state.last_plan_for_date = for_date
            state.last_plan_raw_text = raw_text
            state.last_plan_summary = summary_text

        await session.commit()


async def ensure_daily_checkin_exists(
    telegram_user_id: int,
    checkin_date: date,
    question_text: Optional[str] = None,
) -> uuid.UUID:
    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found: telegram_user_id={telegram_user_id}")

        daily = (
            await session.execute(
                select(DailyCheckIn).where(
                    DailyCheckIn.user_id == user.id, DailyCheckIn.checkin_date == checkin_date
                )
            )
        ).scalar_one_or_none()

        if daily is None:
            daily = DailyCheckIn(
                user_id=user.id,
                checkin_date=checkin_date,
                status="scheduled",
                question_text=question_text,
            )
            session.add(daily)
            await session.commit()

        return daily.id


async def save_daily_checkin_answer(
    telegram_user_id: int,
    checkin_date: date,
    response_text: str,
) -> None:
    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found: telegram_user_id={telegram_user_id}")

        daily = (
            await session.execute(
                select(DailyCheckIn).where(
                    DailyCheckIn.user_id == user.id, DailyCheckIn.checkin_date == checkin_date
                )
            )
        ).scalar_one_or_none()

        if daily is None:
            daily = DailyCheckIn(
                user_id=user.id,
                checkin_date=checkin_date,
                status="answered",
                response_text=response_text,
            )
            session.add(daily)
        else:
            daily.status = "answered"
            daily.response_text = response_text

        await session.commit()


async def get_daily_checkin(telegram_user_id: int, checkin_date: date) -> DailyCheckInData | None:
    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found: telegram_user_id={telegram_user_id}")

        daily = (
            await session.execute(
                select(DailyCheckIn).where(
                    DailyCheckIn.user_id == user.id,
                    DailyCheckIn.checkin_date == checkin_date,
                )
            )
        ).scalar_one_or_none()
        if daily is None:
            return None

        return DailyCheckInData(status=daily.status, mood_score=daily.mood_score)


async def set_daily_checkin_status(telegram_user_id: int, checkin_date: date, status: str) -> None:
    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found: telegram_user_id={telegram_user_id}")

        daily = (
            await session.execute(
                select(DailyCheckIn).where(
                    DailyCheckIn.user_id == user.id,
                    DailyCheckIn.checkin_date == checkin_date,
                )
            )
        ).scalar_one_or_none()
        if daily is None:
            daily = DailyCheckIn(
                user_id=user.id,
                checkin_date=checkin_date,
                status=status,
            )
            session.add(daily)
        else:
            daily.status = status

        await session.commit()


async def reset_daily_checkin_for_date(
    telegram_user_id: int,
    checkin_date: date,
    question_text: Optional[str] = None,
) -> None:
    """
    Debug/helper: сбрасывает чек-ин на дату в состояние "sent" и очищает ответы/оценку.
    Нужен для повторяемого ручного тестирования сценария.
    """
    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found: telegram_user_id={telegram_user_id}")

        daily = (
            await session.execute(
                select(DailyCheckIn).where(
                    DailyCheckIn.user_id == user.id,
                    DailyCheckIn.checkin_date == checkin_date,
                )
            )
        ).scalar_one_or_none()

        if daily is None:
            daily = DailyCheckIn(
                user_id=user.id,
                checkin_date=checkin_date,
                status="sent",
                question_text=question_text,
                question_sent_at=datetime.now(timezone.utc),
            )
            session.add(daily)
        else:
            daily.status = "sent"
            daily.question_text = question_text or daily.question_text
            daily.question_sent_at = datetime.now(timezone.utc)
            daily.response_text = None
            daily.mood_score = None

        await session.commit()


async def save_daily_checkin_mood_score(
    telegram_user_id: int,
    checkin_date: date,
    mood_score: int,
    response_text: Optional[str] = None,
) -> None:
    if mood_score < 0 or mood_score > 10:
        raise ValueError("mood_score must be in range 0..10")

    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found: telegram_user_id={telegram_user_id}")

        daily = (
            await session.execute(
                select(DailyCheckIn).where(
                    DailyCheckIn.user_id == user.id, DailyCheckIn.checkin_date == checkin_date
                )
            )
        ).scalar_one_or_none()

        if daily is None:
            daily = DailyCheckIn(
                user_id=user.id,
                checkin_date=checkin_date,
                status="graded",
                response_text=response_text,
                mood_score=mood_score,
            )
            session.add(daily)
        else:
            daily.status = "graded"
            if response_text is not None:
                daily.response_text = response_text
            daily.mood_score = mood_score

        await session.commit()


async def upsert_plan(
    telegram_user_id: int,
    for_date: date,
    raw_text: str,
    summary_text: Optional[str] = None,
) -> None:
    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found: telegram_user_id={telegram_user_id}")

        plan = (
            await session.execute(
                select(Plan).where(Plan.user_id == user.id, Plan.for_date == for_date)
            )
        ).scalar_one_or_none()

        if plan is None:
            plan = Plan(
                user_id=user.id,
                for_date=for_date,
                raw_text=raw_text,
                summary_text=summary_text,
                status="planned",
            )
            session.add(plan)
        else:
            plan.raw_text = raw_text
            plan.summary_text = summary_text
            plan.status = "planned"

        await session.commit()


async def submit_plan_followup(
    telegram_user_id: int,
    for_date: date,
    response_text: str,
    summary_text: Optional[str] = None,
) -> None:
    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found: telegram_user_id={telegram_user_id}")

        plan = (
            await session.execute(
                select(Plan).where(Plan.user_id == user.id, Plan.for_date == for_date)
            )
        ).scalar_one_or_none()
        if plan is None:
            raise ValueError("Plan not found; create plans first via upsert_plan()")

        followup = (
            await session.execute(select(PlanFollowUp).where(PlanFollowUp.plan_id == plan.id))
        ).scalar_one_or_none()

        if followup is None:
            followup = PlanFollowUp(
                plan_id=plan.id,
                response_text=response_text,
                summary_text=summary_text,
                status="submitted",
            )
            session.add(followup)
        else:
            followup.response_text = response_text
            followup.summary_text = summary_text
            followup.status = "submitted"

        await session.commit()

