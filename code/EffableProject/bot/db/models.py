from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)

    first_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    language_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    future_messages: Mapped[list["FutureMessage"]] = relationship(back_populates="user", lazy="selectin")
    daily_checkins: Mapped[list["DailyCheckIn"]] = relationship(back_populates="user", lazy="selectin")
    plans: Mapped[list["Plan"]] = relationship(back_populates="user", lazy="selectin")
    plan_state: Mapped[Optional["UserPlanState"]] = relationship(
        back_populates="user",
        lazy="selectin",
        uselist=False,
    )


class FutureMessage(Base):
    __tablename__ = "future_messages"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_for: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    # scheduled | sent | cancelled
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled", index=True)
    # generic | plans_followup_question
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="generic", index=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    sent_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="future_messages")


class DailyCheckIn(Base):
    __tablename__ = "daily_checkins"
    __table_args__ = (
        UniqueConstraint("user_id", "checkin_date", name="uq_daily_checkins_user_date"),
        CheckConstraint("mood_score IS NULL OR (mood_score >= 0 AND mood_score <= 10)", name="ck_mood_score_0_10"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Дата "дня" (локальная дата сервера на момент вопроса).
    checkin_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # Статусы: scheduled | sent | answered | graded
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled", index=True)

    question_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    question_sent_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)

    response_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mood_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user: Mapped["User"] = relationship(back_populates="daily_checkins")


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (UniqueConstraint("user_id", "for_date", name="uq_plans_user_date"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Для какой даты пользователь формулировал планы (обычно "завтра").
    for_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # planned | cancelled | completed (опционально)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned", index=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user: Mapped["User"] = relationship(back_populates="plans")
    followup: Mapped[Optional["PlanFollowUp"]] = relationship(
        back_populates="plan", lazy="selectin", uselist=False
    )


class PlanFollowUp(Base):
    __tablename__ = "plan_followups"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    # Текст ответа пользователя "как прошли планы".
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="submitted", index=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    plan: Mapped["Plan"] = relationship(back_populates="followup")


class UserPlanState(Base):
    __tablename__ = "user_plan_states"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # normal | awaiting_plan | awaiting_followup
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="normal", index=True)

    # Храним только последний план пользователя.
    last_plan_for_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_plan_raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_plan_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user: Mapped["User"] = relationship(back_populates="plan_state")

