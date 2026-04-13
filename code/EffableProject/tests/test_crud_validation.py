import pytest
from datetime import date

from bot.db import crud


@pytest.mark.asyncio
async def test_set_plan_mode_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        await crud.set_plan_mode(telegram_user_id=1, mode="wat")


@pytest.mark.asyncio
async def test_save_daily_checkin_mood_score_rejects_out_of_range_low() -> None:
    with pytest.raises(ValueError):
        await crud.save_daily_checkin_mood_score(
            telegram_user_id=1,
            checkin_date=date(2026, 4, 13),
            mood_score=-1,
            response_text=None,
        )


@pytest.mark.asyncio
async def test_save_daily_checkin_mood_score_rejects_out_of_range_high() -> None:
    with pytest.raises(ValueError):
        await crud.save_daily_checkin_mood_score(
            telegram_user_id=1,
            checkin_date=date(2026, 4, 13),
            mood_score=11,
            response_text=None,
        )

