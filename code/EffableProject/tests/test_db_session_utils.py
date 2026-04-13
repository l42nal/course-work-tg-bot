import os

import pytest

from bot.db import session as db_session


def test_build_database_url_prefers_database_url(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/db")
    assert db_session.build_database_url() == "postgresql+asyncpg://u:p@h:5432/db"


def test_build_database_url_from_postgres_parts_with_defaults(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # Clear parts to force defaults
    for key in [
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ]:
        monkeypatch.delenv(key, raising=False)

    assert (
        db_session.build_database_url()
        == "postgresql+asyncpg://postgres:postgres@localhost:5432/effable_bot"
    )


def test_build_database_url_from_postgres_parts_custom(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "dbhost")
    monkeypatch.setenv("POSTGRES_PORT", "5555")
    monkeypatch.setenv("POSTGRES_DB", "mydb")
    monkeypatch.setenv("POSTGRES_USER", "me")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")

    assert (
        db_session.build_database_url()
        == "postgresql+asyncpg://me:secret@dbhost:5555/mydb"
    )


@pytest.mark.asyncio
async def test_session_scope_requires_init_engine() -> None:
    # Ensure deterministic state
    db_session._sessionmaker = None  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError):
        async with db_session.session_scope():
            raise AssertionError("unreachable")

