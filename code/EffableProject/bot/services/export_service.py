from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..db.models import DailyCheckIn, FutureMessage, Plan, User, UserPlanState
from ..db.session import session_scope


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(v) for v in value]
    return str(value)


def dumps_user_export(data: dict[str, Any]) -> str:
    """
    Сериализует экспорт в человекочитаемый JSON:
    - ensure_ascii=False (кириллица без экранирования)
    - indent=2
    - ISO-формат дат/времени
    """
    return json.dumps(data, ensure_ascii=False, indent=2, default=_to_jsonable)


def _serialize_user(u: User) -> dict[str, Any]:
    return {
        "id": str(u.id),
        "telegram_user_id": int(u.telegram_user_id),
        "first_name": u.first_name,
        "last_name": u.last_name,
        "username": u.username,
        "language_code": u.language_code,
        "created_at": u.created_at,
        "updated_at": u.updated_at,
    }


def _serialize_daily_checkin(d: DailyCheckIn) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "checkin_date": d.checkin_date,
        "status": d.status,
        "question_text": d.question_text,
        "question_sent_at": d.question_sent_at,
        "response_text": d.response_text,
        "mood_score": d.mood_score,
        "created_at": d.created_at,
        "updated_at": d.updated_at,
    }


def _serialize_future_message(m: FutureMessage) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "message_text": m.message_text,
        "scheduled_for": m.scheduled_for,
        "status": m.status,
        "kind": m.kind,
        "created_at": m.created_at,
        "sent_at": m.sent_at,
        "cancelled_at": m.cancelled_at,
    }


def _serialize_plan_state(s: UserPlanState) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "mode": s.mode,
        "last_plan_for_date": s.last_plan_for_date,
        "last_plan_raw_text": s.last_plan_raw_text,
        "last_plan_summary": s.last_plan_summary,
        "updated_at": s.updated_at,
    }


def _serialize_plan(p: Plan) -> dict[str, Any]:
    followup: Optional[dict[str, Any]] = None
    if p.followup is not None:
        followup = {
            "id": str(p.followup.id),
            "response_text": p.followup.response_text,
            "summary_text": p.followup.summary_text,
            "status": p.followup.status,
            "created_at": p.followup.created_at,
        }

    return {
        "id": str(p.id),
        "for_date": p.for_date,
        "raw_text": p.raw_text,
        "summary_text": p.summary_text,
        "status": p.status,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "followup": followup,
    }


async def build_user_export_payload(telegram_user_id: int) -> dict[str, Any]:
    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found: telegram_user_id={telegram_user_id}")

        daily_checkins = (
            await session.execute(
                select(DailyCheckIn)
                .where(DailyCheckIn.user_id == user.id)
                .order_by(DailyCheckIn.checkin_date.asc())
            )
        ).scalars().all()

        plans = (
            await session.execute(
                select(Plan)
                .options(selectinload(Plan.followup))
                .where(Plan.user_id == user.id)
                .order_by(Plan.for_date.asc())
            )
        ).scalars().all()

        future_messages = (
            await session.execute(
                select(FutureMessage)
                .where(FutureMessage.user_id == user.id)
                .order_by(FutureMessage.scheduled_for.asc())
            )
        ).scalars().all()

        plan_state = (
            await session.execute(select(UserPlanState).where(UserPlanState.user_id == user.id))
        ).scalar_one_or_none()

    return {
        "meta": {
            "exported_at": datetime.now(timezone.utc),
            "schema_version": 1,
        },
        "user": _serialize_user(user),
        "daily_checkins": [_serialize_daily_checkin(d) for d in daily_checkins],
        "plans": [_serialize_plan(p) for p in plans],
        "plan_state": _serialize_plan_state(plan_state) if plan_state is not None else None,
        "scheduled_messages": [_serialize_future_message(m) for m in future_messages],
    }

