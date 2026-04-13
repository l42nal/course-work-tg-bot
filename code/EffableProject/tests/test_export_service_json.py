import json
import uuid
from datetime import date, datetime, timezone

from bot.services.export_service import _to_jsonable, dumps_user_export


class Weird:
    def __str__(self) -> str:  # pragma: no cover (covered indirectly)
        return "WEIRD"


def test_to_jsonable_primitives_and_none() -> None:
    assert _to_jsonable(None) is None
    assert _to_jsonable("x") == "x"
    assert _to_jsonable(1) == 1
    assert _to_jsonable(1.5) == 1.5
    assert _to_jsonable(True) is True


def test_to_jsonable_uuid_date_datetime() -> None:
    u = uuid.UUID("00000000-0000-0000-0000-000000000001")
    assert _to_jsonable(u) == "00000000-0000-0000-0000-000000000001"

    d = date(2026, 4, 13)
    assert _to_jsonable(d) == "2026-04-13"

    dt = datetime(2026, 4, 13, 12, 30, 0, tzinfo=timezone.utc)
    assert _to_jsonable(dt) == dt.isoformat()


def test_to_jsonable_collections_and_unknown_type() -> None:
    payload = {
        1: {uuid.UUID("00000000-0000-0000-0000-000000000002"): date(2026, 1, 1)},
        "list": [date(2026, 1, 2), uuid.UUID("00000000-0000-0000-0000-000000000003")],
        "set": {1, 2},
        "unknown": Weird(),
    }
    out = _to_jsonable(payload)

    assert out["1"]["00000000-0000-0000-0000-000000000002"] == "2026-01-01"
    assert out["list"][0] == "2026-01-02"
    assert out["list"][1] == "00000000-0000-0000-0000-000000000003"
    assert sorted(out["set"]) == [1, 2]
    assert out["unknown"] == "WEIRD"


def test_dumps_user_export_is_valid_json_and_keeps_cyrillic() -> None:
    exported = {
        "meta": {"schema_version": 1, "exported_at": datetime(2026, 4, 13, 0, 0, tzinfo=timezone.utc)},
        "text": "Привет мир",
        "id": uuid.UUID("00000000-0000-0000-0000-000000000010"),
        "day": date(2026, 4, 13),
    }
    s = dumps_user_export(exported)

    # Cyrillic should not be escaped when ensure_ascii=False
    assert "Привет мир" in s
    assert "\\u041f" not in s

    parsed = json.loads(s)
    assert parsed["text"] == "Привет мир"
    assert parsed["id"] == "00000000-0000-0000-0000-000000000010"
    assert parsed["day"] == "2026-04-13"

