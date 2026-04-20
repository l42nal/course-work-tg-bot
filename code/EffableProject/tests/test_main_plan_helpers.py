from bot.services.checkin_service import _build_followup_question, _summarize_plan_text


def test_summarize_plan_text_normalizes_spaces() -> None:
    assert _summarize_plan_text("  a   b \n c\t") == "a b c"


def test_summarize_plan_text_empty_or_spaces() -> None:
    assert _summarize_plan_text("") == ""
    assert _summarize_plan_text("   \n\t  ") == ""


def test_summarize_plan_text_len_le_140_returns_full() -> None:
    s = "x" * 140
    assert _summarize_plan_text(s) == s


def test_summarize_plan_text_len_gt_140_truncates_with_ellipsis() -> None:
    s = ("x" * 200) + "   "
    out = _summarize_plan_text(s)
    assert len(out) == 140
    assert out.endswith("...")
    assert out[:137] == "x" * 137


def test_build_followup_question_format() -> None:
    q = _build_followup_question("Сделать задачу")
    assert q.startswith("Вчера ты планировал: Сделать задачу\n")
    assert "Как у тебя получилось это реализовать сегодня?" in q

