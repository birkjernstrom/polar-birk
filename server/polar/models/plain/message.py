from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    TIMESTAMP,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from polar.kit.db.models import RecordModel

if TYPE_CHECKING:
    from polar.models.plain.thread import PlainThread


class PlainMessageDirection(StrEnum):
    inbound = "inbound"
    outbound = "outbound"


class PlainMessageChannel(StrEnum):
    email = "email"
    chat = "chat"
    slack = "slack"


class PlainMessageSenderType(StrEnum):
    customer = "customer"
    user = "user"
    machine_user = "machine_user"
    system = "system"


class PlainMessage(RecordModel):
    __tablename__ = "plain_messages"
    __table_args__ = (
        UniqueConstraint("plain_id"),
        Index("ix_plain_messages_thread_id", "thread_id"),
        Index("ix_plain_messages_direction", "direction"),
        Index("ix_plain_messages_channel", "channel"),
        Index("ix_plain_messages_sent_at", "sent_at"),
    )

    plain_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    direction: Mapped[PlainMessageDirection] = mapped_column(String, nullable=False)
    channel: Mapped[PlainMessageChannel] = mapped_column(String, nullable=False)
    sender_type: Mapped[PlainMessageSenderType] = mapped_column(String, nullable=False)
    sender_email: Mapped[str | None] = mapped_column(
        String(320), nullable=True, default=None
    )
    sender_name: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    content: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    sent_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, index=True
    )

    raw_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    thread_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("plain_threads.id", ondelete="cascade"),
        nullable=False,
        index=True,
    )

    @declared_attr
    def thread(cls) -> Mapped["PlainThread"]:
        return relationship("PlainThread", back_populates="messages", lazy="raise")
