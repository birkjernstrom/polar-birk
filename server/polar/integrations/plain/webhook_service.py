from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import func, select

from polar.kit.utils import utc_now
from polar.models import Customer, Organization, User, UserOrganization
from polar.models.plain_message import (
    PlainMessage,
    PlainMessageChannel,
    PlainMessageDirection,
    PlainMessageSenderType,
)
from polar.models.plain_thread import PlainThread, PlainThreadStatus
from polar.postgres import AsyncSession

from .repository import PlainMessageRepository, PlainThreadRepository
from .schemas import (
    PlainThreadStatus as PlainWebhookThreadStatus,
    PlainWebhookChat,
    PlainWebhookEmail,
    PlainWebhookEventPayload,
    PlainWebhookEventType,
    PlainWebhookRequest,
    PlainWebhookSlackMessage,
    PlainWebhookThread,
)

log = structlog.get_logger(__name__)


def _map_thread_status(status: PlainWebhookThreadStatus | None) -> PlainThreadStatus:
    if status is None:
        return PlainThreadStatus.open
    match status:
        case PlainWebhookThreadStatus.todo:
            return PlainThreadStatus.open
        case PlainWebhookThreadStatus.snoozed:
            return PlainThreadStatus.snoozed
        case PlainWebhookThreadStatus.in_progress:
            return PlainThreadStatus.in_progress
        case PlainWebhookThreadStatus.done:
            return PlainThreadStatus.done
        case _:
            return PlainThreadStatus.open


def _map_sender_type(sender_type: str | None) -> PlainMessageSenderType:
    if sender_type is None:
        return PlainMessageSenderType.system
    match sender_type.upper():
        case "CUSTOMER":
            return PlainMessageSenderType.customer
        case "USER":
            return PlainMessageSenderType.user
        case "MACHINE_USER":
            return PlainMessageSenderType.machine_user
        case _:
            return PlainMessageSenderType.system


