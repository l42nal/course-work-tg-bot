from __future__ import annotations

import os
from typing import Iterable, Optional

import sqlalchemy as sa
from alembic import op
from cryptography.fernet import Fernet


# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


PREFIX = "enc:v1:"
KEY_ENV = "DATA_ENCRYPTION_FERNET_KEY"


def _get_fernet() -> Fernet:
    key = (os.getenv(KEY_ENV) or "").strip()
    if not key:
        raise RuntimeError(
            f"Missing env var {KEY_ENV}. "
            "It is required to migrate plaintext -> encrypted text fields."
        )
    return Fernet(key.encode("utf-8"))


def _encrypt_if_needed(fernet: Fernet, v: Optional[str]) -> Optional[str]:
    if v is None or v == "":
        return v
    if v.startswith(PREFIX):
        return v
    token = fernet.encrypt(v.encode("utf-8")).decode("utf-8")
    return f"{PREFIX}{token}"


def _iter_rows(conn, *, table: str, pk: str, cols: Iterable[str]) -> list[tuple]:
    columns = ", ".join([pk] + list(cols))
    return list(conn.execute(sa.text(f"SELECT {columns} FROM public.{table}")).fetchall())


def upgrade() -> None:
    conn = op.get_bind()
    fernet = _get_fernet()

    # future_messages.message_text
    for row in _iter_rows(conn, table="future_messages", pk="id", cols=["message_text"]):
        row_id, message_text = row[0], row[1]
        new_val = _encrypt_if_needed(fernet, message_text)
        if new_val != message_text:
            conn.execute(
                sa.text("UPDATE public.future_messages SET message_text = :v WHERE id = :id"),
                {"v": new_val, "id": row_id},
            )

    # plans.raw_text, plans.summary_text
    for row in _iter_rows(conn, table="plans", pk="id", cols=["raw_text", "summary_text"]):
        row_id, raw_text, summary_text = row[0], row[1], row[2]
        new_raw = _encrypt_if_needed(fernet, raw_text)
        new_sum = _encrypt_if_needed(fernet, summary_text)
        if new_raw != raw_text or new_sum != summary_text:
            conn.execute(
                sa.text(
                    "UPDATE public.plans SET raw_text = :raw_text, summary_text = :summary_text WHERE id = :id"
                ),
                {"raw_text": new_raw, "summary_text": new_sum, "id": row_id},
            )

    # plan_followups.response_text, plan_followups.summary_text
    for row in _iter_rows(conn, table="plan_followups", pk="id", cols=["response_text", "summary_text"]):
        row_id, response_text, summary_text = row[0], row[1], row[2]
        new_resp = _encrypt_if_needed(fernet, response_text)
        new_sum = _encrypt_if_needed(fernet, summary_text)
        if new_resp != response_text or new_sum != summary_text:
            conn.execute(
                sa.text(
                    "UPDATE public.plan_followups SET response_text = :response_text, summary_text = :summary_text WHERE id = :id"
                ),
                {"response_text": new_resp, "summary_text": new_sum, "id": row_id},
            )

    # user_plan_states.last_plan_raw_text, user_plan_states.last_plan_summary
    for row in _iter_rows(
        conn,
        table="user_plan_states",
        pk="id",
        cols=["last_plan_raw_text", "last_plan_summary"],
    ):
        row_id, last_plan_raw_text, last_plan_summary = row[0], row[1], row[2]
        new_raw = _encrypt_if_needed(fernet, last_plan_raw_text)
        new_sum = _encrypt_if_needed(fernet, last_plan_summary)
        if new_raw != last_plan_raw_text or new_sum != last_plan_summary:
            conn.execute(
                sa.text(
                    "UPDATE public.user_plan_states SET last_plan_raw_text = :last_plan_raw_text, last_plan_summary = :last_plan_summary WHERE id = :id"
                ),
                {"last_plan_raw_text": new_raw, "last_plan_summary": new_sum, "id": row_id},
            )


def downgrade() -> None:
    # Data migration is intentionally non-reversible without decrypting.
    # We do not provide automatic plaintext rollback.
    pass

