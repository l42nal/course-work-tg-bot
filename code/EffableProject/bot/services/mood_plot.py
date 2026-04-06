from __future__ import annotations

import calendar
import os
import tempfile
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class MonthMoodPoint:
    day: date
    score: int


_RU_MONTHS = {
    1: "январь",
    2: "февраль",
    3: "март",
    4: "апрель",
    5: "май",
    6: "июнь",
    7: "июль",
    8: "август",
    9: "сентябрь",
    10: "октябрь",
    11: "ноябрь",
    12: "декабрь",
}


def build_month_mood_plot_png(
    points: list[MonthMoodPoint],
    *,
    year: int,
    month: int,
) -> str | None:
    """
    Строит график и возвращает путь к PNG.
    Если points пустой — возвращает None.
    """
    if not points:
        return None

    # Import inside to avoid requiring matplotlib for non-plot use-cases.
    os.environ.setdefault("MPLCONFIGDIR", tempfile.mkdtemp(prefix="mplconfig_"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    days = [p.day.day for p in points]
    scores = [p.score for p in points]

    try:
        plt.style.use("seaborn-v0_8")
    except Exception:
        pass

    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=160)
    ax.plot(
        days,
        scores,
        marker="o",
        linewidth=2.2,
        markersize=5.5,
        color="#2b6cb0",
    )

    ax.set_ylim(0, 10)
    ax.set_yticks(range(0, 11, 1))

    month_name = _RU_MONTHS.get(month, calendar.month_name[month]).capitalize()
    ax.set_title(f"Настроение — {month_name} {year}", fontsize=14, pad=12)
    ax.set_xlabel("День месяца")
    ax.set_ylabel("Оценка настроения")

    ax.grid(True, which="major", axis="both", linestyle="--", linewidth=0.6, alpha=0.5)

    # Покажем тики только на реально существующих днях (без пропусков).
    ax.set_xticks(days)
    ax.set_xticklabels([str(d) for d in days], rotation=0)

    fig.tight_layout()

    tmp = tempfile.NamedTemporaryFile(prefix="mood_plot_", suffix=".png", delete=False)
    tmp_path = tmp.name
    tmp.close()

    fig.savefig(tmp_path, bbox_inches="tight")
    plt.close(fig)
    return tmp_path

