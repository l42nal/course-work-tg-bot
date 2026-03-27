from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select

from .models import DailyCheckIn, FutureMessage, Plan, PlanFollowUp, User, UserPlanState
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

