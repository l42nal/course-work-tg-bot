from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import User

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def build_database_url() -> str:
    """
    Строит строку подключения к Postgres.

    Поддерживает:
    - `DATABASE_URL` (предпочтительно)
    - или раздельные переменные `POSTGRES_*`
    """
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return db_url

    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "effable_bot")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")

    # Важно: для async SQLAlchemy нужен драйвер asyncpg.
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"


def init_engine() -> None:
    """Инициализирует AsyncEngine и sessionmaker (вызывать один раз при старте)."""
    global _engine, _sessionmaker
    if _engine is not None and _sessionmaker is not None:
        return

    db_url = build_database_url()
    _engine = create_async_engine(
        db_url,
        echo=False,
        pool_pre_ping=True,
    )
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, autoflush=False)


async def shutdown_engine() -> None:
    """Корректно закрывает пул соединений."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("БД не инициализирована: вызови init_engine() при старте.")
    async with _sessionmaker() as session:
        yield session


async def ping_db() -> None:
    """Простой healthcheck, чтобы убедиться, что БД доступна."""
    async with session_scope() as session:
        await session.execute(text("SELECT 1"))


async def ensure_users_telegram_id_bigint() -> None:
    """
    Делает схему совместимой с текущими Telegram user_id (которые могут превышать int32).
    Выполняет ALTER только если текущий тип колонки `users.telegram_user_id` == INTEGER.
    """
    async with session_scope() as session:
        async with session.begin():
            row = (
                await session.execute(
                    text(
                        """
                        SELECT data_type
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'users'
                          AND column_name = 'telegram_user_id'
                        """
                    )
                )
            ).one_or_none()

            data_type = str(row[0]) if row is not None and row[0] is not None else None

            if data_type == "integer":
                await session.execute(
                    text("ALTER TABLE public.users ALTER COLUMN telegram_user_id TYPE BIGINT")
                )


async def upsert_user(
    telegram_user_id: int,
    first_name: Optional[str],
    last_name: Optional[str],
    username: Optional[str],
    language_code: Optional[str],
) -> None:
    async with session_scope() as session:
        stmt = select(User).where(User.telegram_user_id == telegram_user_id)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

        if user is None:
            session.add(
                User(
                    telegram_user_id=telegram_user_id,
                    first_name=first_name,
                    last_name=last_name,
                    username=username,
                    language_code=language_code,
                )
            )
        else:
            # Обновляем актуальные поля (если Telegram прислал новые).
            user.first_name = first_name
            user.last_name = last_name
            user.username = username
            user.language_code = language_code

        await session.commit()


async def load_known_user_ids() -> set[int]:
    async with session_scope() as session:
        stmt = select(User.telegram_user_id)
        res = await session.execute(stmt)
        rows = res.all()
        return {int(r[0]) for r in rows}


async def get_user_by_telegram_id(telegram_user_id: int) -> User | None:
    async with session_scope() as session:
        stmt = select(User).where(User.telegram_user_id == telegram_user_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

