from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    TIMESTAMP,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from polar.kit.db.models import RecordModel

if TYPE_CHECKING:
    from polar.models import Customer, Organization, User
    from polar.models.plain.message import PlainMessage


class PlainThreadStatus(StrEnum):
    open = "open"
    in_progress = "in_progress"
    waiting = "waiting"
    done = "done"
    snoozed = "snoozed"


class PlainThread(RecordModel):
    __tablename__ = "plain_threads"
    __table_args__ = (
        UniqueConstraint("plain_id"),
        Index("ix_plain_threads_plain_customer_id", "plain_customer_id"),
        Index("ix_plain_threads_customer_email", "customer_email"),
        Index("ix_plain_threads_status", "status"),
    )

    plain_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    plain_customer_id: Mapped[str] = mapped_column(String, nullable=False)
    customer_email: Mapped[str] = mapped_column(String(320), nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[PlainThreadStatus] = mapped_column(
        String, nullable=False, default=PlainThreadStatus.open
    )
    labels: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    priority: Mapped[int | None] = mapped_column(nullable=True, default=None)
    assignee_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    first_message_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, default=None
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, default=None
    )
    plain_created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, default=None
    )
    plain_updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, default=None
    )

    raw_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    user_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="set null"),
        nullable=True,
        index=True,
    )

    @declared_attr
    def user(cls) -> Mapped["User | None"]:
        return relationship("User", lazy="raise")

    organization_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("organizations.id", ondelete="set null"),
        nullable=True,
        index=True,
    )

    @declared_attr
    def organization(cls) -> Mapped["Organization | None"]:
        return relationship("Organization", lazy="raise")

    customer_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("customers.id", ondelete="set null"),
        nullable=True,
        index=True,
    )

    @declared_attr
    def customer(cls) -> Mapped["Customer | None"]:
        return relationship("Customer", lazy="raise")

    @declared_attr
    def messages(cls) -> Mapped[list["PlainMessage"]]:
        return relationship(
            "PlainMessage",
            back_populates="thread",
            lazy="raise",
            cascade="all, delete-orphan",
        )
