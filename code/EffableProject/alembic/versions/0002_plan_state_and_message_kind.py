from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "future_messages",
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="generic"),
    )
    op.create_index("ix_future_messages_kind", "future_messages", ["kind"], unique=False)

    op.create_table(
        "user_plan_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="normal"),
        sa.Column("last_plan_for_date", sa.Date(), nullable=True),
        sa.Column("last_plan_raw_text", sa.Text(), nullable=True),
        sa.Column("last_plan_summary", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_user_plan_states_user_id"),
    )
    op.create_index("ix_user_plan_states_user_id", "user_plan_states", ["user_id"], unique=False)
    op.create_index("ix_user_plan_states_mode", "user_plan_states", ["mode"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_user_plan_states_mode", table_name="user_plan_states")
    op.drop_index("ix_user_plan_states_user_id", table_name="user_plan_states")
    op.drop_table("user_plan_states")

    op.drop_index("ix_future_messages_kind", table_name="future_messages")
    op.drop_column("future_messages", "kind")

