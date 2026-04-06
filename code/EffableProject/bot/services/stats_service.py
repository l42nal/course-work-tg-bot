from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..db import crud


@dataclass(frozen=True)
class MoodEntry:
    day: date
    score: int


@dataclass(frozen=True)
class MoodStats:
    total_days: int
    avg_all_time: float | None
    avg_last_7_days: float | None
    best_day: date | None
    best_score: int | None
    worst_day: date | None
    worst_score: int | None
    mood_today: int | None
    mood_yesterday: int | None
    current_streak_days: int
    longest_streak_days: int


def _avg(scores: list[int]) -> float | None:
    if not scores:
        return None
    return sum(scores) / len(scores)


def _compute_streaks(days_sorted: list[date]) -> tuple[int, int]:
    """
    days_sorted: уникальные даты (asc), только дни с реальными оценками.
    Возвращает (current_streak, longest_streak).
    """
    if not days_sorted:
        return 0, 0

    # Longest streak
    longest = 1
    cur = 1
    for i in range(1, len(days_sorted)):
        if days_sorted[i] == days_sorted[i - 1] + timedelta(days=1):
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 1

    # Current streak (ending at most recent entry)
    current = 1
    for i in range(len(days_sorted) - 1, 0, -1):
        if days_sorted[i] == days_sorted[i - 1] + timedelta(days=1):
            current += 1
        else:
            break

    return current, longest


async def get_user_mood_entries(telegram_user_id: int) -> list[MoodEntry]:
    rows = await crud.list_mood_scores(telegram_user_id)
    return [MoodEntry(day=r.day, score=r.score) for r in rows]


async def get_user_mood_stats(telegram_user_id: int, today: date) -> MoodStats:
    entries = await get_user_mood_entries(telegram_user_id)
    total = len(entries)

    if total == 0:
        return MoodStats(
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

    scores = [e.score for e in entries]
    avg_all = _avg(scores)

    start_7 = today - timedelta(days=6)
    scores_7 = [e.score for e in entries if e.day >= start_7 and e.day <= today]
    avg_7 = _avg(scores_7)

    max_score = max(scores)
    min_score = min(scores)

    best_day = max((e.day for e in entries if e.score == max_score), default=None)
    worst_day = max((e.day for e in entries if e.score == min_score), default=None)

    mood_today = next((e.score for e in reversed(entries) if e.day == today), None)
    mood_yesterday = next((e.score for e in reversed(entries) if e.day == today - timedelta(days=1)), None)

    days_sorted = sorted({e.day for e in entries})
    current_streak, longest_streak = _compute_streaks(days_sorted)

    return MoodStats(
        total_days=total,
        avg_all_time=avg_all,
        avg_last_7_days=avg_7,
        best_day=best_day,
        best_score=max_score,
        worst_day=worst_day,
        worst_score=min_score,
        mood_today=mood_today,
        mood_yesterday=mood_yesterday,
        current_streak_days=current_streak,
        longest_streak_days=longest_streak,
    )


def format_mood_stats_text(stats: MoodStats) -> str:
    if stats.total_days == 0:
        return (
            "Пока у тебя нет сохранённых оценок настроения.\n\n"
            "Попробуй сначала поставить оценку в ежедневном чек-ине, "
            "или используй debug-команды, чтобы быстро наполнить данные."
        )

    def fmt_day(d: date | None) -> str:
        return d.isoformat() if d else "—"

    def fmt_score(s: int | None) -> str:
        return str(s) if s is not None else "—"

    def fmt_avg(v: float | None) -> str:
        return f"{v:.2f}" if v is not None else "—"

    lines: list[str] = []
    lines.append("Твоя статистика настроения")
    lines.append("")
    lines.append(f"Заполнено дней: {stats.total_days}")
    lines.append(f"Среднее за всё время: {fmt_avg(stats.avg_all_time)}")
    lines.append(f"Среднее за последние 7 дней: {fmt_avg(stats.avg_last_7_days)}")
    lines.append("")
    lines.append(f"Лучший день (последний максимум): {fmt_day(stats.best_day)} — {fmt_score(stats.best_score)}")
    lines.append(f"Худший день (последний минимум): {fmt_day(stats.worst_day)} — {fmt_score(stats.worst_score)}")
    lines.append("")
    lines.append(f"Сегодня: {fmt_score(stats.mood_today)}   Вчера: {fmt_score(stats.mood_yesterday)}")
    lines.append(f"Серия ответов: {stats.current_streak_days} дн. подряд (рекорд: {stats.longest_streak_days})")
    return "\n".join(lines)

