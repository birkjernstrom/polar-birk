import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from polar.integrations.plain.repository import (
    PlainMessageRepository,
    PlainThreadRepository,
)
from polar.integrations.plain.schemas import PlainThreadStatus, PlainWebhookEventType
from polar.integrations.plain.service import plain_webhook_service
from polar.models import Organization, PlainThread, User
from polar.models.plain import (
    PlainMessageChannel,
    PlainMessageDirection,
    PlainMessageSenderType,
    PlainThreadStatus as PlainThreadStatusModel,
)
from tests.fixtures.database import SaveFixture

from .conftest import (
    build_plain_chat,
    build_plain_customer,
    build_plain_email,
    build_plain_slack_message,
    build_plain_thread,
    build_plain_webhook_request,
)


@pytest.mark.asyncio
class TestPlainWebhookServiceThreadCreated:
    async def test_creates_new_thread(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
    ) -> None:
        customer = build_plain_customer(
            id="c_new_123",
            email="newcustomer@example.com",
        )
        thread = build_plain_thread(
            id="th_new_123",
            customer=customer,
            title="New Support Request",
            status=PlainThreadStatus.todo,
        )
        webhook = build_plain_webhook_request(
            event_type=PlainWebhookEventType.thread_created,
            thread=thread,
        )

        await plain_webhook_service.handle_webhook(session, webhook)

        repository = PlainThreadRepository.from_session(session)
        created_thread = await repository.get_by_plain_id("th_new_123")

        assert created_thread is not None
        assert created_thread.plain_id == "th_new_123"
        assert created_thread.customer_email == "newcustomer@example.com"
        assert created_thread.title == "New Support Request"
        assert created_thread.status == PlainThreadStatusModel.open

    async def test_links_to_existing_user_by_email(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        user: User,
    ) -> None:
        customer = build_plain_customer(
            id="c_user_123",
            email=user.email,
        )
        thread = build_plain_thread(
            id="th_user_123",
            customer=customer,
            title="User Support Request",
        )
        webhook = build_plain_webhook_request(
            event_type=PlainWebhookEventType.thread_created,
            thread=thread,
        )

        await plain_webhook_service.handle_webhook(session, webhook)

        repository = PlainThreadRepository.from_session(session)
        created_thread = await repository.get_by_plain_id("th_user_123")

        assert created_thread is not None
        assert created_thread.user_id == user.id

    async def test_links_to_existing_user_by_external_id(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        user: User,
    ) -> None:
        customer = build_plain_customer(
            id="c_extid_123",
            email="different@example.com",
            external_id=str(user.id),
        )
        thread = build_plain_thread(
            id="th_extid_123",
            customer=customer,
            title="External ID Support Request",
        )
        webhook = build_plain_webhook_request(
            event_type=PlainWebhookEventType.thread_created,
            thread=thread,
        )

        await plain_webhook_service.handle_webhook(session, webhook)

        repository = PlainThreadRepository.from_session(session)
        created_thread = await repository.get_by_plain_id("th_extid_123")

        assert created_thread is not None
        assert created_thread.user_id == user.id


@pytest.mark.asyncio
class TestPlainWebhookServiceThreadUpdated:
    async def test_updates_existing_thread_status(
        self,
        session: AsyncSession,
        plain_thread: PlainThread,
    ) -> None:
        thread = build_plain_thread(
            id=plain_thread.plain_id,
            customer=build_plain_customer(email=plain_thread.customer_email),
            title="Updated Title",
            status=PlainThreadStatus.done,
        )
        webhook = build_plain_webhook_request(
            event_type=PlainWebhookEventType.thread_status_transitioned,
            thread=thread,
        )

        await plain_webhook_service.handle_webhook(session, webhook)

        repository = PlainThreadRepository.from_session(session)
        updated_thread = await repository.get_by_plain_id(plain_thread.plain_id)

        assert updated_thread is not None
        assert updated_thread.title == "Updated Title"
        assert updated_thread.status == PlainThreadStatusModel.done

    async def test_creates_thread_if_not_exists(
        self,
        session: AsyncSession,
    ) -> None:
        thread = build_plain_thread(
            id="th_nonexistent_123",
            customer=build_plain_customer(email="new@example.com"),
            title="Thread from status update",
            status=PlainThreadStatus.in_progress,
        )
        webhook = build_plain_webhook_request(
            event_type=PlainWebhookEventType.thread_status_transitioned,
            thread=thread,
        )

        await plain_webhook_service.handle_webhook(session, webhook)

        repository = PlainThreadRepository.from_session(session)
        created_thread = await repository.get_by_plain_id("th_nonexistent_123")

        assert created_thread is not None
        assert created_thread.status == PlainThreadStatusModel.in_progress


