from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# Customer Card schemas (existing)


class CustomerCardKey(StrEnum):
    user = "user"
    organization = "organization"
    customer = "customer"
    order = "order"
    snippets = "snippets"


class CustomerCardCustomer(BaseModel):
    id: str
    email: str
    externalId: str | None


class CustomerCardThread(BaseModel):
    id: str
    externalId: str | None


class CustomerCardsRequest(BaseModel):
    cardKeys: list[CustomerCardKey]
    customer: CustomerCardCustomer
    thread: CustomerCardThread | None


class CustomerCard(BaseModel):
    key: CustomerCardKey
    timeToLiveSeconds: int
    components: list[dict[str, Any]] | None


class CustomerCardsResponse(BaseModel):
    cards: list[CustomerCard]


# Webhook Event schemas


class PlainWebhookEventType(StrEnum):
    thread_created = "thread.thread_created"
    thread_status_transitioned = "thread.status_transitioned"
    thread_assignment_transitioned = "thread.assignment_transitioned"
    thread_labels_changed = "thread.labels_changed"
    thread_priority_changed = "thread.priority_changed"
    chat_sent = "thread.chat_sent"
    chat_received = "thread.chat_received"
    email_sent = "thread.email_sent"
    email_received = "thread.email_received"
    note_created = "thread.note_created"
    slack_message_sent = "thread.slack_message_sent"
    slack_message_received = "thread.slack_message_received"
    customer_created = "customer.customer_created"
    customer_updated = "customer.customer_updated"
    customer_deleted = "customer.customer_deleted"


class PlainThreadStatus(StrEnum):
    todo = "TODO"
    snoozed = "SNOOZED"
    in_progress = "IN_PROGRESS"
    done = "DONE"


class PlainWebhookCustomer(BaseModel):
    id: str
    email: str | None = Field(default=None, alias="email")
    external_id: str | None = Field(default=None, alias="externalId")
    full_name: str | None = Field(default=None, alias="fullName")
    short_name: str | None = Field(default=None, alias="shortName")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")

    class Config:
        populate_by_name = True


class PlainWebhookLabel(BaseModel):
    id: str
    name: str
    icon: str | None = None


class PlainWebhookAssignee(BaseModel):
    id: str
    email: str | None = None
    full_name: str | None = Field(default=None, alias="fullName")

    class Config:
        populate_by_name = True


class PlainWebhookThread(BaseModel):
    id: str
    external_id: str | None = Field(default=None, alias="externalId")
    customer: PlainWebhookCustomer
    title: str | None = None
    preview_text: str | None = Field(default=None, alias="previewText")
    status: PlainThreadStatus | None = None
    status_changed_at: datetime | None = Field(default=None, alias="statusChangedAt")
    labels: list[PlainWebhookLabel] = Field(default_factory=list)
    priority: int | None = None
    assignee: PlainWebhookAssignee | None = None
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")

    class Config:
        populate_by_name = True


class PlainWebhookEmailParticipant(BaseModel):
    email: str
    name: str | None = None


class PlainWebhookEmailContent(BaseModel):
    subject: str | None = None
    text_content: str | None = Field(default=None, alias="textContent")
    markdown_content: str | None = Field(default=None, alias="markdownContent")
    from_participant: PlainWebhookEmailParticipant | None = Field(
        default=None, alias="from"
    )
    to_participants: list[PlainWebhookEmailParticipant] = Field(
        default_factory=list, alias="to"
    )
    cc_participants: list[PlainWebhookEmailParticipant] = Field(
        default_factory=list, alias="cc"
    )

    class Config:
        populate_by_name = True


class PlainWebhookChatContent(BaseModel):
    text: str | None = None
    markdown_text: str | None = Field(default=None, alias="markdownText")

    class Config:
        populate_by_name = True


class PlainWebhookSlackContent(BaseModel):
    text: str | None = None

    class Config:
        populate_by_name = True


class PlainWebhookMessageSender(BaseModel):
    id: str
    type: str  # "CUSTOMER", "USER", "MACHINE_USER", "SYSTEM"
    email: str | None = None
    full_name: str | None = Field(default=None, alias="fullName")

    class Config:
        populate_by_name = True


class PlainWebhookEmail(BaseModel):
    id: str
    thread: PlainWebhookThread
    sender: PlainWebhookMessageSender | None = None
    email_content: PlainWebhookEmailContent | None = Field(
        default=None, alias="emailContent"
    )
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")

    class Config:
        populate_by_name = True


class PlainWebhookChat(BaseModel):
    id: str
    thread: PlainWebhookThread
    sender: PlainWebhookMessageSender | None = None
    chat_content: PlainWebhookChatContent | None = Field(
        default=None, alias="chatContent"
    )
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")

    class Config:
        populate_by_name = True


class PlainWebhookSlackMessage(BaseModel):
    id: str
    thread: PlainWebhookThread
    sender: PlainWebhookMessageSender | None = None
    slack_content: PlainWebhookSlackContent | None = Field(
        default=None, alias="slackContent"
    )
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")

    class Config:
        populate_by_name = True


class PlainWebhookNote(BaseModel):
    id: str
    thread: PlainWebhookThread
    text: str | None = None
    markdown_text: str | None = Field(default=None, alias="markdownText")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")

    class Config:
        populate_by_name = True


class PlainWebhookEventPayload(BaseModel):
    event_type: PlainWebhookEventType = Field(alias="eventType")
    thread: PlainWebhookThread | None = None
    previous_thread: PlainWebhookThread | None = Field(
        default=None, alias="previousThread"
    )
    email: PlainWebhookEmail | None = None
    chat: PlainWebhookChat | None = None
    slack_message: PlainWebhookSlackMessage | None = Field(
        default=None, alias="slackMessage"
    )
    note: PlainWebhookNote | None = None
    customer: PlainWebhookCustomer | None = None
    previous_customer: PlainWebhookCustomer | None = Field(
        default=None, alias="previousCustomer"
    )

    class Config:
        populate_by_name = True


class PlainWebhookRequest(BaseModel):
    id: str
    webhook_metadata: dict[str, Any] = Field(alias="webhookMetadata")
    timestamp: datetime
    workspace_id: str = Field(alias="workspaceId")
    payload: PlainWebhookEventPayload

    class Config:
        populate_by_name = True
