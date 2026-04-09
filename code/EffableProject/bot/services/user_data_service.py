from __future__ import annotations

from sqlalchemy import delete

from ..db.models import User
from ..db.session import session_scope


async def reset_user_data(telegram_user_id: int) -> bool:
    """
    Полностью удаляет пользователя и все его данные из БД.

    Механизм:
    - удаляем строку из `users` по telegram_user_id
    - связанные таблицы очищаются каскадно через FK ON DELETE CASCADE
      (daily_checkins, plans -> plan_followups, future_messages, user_plan_states).

    Возвращает:
    - True, если пользователь существовал и был удалён
    - False, если пользователя в БД не было
    """
    async with session_scope() as session:
        async with session.begin():
            res = await session.execute(
                delete(User).where(User.telegram_user_id == telegram_user_id)
            )
            deleted = int(res.rowcount or 0)
            return deleted > 0