@pytest.mark.asyncio
class TestPlainWebhookServiceEmailReceived:
    async def test_creates_inbound_email_message(
        self,
        session: AsyncSession,
    ) -> None:
        thread = build_plain_thread(id="th_email_123")
        email = build_plain_email(
            id="em_inbound_123",
            thread=thread,
            sender_type="CUSTOMER",
            sender_email="customer@example.com",
            text_content="Help me with my order!",
        )
        webhook = build_plain_webhook_request(
            event_type=PlainWebhookEventType.email_received,
            email=email,
        )

        await plain_webhook_service.handle_webhook(session, webhook)

        thread_repo = PlainThreadRepository.from_session(session)
        message_repo = PlainMessageRepository.from_session(session)

        created_thread = await thread_repo.get_by_plain_id("th_email_123")
        assert created_thread is not None

        messages = await message_repo.get_by_thread_id(created_thread.id)
        assert len(messages) == 1

        message = messages[0]
        assert message.plain_id == "em_inbound_123"
        assert message.direction == PlainMessageDirection.inbound
        assert message.channel == PlainMessageChannel.email
        assert message.sender_type == PlainMessageSenderType.customer
        assert message.content == "Help me with my order!"

    async def test_updates_thread_message_timestamps(
        self,
        session: AsyncSession,
    ) -> None:
        thread = build_plain_thread(id="th_timestamps_123")
        email = build_plain_email(
            id="em_timestamps_123",
            thread=thread,
        )
        webhook = build_plain_webhook_request(
            event_type=PlainWebhookEventType.email_received,
            email=email,
        )

        await plain_webhook_service.handle_webhook(session, webhook)

        thread_repo = PlainThreadRepository.from_session(session)
        created_thread = await thread_repo.get_by_plain_id("th_timestamps_123")

        assert created_thread is not None
        assert created_thread.first_message_at is not None
        assert created_thread.last_message_at is not None


@pytest.mark.asyncio
class TestPlainWebhookServiceEmailSent:
    async def test_creates_outbound_email_message(
        self,
        session: AsyncSession,
    ) -> None:
        thread = build_plain_thread(id="th_email_sent_123")
        email = build_plain_email(
            id="em_outbound_123",
            thread=thread,
            sender_type="USER",
            sender_email="agent@polar.sh",
            sender_name="Support Agent",
            text_content="Thanks for reaching out!",
        )
        webhook = build_plain_webhook_request(
            event_type=PlainWebhookEventType.email_sent,
            email=email,
        )

        await plain_webhook_service.handle_webhook(session, webhook)

        thread_repo = PlainThreadRepository.from_session(session)
        message_repo = PlainMessageRepository.from_session(session)

        created_thread = await thread_repo.get_by_plain_id("th_email_sent_123")
        assert created_thread is not None

        messages = await message_repo.get_by_thread_id(created_thread.id)
        assert len(messages) == 1

        message = messages[0]
        assert message.plain_id == "em_outbound_123"
        assert message.direction == PlainMessageDirection.outbound
        assert message.channel == PlainMessageChannel.email
        assert message.sender_type == PlainMessageSenderType.user


@pytest.mark.asyncio
class TestPlainWebhookServiceChatReceived:
    async def test_creates_inbound_chat_message(
        self,
        session: AsyncSession,
    ) -> None:
        thread = build_plain_thread(id="th_chat_123")
        chat = build_plain_chat(
            id="ch_inbound_123",
            thread=thread,
            sender_type="CUSTOMER",
            text="Hello, I need help!",
        )
        webhook = build_plain_webhook_request(
            event_type=PlainWebhookEventType.chat_received,
            chat=chat,
        )

        await plain_webhook_service.handle_webhook(session, webhook)

        thread_repo = PlainThreadRepository.from_session(session)
        message_repo = PlainMessageRepository.from_session(session)

        created_thread = await thread_repo.get_by_plain_id("th_chat_123")
        assert created_thread is not None

        messages = await message_repo.get_by_thread_id(created_thread.id)
        assert len(messages) == 1

        message = messages[0]
        assert message.plain_id == "ch_inbound_123"
        assert message.direction == PlainMessageDirection.inbound
        assert message.channel == PlainMessageChannel.chat
        assert message.content == "Hello, I need help!"