class PlainWebhookService:
    async def handle_webhook(
        self, session: AsyncSession, webhook_request: PlainWebhookRequest
    ) -> None:
        log.info(
            "Processing Plain webhook",
            event_type=webhook_request.payload.event_type,
            webhook_id=webhook_request.id,
        )

        payload = webhook_request.payload
        event_type = payload.event_type

        try:
            match event_type:
                case PlainWebhookEventType.thread_created:
                    await self._handle_thread_created(session, payload)
                case (
                    PlainWebhookEventType.thread_status_transitioned
                    | PlainWebhookEventType.thread_assignment_transitioned
                    | PlainWebhookEventType.thread_labels_changed
                    | PlainWebhookEventType.thread_priority_changed
                ):
                    await self._handle_thread_updated(session, payload)
                case (
                    PlainWebhookEventType.email_sent
                    | PlainWebhookEventType.email_received
                ):
                    await self._handle_email_event(session, payload, event_type)
                case (
                    PlainWebhookEventType.chat_sent
                    | PlainWebhookEventType.chat_received
                ):
                    await self._handle_chat_event(session, payload, event_type)
                case (
                    PlainWebhookEventType.slack_message_sent
                    | PlainWebhookEventType.slack_message_received
                ):
                    await self._handle_slack_event(session, payload, event_type)
                case _:
                    log.debug("Ignoring unhandled event type", event_type=event_type)
        except Exception:
            log.exception(
                "Error processing Plain webhook",
                event_type=event_type,
                webhook_id=webhook_request.id,
            )
            raise

    async def _handle_thread_created(
        self, session: AsyncSession, payload: PlainWebhookEventPayload
    ) -> PlainThread | None:
        if payload.thread is None:
            log.warning("Thread created event without thread data")
            return None

        return await self._upsert_thread(session, payload.thread)

    async def _handle_thread_updated(
        self, session: AsyncSession, payload: PlainWebhookEventPayload
    ) -> PlainThread | None:
        if payload.thread is None:
            log.warning("Thread update event without thread data")
            return None

        return await self._upsert_thread(session, payload.thread)

    async def _handle_email_event(
        self,
        session: AsyncSession,
        payload: PlainWebhookEventPayload,
        event_type: PlainWebhookEventType,
    ) -> PlainMessage | None:
        if payload.email is None:
            log.warning("Email event without email data")
            return None

        thread = await self._upsert_thread(session, payload.email.thread)
        if thread is None:
            return None

        return await self._create_email_message(
            session, thread, payload.email, event_type
        )

    async def _handle_chat_event(
        self,
        session: AsyncSession,
        payload: PlainWebhookEventPayload,
        event_type: PlainWebhookEventType,
    ) -> PlainMessage | None:
        if payload.chat is None:
            log.warning("Chat event without chat data")
            return None

        thread = await self._upsert_thread(session, payload.chat.thread)
        if thread is None:
            return None

        return await self._create_chat_message(
            session, thread, payload.chat, event_type
        )

    async def _handle_slack_event(
        self,
        session: AsyncSession,
        payload: PlainWebhookEventPayload,
        event_type: PlainWebhookEventType,
    ) -> PlainMessage | None:
        if payload.slack_message is None:
            log.warning("Slack event without slack message data")
            return None

        thread = await self._upsert_thread(session, payload.slack_message.thread)
        if thread is None:
            return None

        return await self._create_slack_message(
            session, thread, payload.slack_message, event_type
        )

    async def _upsert_thread(
        self, session: AsyncSession, thread_data: PlainWebhookThread
    ) -> PlainThread | None:
        repository = PlainThreadRepository.from_session(session)

        existing_thread = await repository.get_by_plain_id(thread_data.id)

        customer_email = thread_data.customer.email or ""
        customer_external_id = thread_data.customer.external_id

        user_id, organization_id, customer_id = await self._resolve_polar_entities(
            session, customer_email, customer_external_id
        )

        labels = [label.name for label in thread_data.labels]
        assignee_id = thread_data.assignee.id if thread_data.assignee else None

        if existing_thread is not None:
            existing_thread.title = thread_data.title
            existing_thread.status = _map_thread_status(thread_data.status)
            existing_thread.labels = labels
            existing_thread.priority = thread_data.priority
            existing_thread.assignee_id = assignee_id
            existing_thread.plain_updated_at = thread_data.updated_at
            existing_thread.raw_data = thread_data.model_dump(mode="json")

            if user_id and not existing_thread.user_id:
                existing_thread.user_id = user_id
            if organization_id and not existing_thread.organization_id:
                existing_thread.organization_id = organization_id
            if customer_id and not existing_thread.customer_id:
                existing_thread.customer_id = customer_id

            session.add(existing_thread)
            return existing_thread

        new_thread = PlainThread(
            plain_id=thread_data.id,
            plain_customer_id=thread_data.customer.id,
            customer_email=customer_email,
            title=thread_data.title,
            status=_map_thread_status(thread_data.status),
            labels=labels,
            priority=thread_data.priority,
            assignee_id=assignee_id,
            plain_created_at=thread_data.created_at,
            plain_updated_at=thread_data.updated_at,
            raw_data=thread_data.model_dump(mode="json"),
            user_id=user_id,
            organization_id=organization_id,
            customer_id=customer_id,
        )

        await repository.create(new_thread)
        return new_thread

    async def _create_email_message(
        self,
        session: AsyncSession,
        thread: PlainThread,
        email_data: PlainWebhookEmail,
        event_type: PlainWebhookEventType,
    ) -> PlainMessage | None:
        repository = PlainMessageRepository.from_session(session)

        existing_message = await repository.get_by_plain_id(email_data.id)
        if existing_message is not None:
            return existing_message

        direction = (
            PlainMessageDirection.inbound
            if event_type == PlainWebhookEventType.email_received
            else PlainMessageDirection.outbound
        )

        sender_type = _map_sender_type(
            email_data.sender.type if email_data.sender else None
        )
        sender_email = email_data.sender.email if email_data.sender else None
        sender_name = email_data.sender.full_name if email_data.sender else None

        content = None
        if email_data.email_content:
            content = (
                email_data.email_content.text_content
                or email_data.email_content.markdown_content
            )

        sent_at = email_data.created_at or utc_now()

        message = PlainMessage(
            plain_id=email_data.id,
            thread_id=thread.id,
            direction=direction,
            channel=PlainMessageChannel.email,
            sender_type=sender_type,
            sender_email=sender_email,
            sender_name=sender_name,
            content=content,
            sent_at=sent_at,
            raw_data=email_data.model_dump(mode="json"),
        )

        await repository.create(message)
        await self._update_thread_message_timestamps(session, thread, sent_at)

        return message

    async def _create_chat_message(
        self,
        session: AsyncSession,
        thread: PlainThread,
        chat_data: PlainWebhookChat,
        event_type: PlainWebhookEventType,
    ) -> PlainMessage | None:
        repository = PlainMessageRepository.from_session(session)

        existing_message = await repository.get_by_plain_id(chat_data.id)
        if existing_message is not None:
            return existing_message

        direction = (
            PlainMessageDirection.inbound
            if event_type == PlainWebhookEventType.chat_received
            else PlainMessageDirection.outbound
        )

        sender_type = _map_sender_type(
            chat_data.sender.type if chat_data.sender else None
        )
        sender_email = chat_data.sender.email if chat_data.sender else None
        sender_name = chat_data.sender.full_name if chat_data.sender else None

        content = None
        if chat_data.chat_content:
            content = (
                chat_data.chat_content.text or chat_data.chat_content.markdown_text
            )

        sent_at = chat_data.created_at or utc_now()

        message = PlainMessage(
            plain_id=chat_data.id,
            thread_id=thread.id,
            direction=direction,
            channel=PlainMessageChannel.chat,
            sender_type=sender_type,
            sender_email=sender_email,
            sender_name=sender_name,
            content=content,
            sent_at=sent_at,
            raw_data=chat_data.model_dump(mode="json"),
        )

        await repository.create(message)
        await self._update_thread_message_timestamps(session, thread, sent_at)

        return message

    async def _create_slack_message(
        self,
        session: AsyncSession,
        thread: PlainThread,
        slack_data: PlainWebhookSlackMessage,
        event_type: PlainWebhookEventType,
    ) -> PlainMessage | None:
        repository = PlainMessageRepository.from_session(session)

        existing_message = await repository.get_by_plain_id(slack_data.id)
        if existing_message is not None:
            return existing_message

        direction = (
            PlainMessageDirection.inbound
            if event_type == PlainWebhookEventType.slack_message_received
            else PlainMessageDirection.outbound
        )

        sender_type = _map_sender_type(
            slack_data.sender.type if slack_data.sender else None
        )
        sender_email = slack_data.sender.email if slack_data.sender else None
        sender_name = slack_data.sender.full_name if slack_data.sender else None

        content = slack_data.slack_content.text if slack_data.slack_content else None

        sent_at = slack_data.created_at or utc_now()

        message = PlainMessage(
            plain_id=slack_data.id,
            thread_id=thread.id,
            direction=direction,
            channel=PlainMessageChannel.slack,
            sender_type=sender_type,
            sender_email=sender_email,
            sender_name=sender_name,
            content=content,
            sent_at=sent_at,
            raw_data=slack_data.model_dump(mode="json"),
        )

        await repository.create(message)
        await self._update_thread_message_timestamps(session, thread, sent_at)

        return message

    async def _update_thread_message_timestamps(
        self, session: AsyncSession, thread: PlainThread, message_time: datetime
    ) -> None:
        if thread.first_message_at is None or message_time < thread.first_message_at:
            thread.first_message_at = message_time
        if thread.last_message_at is None or message_time > thread.last_message_at:
            thread.last_message_at = message_time
        session.add(thread)

    async def _resolve_polar_entities(
        self,
        session: AsyncSession,
        customer_email: str,
        external_id: str | None,
    ) -> tuple[Any | None, Any | None, Any | None]:
        user_id = None
        organization_id = None
        customer_id = None

        if external_id:
            try:
                from uuid import UUID

                user_uuid = UUID(external_id)
                statement = select(User).where(User.id == user_uuid)
                result = await session.execute(statement)
                user = result.scalar_one_or_none()
                if user:
                    user_id = user.id

                    user_org_statement = (
                        select(UserOrganization)
                        .where(UserOrganization.user_id == user_id)
                        .limit(1)
                    )
                    user_org_result = await session.execute(user_org_statement)
                    user_org = user_org_result.scalar_one_or_none()
                    if user_org:
                        organization_id = user_org.organization_id
            except (ValueError, TypeError):
                pass

        if customer_email and not user_id:
            statement = select(User).where(func.lower(User.email) == customer_email.lower())
            result = await session.execute(statement)
            user = result.scalar_one_or_none()
            if user:
                user_id = user.id

        if customer_email:
            statement = select(Customer).where(
                func.lower(Customer.email) == customer_email.lower()
            )
            result = await session.execute(statement)
            customer = result.scalars().first()
            if customer:
                customer_id = customer.id
                if not organization_id:
                    organization_id = customer.organization_id

        return user_id, organization_id, customer_id


plain_webhook_service = PlainWebhookService()
