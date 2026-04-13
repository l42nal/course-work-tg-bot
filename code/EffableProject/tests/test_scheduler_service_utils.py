from datetime import datetime, timedelta, timezone

import pytest

from bot.services import scheduler_service


def test_ensure_aware_utc_naive_assumed_utc() -> None:
    dt = datetime(2026, 4, 13, 12, 0, 0)  # naive
    out = scheduler_service._ensure_aware_utc(dt)
    assert out.tzinfo == timezone.utc
    assert out.hour == 12


def test_ensure_aware_utc_converts_to_utc() -> None:
    tz = timezone(timedelta(hours=3))
    dt = datetime(2026, 4, 13, 12, 0, 0, tzinfo=tz)
    out = scheduler_service._ensure_aware_utc(dt)
    assert out.tzinfo == timezone.utc
    assert out.hour == 9


def test_get_scheduler_requires_init() -> None:
    # Reset the module-level singleton for deterministic test.
    scheduler_service._service = None  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError):
        scheduler_service.get_scheduler()

