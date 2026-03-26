from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import select

from .models import DailyCheckIn, FutureMessage, Plan, PlanFollowUp, User
from .session import session_scope


async def create_future_message(
    telegram_user_id: int,
    message_text: str,
    scheduled_for: datetime,
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
        )
        session.add(fm)
        await session.commit()
        return fm.id


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

