"""add plain threads and messages tables

Revision ID: a7c2e1f3b9d4
Revises: 081d3553f2ed
Create Date: 2026-01-08 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Polar Custom Imports

# revision identifiers, used by Alembic.
revision = "a7c2e1f3b9d4"
down_revision = "081d3553f2ed"
branch_labels: tuple[str] | None = None
depends_on: tuple[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plain_threads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, index=True
        ),
        sa.Column("modified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True, index=True),
        sa.Column("plain_id", sa.String(), nullable=False, index=True),
        sa.Column("plain_customer_id", sa.String(), nullable=False),
        sa.Column("customer_email", sa.String(320), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("labels", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("assignee_id", sa.String(), nullable=True),
        sa.Column("first_message_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_message_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("plain_created_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("plain_updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "raw_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("plain_threads_user_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("plain_threads_organization_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("plain_threads_customer_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("plain_threads_pkey")),
        sa.UniqueConstraint("plain_id", name=op.f("plain_threads_plain_id_key")),
    )
    op.create_index(
        "ix_plain_threads_plain_customer_id",
        "plain_threads",
        ["plain_customer_id"],
        unique=False,
    )
    op.create_index(
        "ix_plain_threads_customer_email",
        "plain_threads",
        ["customer_email"],
        unique=False,
    )
    op.create_index(
        "ix_plain_threads_status",
        "plain_threads",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_plain_threads_user_id",
        "plain_threads",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_plain_threads_organization_id",
        "plain_threads",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_plain_threads_customer_id",
        "plain_threads",
        ["customer_id"],
        unique=False,
    )

    op.create_table(
        "plain_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, index=True
        ),
        sa.Column("modified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True, index=True),
        sa.Column("plain_id", sa.String(), nullable=False, index=True),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("sender_type", sa.String(), nullable=False),
        sa.Column("sender_email", sa.String(320), nullable=True),
        sa.Column("sender_name", sa.String(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=False, index=True),
        sa.Column(
            "raw_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["plain_threads.id"],
            name=op.f("plain_messages_thread_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("plain_messages_pkey")),
        sa.UniqueConstraint("plain_id", name=op.f("plain_messages_plain_id_key")),
    )
    op.create_index(
        "ix_plain_messages_thread_id",
        "plain_messages",
        ["thread_id"],
        unique=False,
    )
    op.create_index(
        "ix_plain_messages_direction",
        "plain_messages",
        ["direction"],
        unique=False,
    )
    op.create_index(
        "ix_plain_messages_channel",
        "plain_messages",
        ["channel"],
        unique=False,
    )
    op.create_index(
        "ix_plain_messages_sent_at",
        "plain_messages",
        ["sent_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_plain_messages_sent_at", table_name="plain_messages")
    op.drop_index("ix_plain_messages_channel", table_name="plain_messages")
    op.drop_index("ix_plain_messages_direction", table_name="plain_messages")
    op.drop_index("ix_plain_messages_thread_id", table_name="plain_messages")
    op.drop_table("plain_messages")

    op.drop_index("ix_plain_threads_customer_id", table_name="plain_threads")
    op.drop_index("ix_plain_threads_organization_id", table_name="plain_threads")
    op.drop_index("ix_plain_threads_user_id", table_name="plain_threads")
    op.drop_index("ix_plain_threads_status", table_name="plain_threads")
    op.drop_index("ix_plain_threads_customer_email", table_name="plain_threads")
    op.drop_index("ix_plain_threads_plain_customer_id", table_name="plain_threads")
    op.drop_table("plain_threads")
