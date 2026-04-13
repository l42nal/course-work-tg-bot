from datetime import datetime

import bot.scheduler as scheduler


class _FixedDateTime(datetime):
    _fixed_now: datetime

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        # scheduler._seconds_until_next_21 uses naive local datetime.now()
        return cls._fixed_now


def _seconds_at(dt: datetime, monkeypatch) -> float:
    _FixedDateTime._fixed_now = dt
    monkeypatch.setattr(scheduler, "datetime", _FixedDateTime)
    return scheduler._seconds_until_next_21()


def test_seconds_until_next_21_from_20_00(monkeypatch) -> None:
    secs = _seconds_at(datetime(2026, 4, 13, 20, 0, 0), monkeypatch)
    assert secs == 60 * 60


def test_seconds_until_next_21_from_exact_21_00(monkeypatch) -> None:
    secs = _seconds_at(datetime(2026, 4, 13, 21, 0, 0), monkeypatch)
    assert secs == 24 * 60 * 60


def test_seconds_until_next_21_from_22_00(monkeypatch) -> None:
    secs = _seconds_at(datetime(2026, 4, 13, 22, 0, 0), monkeypatch)
    assert secs == 23 * 60 * 60