@pytest.mark.asyncio
class TestPlainWebhookServiceChatSent:
    async def test_creates_outbound_chat_message(
        self,
        session: AsyncSession,
    ) -> None:
        thread = build_plain_thread(id="th_chat_sent_123")
        chat = build_plain_chat(
            id="ch_outbound_123",
            thread=thread,
            sender_type="USER",
            sender_email="agent@polar.sh",
            text="How can I help you?",
        )
        webhook = build_plain_webhook_request(
            event_type=PlainWebhookEventType.chat_sent,
            chat=chat,
        )

        await plain_webhook_service.handle_webhook(session, webhook)

        thread_repo = PlainThreadRepository.from_session(session)
        message_repo = PlainMessageRepository.from_session(session)

        created_thread = await thread_repo.get_by_plain_id("th_chat_sent_123")
        assert created_thread is not None

        messages = await message_repo.get_by_thread_id(created_thread.id)
        assert len(messages) == 1

        message = messages[0]
        assert message.direction == PlainMessageDirection.outbound
        assert message.channel == PlainMessageChannel.chat


@pytest.mark.asyncio
class TestPlainWebhookServiceSlackMessages:
    async def test_creates_inbound_slack_message(
        self,
        session: AsyncSession,
    ) -> None:
        thread = build_plain_thread(id="th_slack_123")
        slack = build_plain_slack_message(
            id="sl_inbound_123",
            thread=thread,
            sender_type="CUSTOMER",
            text="Slack support request",
        )
        webhook = build_plain_webhook_request(
            event_type=PlainWebhookEventType.slack_message_received,
            slack_message=slack,
        )

        await plain_webhook_service.handle_webhook(session, webhook)

        thread_repo = PlainThreadRepository.from_session(session)
        message_repo = PlainMessageRepository.from_session(session)

        created_thread = await thread_repo.get_by_plain_id("th_slack_123")
        assert created_thread is not None

        messages = await message_repo.get_by_thread_id(created_thread.id)
        assert len(messages) == 1

        message = messages[0]
        assert message.direction == PlainMessageDirection.inbound
        assert message.channel == PlainMessageChannel.slack
        assert message.content == "Slack support request"

    async def test_creates_outbound_slack_message(
        self,
        session: AsyncSession,
    ) -> None:
        thread = build_plain_thread(id="th_slack_sent_123")
        slack = build_plain_slack_message(
            id="sl_outbound_123",
            thread=thread,
            sender_type="USER",
            text="Response via Slack",
        )
        webhook = build_plain_webhook_request(
            event_type=PlainWebhookEventType.slack_message_sent,
            slack_message=slack,
        )

        await plain_webhook_service.handle_webhook(session, webhook)

        thread_repo = PlainThreadRepository.from_session(session)
        message_repo = PlainMessageRepository.from_session(session)

        created_thread = await thread_repo.get_by_plain_id("th_slack_sent_123")
        assert created_thread is not None

        messages = await message_repo.get_by_thread_id(created_thread.id)
        assert len(messages) == 1

        message = messages[0]
        assert message.direction == PlainMessageDirection.outbound
        assert message.channel == PlainMessageChannel.slack


@pytest.mark.asyncio
class TestPlainWebhookServiceIdempotency:
    async def test_does_not_duplicate_messages(
        self,
        session: AsyncSession,
    ) -> None:
        thread = build_plain_thread(id="th_idempotent_123")
        email = build_plain_email(
            id="em_idempotent_123",
            thread=thread,
        )
        webhook = build_plain_webhook_request(
            event_type=PlainWebhookEventType.email_received,
            email=email,
        )

        # Process same webhook twice
        await plain_webhook_service.handle_webhook(session, webhook)
        await plain_webhook_service.handle_webhook(session, webhook)

        thread_repo = PlainThreadRepository.from_session(session)
        message_repo = PlainMessageRepository.from_session(session)

        created_thread = await thread_repo.get_by_plain_id("th_idempotent_123")
        assert created_thread is not None

        messages = await message_repo.get_by_thread_id(created_thread.id)
        # Should only have 1 message, not 2
        assert len(messages) == 1


@pytest.mark.asyncio
class TestPlainWebhookServiceIgnoredEvents:
    async def test_ignores_customer_events(
        self,
        session: AsyncSession,
    ) -> None:
        webhook = build_plain_webhook_request(
            event_type=PlainWebhookEventType.customer_created,
        )

        # Should not raise
        await plain_webhook_service.handle_webhook(session, webhook)

        # Verify no threads were created
        thread_repo = PlainThreadRepository.from_session(session)
        threads = await thread_repo.get_by_customer_email("customer@example.com")
        assert len(threads) == 0
