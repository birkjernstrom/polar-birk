from datetime import datetime, timezone
from typing import Any

import pytest_asyncio

from polar.integrations.plain.schemas import (
    PlainThreadStatus,
    PlainWebhookAssignee,
    PlainWebhookChat,
    PlainWebhookChatContent,
    PlainWebhookCustomer,
    PlainWebhookEmail,
    PlainWebhookEmailContent,
    PlainWebhookEventPayload,
    PlainWebhookEventType,
    PlainWebhookLabel,
    PlainWebhookMessageSender,
    PlainWebhookRequest,
    PlainWebhookSlackContent,
    PlainWebhookSlackMessage,
    PlainWebhookThread,
)
from polar.models import Organization, PlainThread, User
from tests.fixtures.database import SaveFixture


def build_plain_customer(
    *,
    id: str = "c_01ABC123",
    email: str = "customer@example.com",
    external_id: str | None = None,
    full_name: str | None = "Test Customer",
) -> PlainWebhookCustomer:
    return PlainWebhookCustomer(
        id=id,
        email=email,
        external_id=external_id,
        full_name=full_name,
        short_name=full_name.split()[0] if full_name else None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def build_plain_thread(
    *,
    id: str = "th_01ABC123",
    customer: PlainWebhookCustomer | None = None,
    title: str | None = "Test Support Thread",
    status: PlainThreadStatus = PlainThreadStatus.todo,
    labels: list[PlainWebhookLabel] | None = None,
    priority: int | None = None,
    assignee: PlainWebhookAssignee | None = None,
) -> PlainWebhookThread:
    if customer is None:
        customer = build_plain_customer()

    return PlainWebhookThread(
        id=id,
        customer=customer,
        title=title,
        status=status,
        labels=labels or [],
        priority=priority,
        assignee=assignee,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def build_plain_email(
    *,
    id: str = "em_01ABC123",
    thread: PlainWebhookThread | None = None,
    sender_type: str = "CUSTOMER",
    sender_email: str = "customer@example.com",
    sender_name: str | None = "Test Customer",
    subject: str | None = "Test Subject",
    text_content: str | None = "This is a test email message.",
) -> PlainWebhookEmail:
    if thread is None:
        thread = build_plain_thread()

    sender = PlainWebhookMessageSender(
        id="u_sender",
        type=sender_type,
        email=sender_email,
        full_name=sender_name,
    )

    email_content = PlainWebhookEmailContent(
        subject=subject,
        text_content=text_content,
    )

    return PlainWebhookEmail(
        id=id,
        thread=thread,
        sender=sender,
        email_content=email_content,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def build_plain_chat(
    *,
    id: str = "ch_01ABC123",
    thread: PlainWebhookThread | None = None,
    sender_type: str = "CUSTOMER",
    sender_email: str = "customer@example.com",
    sender_name: str | None = "Test Customer",
    text: str | None = "This is a test chat message.",
) -> PlainWebhookChat:
    if thread is None:
        thread = build_plain_thread()

    sender = PlainWebhookMessageSender(
        id="u_sender",
        type=sender_type,
        email=sender_email,
        full_name=sender_name,
    )

    chat_content = PlainWebhookChatContent(text=text)

    return PlainWebhookChat(
        id=id,
        thread=thread,
        sender=sender,
        chat_content=chat_content,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def build_plain_slack_message(
    *,
    id: str = "sl_01ABC123",
    thread: PlainWebhookThread | None = None,
    sender_type: str = "CUSTOMER",
    sender_email: str = "customer@example.com",
    sender_name: str | None = "Test Customer",
    text: str | None = "This is a test Slack message.",
) -> PlainWebhookSlackMessage:
    if thread is None:
        thread = build_plain_thread()

    sender = PlainWebhookMessageSender(
        id="u_sender",
        type=sender_type,
        email=sender_email,
        full_name=sender_name,
    )

    slack_content = PlainWebhookSlackContent(text=text)

    return PlainWebhookSlackMessage(
        id=id,
        thread=thread,
        sender=sender,
        slack_content=slack_content,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def build_plain_webhook_request(
    *,
    id: str = "wh_01ABC123",
    event_type: PlainWebhookEventType,
    thread: PlainWebhookThread | None = None,
    email: PlainWebhookEmail | None = None,
    chat: PlainWebhookChat | None = None,
    slack_message: PlainWebhookSlackMessage | None = None,
    workspace_id: str = "ws_01ABC123",
) -> PlainWebhookRequest:
    payload = PlainWebhookEventPayload(
        eventType=event_type,
        thread=thread,
        email=email,
        chat=chat,
        slackMessage=slack_message,
    )

    return PlainWebhookRequest(
        id=id,
        webhookMetadata={},
        timestamp=datetime.now(timezone.utc),
        workspaceId=workspace_id,
        payload=payload,
    )


@pytest_asyncio.fixture
async def plain_thread(
    save_fixture: SaveFixture,
    user: User,
    organization: Organization,
) -> PlainThread:
    thread = PlainThread(
        plain_id="th_existing_123",
        plain_customer_id="c_existing_123",
        customer_email=user.email,
        title="Existing Support Thread",
        status="open",
        labels=["support"],
        user_id=user.id,
        organization_id=organization.id,
        raw_data={},
    )
    await save_fixture(thread)
    return thread
