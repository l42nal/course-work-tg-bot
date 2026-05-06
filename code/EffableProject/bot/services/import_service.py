from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import delete, select

from ..db.models import DailyCheckIn, FutureMessage, Plan, PlanFollowUp, User, UserPlanState
from ..db.session import session_scope
from .scheduler_service import get_scheduler


@dataclass(frozen=True)
class ImportResult:
    daily_checkins: int
    plans: int
    plan_followups: int
    scheduled_messages: int


def _require_dict(obj: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ValueError(f"{where} must be an object")
    return obj


def _require_list(obj: Any, *, where: str) -> list[Any]:
    if not isinstance(obj, list):
        raise ValueError(f"{where} must be an array")
    return obj


def _opt_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return str(v)


def _parse_uuid(v: Any, *, where: str) -> uuid.UUID:
    if isinstance(v, uuid.UUID):
        return v
    if not isinstance(v, str):
        raise ValueError(f"{where} must be UUID string")
    return uuid.UUID(v)


def _parse_date(v: Any, *, where: str) -> date:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if not isinstance(v, str):
        raise ValueError(f"{where} must be ISO date string")
    return date.fromisoformat(v)


def _parse_dt(v: Any, *, where: str) -> datetime:
    if isinstance(v, datetime):
        return v
    if not isinstance(v, str):
        raise ValueError(f"{where} must be ISO datetime string")
    # export uses .isoformat(); allow both with/without tzinfo
    return datetime.fromisoformat(v)


def _opt_dt(v: Any, *, where: str) -> Optional[datetime]:
    if v is None:
        return None
    return _parse_dt(v, where=where)


def _opt_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    raise ValueError("expected int")


def _validate_root(payload: Any) -> dict[str, Any]:
    root = _require_dict(payload, where="payload")
    meta = _require_dict(root.get("meta"), where="payload.meta")
    schema_version = meta.get("schema_version")
    if schema_version != 1:
        raise ValueError("Unsupported schema_version")

    # Required top-level keys (same as export)
    for k in ["user", "daily_checkins", "plans", "plan_state", "scheduled_messages"]:
        if k not in root:
            raise ValueError(f"Missing key: payload.{k}")
    return root


async def import_user_data_from_export_payload(*, telegram_user_id: int, payload: Any) -> ImportResult:
    """
    Импортирует данные в БД по формату, который генерирует /export.

    Правила:
    - все данные привязываются к текущему `telegram_user_id`
    - старые user_id из JSON игнорируются
    - даты/времена сохраняются (created_at, scheduled_for и т.д.)
    - стратегия: удалить текущие данные пользователя → вставить новые
    """
    root = _validate_root(payload)

    daily_checkins_in = _require_list(root.get("daily_checkins"), where="payload.daily_checkins")
    plans_in = _require_list(root.get("plans"), where="payload.plans")
    plan_state_in = root.get("plan_state")
    scheduled_in = _require_list(root.get("scheduled_messages"), where="payload.scheduled_messages")

    async with session_scope() as session:
        # Важно: SQLAlchemy 2.x начинает транзакцию автоматически при первом запросе.
        # Поэтому весь импорт делаем в ОДНОЙ явной транзакции (без вложенных begin()).
        async with session.begin():
            user = (
                await session.execute(select(User).where(User.telegram_user_id == int(telegram_user_id)))
            ).scalar_one_or_none()
            if user is None:
                raise ValueError("User not found in DB")

            # 1) Purge existing user-owned data (keep User + settings)
            await session.execute(delete(FutureMessage).where(FutureMessage.user_id == user.id))
            await session.execute(delete(DailyCheckIn).where(DailyCheckIn.user_id == user.id))
            await session.execute(delete(UserPlanState).where(UserPlanState.user_id == user.id))
            await session.execute(delete(Plan).where(Plan.user_id == user.id))

            # 2) Insert new data
            daily_objs: list[DailyCheckIn] = []
            for i, raw in enumerate(daily_checkins_in):
                d = _require_dict(raw, where=f"payload.daily_checkins[{i}]")
                mood_score = d.get("mood_score")
                mood_score_i = _opt_int(mood_score) if mood_score is not None else None
                if mood_score_i is not None and not (0 <= int(mood_score_i) <= 10):
                    raise ValueError("mood_score out of range")

                daily_objs.append(
                    DailyCheckIn(
                        id=_parse_uuid(d.get("id"), where=f"payload.daily_checkins[{i}].id"),
                        user_id=user.id,
                        checkin_date=_parse_date(
                            d.get("checkin_date"),
                            where=f"payload.daily_checkins[{i}].checkin_date",
                        ),
                        status=str(d.get("status") or "scheduled"),
                        question_text=_opt_str(d.get("question_text")),
                        question_sent_at=_opt_dt(
                            d.get("question_sent_at"),
                            where=f"payload.daily_checkins[{i}].question_sent_at",
                        ),
                        response_text=_opt_str(d.get("response_text")),
                        mood_score=mood_score_i,
                        created_at=_parse_dt(d.get("created_at"), where=f"payload.daily_checkins[{i}].created_at"),
                        updated_at=_parse_dt(d.get("updated_at"), where=f"payload.daily_checkins[{i}].updated_at"),
                    )
                )

            plan_objs: list[Plan] = []
            followup_objs: list[PlanFollowUp] = []
            for i, raw in enumerate(plans_in):
                p = _require_dict(raw, where=f"payload.plans[{i}]")
                plan_id = _parse_uuid(p.get("id"), where=f"payload.plans[{i}].id")
                plan_objs.append(
                    Plan(
                        id=plan_id,
                        user_id=user.id,
                        for_date=_parse_date(p.get("for_date"), where=f"payload.plans[{i}].for_date"),
                        raw_text=str(p.get("raw_text") or ""),
                        summary_text=_opt_str(p.get("summary_text")),
                        status=str(p.get("status") or "planned"),
                        created_at=_parse_dt(p.get("created_at"), where=f"payload.plans[{i}].created_at"),
                        updated_at=_parse_dt(p.get("updated_at"), where=f"payload.plans[{i}].updated_at"),
                    )
                )

                fu = p.get("followup")
                if fu is not None:
                    f = _require_dict(fu, where=f"payload.plans[{i}].followup")
                    followup_objs.append(
                        PlanFollowUp(
                            id=_parse_uuid(f.get("id"), where=f"payload.plans[{i}].followup.id"),
                            plan_id=plan_id,
                            response_text=str(f.get("response_text") or ""),
                            summary_text=_opt_str(f.get("summary_text")),
                            status=str(f.get("status") or "submitted"),
                            created_at=_parse_dt(
                                f.get("created_at"),
                                where=f"payload.plans[{i}].followup.created_at",
                            ),
                        )
                    )

            plan_state_obj: Optional[UserPlanState] = None
            if plan_state_in is not None:
                s = _require_dict(plan_state_in, where="payload.plan_state")
                plan_state_obj = UserPlanState(
                    id=_parse_uuid(s.get("id"), where="payload.plan_state.id"),
                    user_id=user.id,
                    mode=str(s.get("mode") or "normal"),
                    last_plan_for_date=(
                        _parse_date(s.get("last_plan_for_date"), where="payload.plan_state.last_plan_for_date")
                        if s.get("last_plan_for_date") is not None
                        else None
                    ),
                    last_plan_raw_text=_opt_str(s.get("last_plan_raw_text")),
                    last_plan_summary=_opt_str(s.get("last_plan_summary")),
                    updated_at=_parse_dt(s.get("updated_at"), where="payload.plan_state.updated_at"),
                )

            future_objs: list[FutureMessage] = []
            for i, raw in enumerate(scheduled_in):
                m = _require_dict(raw, where=f"payload.scheduled_messages[{i}]")
                future_objs.append(
                    FutureMessage(
                        id=_parse_uuid(m.get("id"), where=f"payload.scheduled_messages[{i}].id"),
                        user_id=user.id,
                        message_text=str(m.get("message_text") or ""),
                        scheduled_for=_parse_dt(
                            m.get("scheduled_for"),
                            where=f"payload.scheduled_messages[{i}].scheduled_for",
                        ),
                        status=str(m.get("status") or "scheduled"),
                        kind=str(m.get("kind") or "generic"),
                        created_at=_parse_dt(
                            m.get("created_at"),
                            where=f"payload.scheduled_messages[{i}].created_at",
                        ),
                        sent_at=_opt_dt(m.get("sent_at"), where=f"payload.scheduled_messages[{i}].sent_at"),
                        cancelled_at=_opt_dt(
                            m.get("cancelled_at"),
                            where=f"payload.scheduled_messages[{i}].cancelled_at",
                        ),
                    )
                )

            if daily_objs:
                session.add_all(daily_objs)
            if plan_objs:
                session.add_all(plan_objs)
            if followup_objs:
                session.add_all(followup_objs)
            if plan_state_obj is not None:
                session.add(plan_state_obj)
            if future_objs:
                session.add_all(future_objs)

        # 3) Register imported scheduled messages in in-process scheduler
        scheduler = get_scheduler()
        scheduled_cnt = 0
        for m in future_objs:
            if m.status == "scheduled":
                scheduler.register_message_job(m.id, m.scheduled_for)
                scheduled_cnt += 1

    return ImportResult(
        daily_checkins=len(daily_objs),
        plans=len(plan_objs),
        plan_followups=len(followup_objs),
        scheduled_messages=len(future_objs),
    )

