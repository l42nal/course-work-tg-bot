from datetime import date

from bot.services import stats_service
from bot.services.stats_service import MoodStats, format_mood_stats_text


def test_avg_empty_returns_none() -> None:
    assert stats_service._avg([]) is None


def test_avg_returns_mean() -> None:
    assert stats_service._avg([1, 2, 3]) == 2.0


def test_compute_streaks_empty() -> None:
    assert stats_service._compute_streaks([]) == (0, 0)


def test_compute_streaks_single_day() -> None:
    assert stats_service._compute_streaks([date(2026, 4, 10)]) == (1, 1)


def test_compute_streaks_with_gaps_current_and_longest() -> None:
    # Longest = 3 (10-12), current = 2 (20-21)
    days = [
        date(2026, 4, 10),
        date(2026, 4, 11),
        date(2026, 4, 12),
        date(2026, 4, 20),
        date(2026, 4, 21),
    ]
    assert stats_service._compute_streaks(days) == (2, 3)


def test_format_mood_stats_text_no_data() -> None:
    stats = MoodStats(
        total_days=0,
        avg_all_time=None,
        avg_last_7_days=None,
        best_day=None,
        best_score=None,
        worst_day=None,
        worst_score=None,
        mood_today=None,
        mood_yesterday=None,
        current_streak_days=0,
        longest_streak_days=0,
    )
    text = format_mood_stats_text(stats)
    assert "Пока у тебя нет сохранённых оценок настроения" in text


def test_format_mood_stats_text_with_data_formats_avgs_and_days() -> None:
    stats = MoodStats(
        total_days=10,
        avg_all_time=5.0,
        avg_last_7_days=6.25,
        best_day=date(2026, 4, 10),
        best_score=9,
        worst_day=date(2026, 4, 3),
        worst_score=2,
        mood_today=7,
        mood_yesterday=6,
        current_streak_days=3,
        longest_streak_days=5,
    )
    text = format_mood_stats_text(stats)
    assert "Твоя статистика настроения" in text
    assert "Заполнено дней: 10" in text
    assert "Среднее за всё время: 5.00" in text
    assert "Среднее за последние 7 дней: 6.25" in text
    assert "2026-04-10" in text
    assert "2026-04-03" in text
    assert "Сегодня: 7" in text
    assert "Вчера: 6" in text
    assert "Серия ответов: 3 дн. подряд (рекорд: 5)" in text

