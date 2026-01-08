# pyright: reportCallIssue=false
import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Coroutine
from datetime import datetime
from typing import Any

import httpx
import pycountry
import pycountry.db
import structlog
from babel.numbers import format_currency
from plain_client import (
    ComponentContainerContentInput,
    ComponentContainerInput,
    ComponentCopyButtonInput,
    ComponentDividerInput,
    ComponentDividerSpacingSize,
    ComponentInput,
    ComponentLinkButtonInput,
    ComponentRowContentInput,
    ComponentRowInput,
    ComponentSpacerInput,
    ComponentSpacerSize,
    ComponentTextColor,
    ComponentTextInput,
    ComponentTextSize,
    CreateThreadInput,
    CustomerIdentifierInput,
    EmailAddressInput,
    Plain,
    ThreadsFilter,
    UpsertCustomerIdentifierInput,
    UpsertCustomerInput,
    UpsertCustomerOnCreateInput,
    UpsertCustomerOnUpdateInput,
    UpsertCustomerUpsertCustomer,
)
from sqlalchemy import func, select
from sqlalchemy.orm import contains_eager

from polar.config import settings
from polar.exceptions import PolarError
from polar.kit.utils import utc_now
from polar.models import (
    Customer,
    Order,
    Organization,
    PlainMessage,
    PlainMessageChannel,
    PlainMessageDirection,
    PlainMessageSenderType,
    PlainThread,
    PlainThreadStatus,
    Product,
    User,
    UserOrganization,
)
from polar.models.organization import OrganizationStatus
from polar.models.organization_review import OrganizationReview
from polar.postgres import AsyncSession
from polar.user.repository import UserRepository

from .repository import PlainMessageRepository, PlainThreadRepository
from .schemas import (
    CustomerCard,
    CustomerCardKey,
    CustomerCardsRequest,
    CustomerCardsResponse,
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


class PlainServiceError(PolarError): ...


class AccountAdminDoesNotExistError(PlainServiceError):
    def __init__(self, account_id: uuid.UUID) -> None:
        self.account_id = account_id
        super().__init__(f"Account admin does not exist for account ID {account_id}")


class AccountReviewThreadCreationError(PlainServiceError):
    def __init__(self, account_id: uuid.UUID, message: str) -> None:
        self.account_id = account_id
        self.message = message
        super().__init__(
            f"Error creating thread for account ID {account_id}: {message}"
        )


class NoUserFoundError(PlainServiceError):
    def __init__(self, organization_id: uuid.UUID) -> None:
        self.organization_id = organization_id
        super().__init__(f"No user found for organization {organization_id}")


_card_getter_semaphore = asyncio.Semaphore(3)


async def _card_getter_task(
    coroutine: Coroutine[Any, Any, CustomerCard | None],
) -> CustomerCard | None:
    async with _card_getter_semaphore:
        return await coroutine


class PlainService:
    enabled = settings.PLAIN_TOKEN is not None

    async def get_cards(
        self, session: AsyncSession, request: CustomerCardsRequest
    ) -> CustomerCardsResponse:
        tasks: list[asyncio.Task[CustomerCard | None]] = []
        async with asyncio.TaskGroup() as tg:
            if CustomerCardKey.user in request.cardKeys:
                tasks.append(
                    tg.create_task(
                        _card_getter_task(self._get_user_card(session, request))
                    )
                )
            if CustomerCardKey.organization in request.cardKeys:
                tasks.append(
                    tg.create_task(
                        _card_getter_task(self._get_organization_card(session, request))
                    )
                )
            if CustomerCardKey.customer in request.cardKeys:
                tasks.append(
                    tg.create_task(
                        _card_getter_task(self.get_customer_card(session, request))
                    )
                )
            if CustomerCardKey.order in request.cardKeys:
                tasks.append(
                    tg.create_task(
                        _card_getter_task(self._get_order_card(session, request))
                    )
                )
            if CustomerCardKey.snippets in request.cardKeys:
                tasks.append(
                    tg.create_task(
                        _card_getter_task(self._get_snippets_card(session, request))
                    )
                )

        cards = [card for task in tasks if (card := task.result()) is not None]
        return CustomerCardsResponse(cards=cards)

    async def create_organization_review_thread(
        self, session: AsyncSession, organization: Organization
    ) -> None:
        if not self.enabled:
            return

        user_repository = UserRepository.from_session(session)
        if organization.account is None:
            from polar.organization.tasks import OrganizationAccountNotSet

            raise OrganizationAccountNotSet(organization.id)
        admin = await user_repository.get_by_id(organization.account.admin_id)
        if admin is None:
            raise AccountAdminDoesNotExistError(organization.account.admin_id)

        async with self._get_plain_client() as plain:
            customer_result = await plain.upsert_customer(
                UpsertCustomerInput(
                    identifier=UpsertCustomerIdentifierInput(email_address=admin.email),
                    on_create=UpsertCustomerOnCreateInput(
                        external_id=str(admin.id),
                        full_name=admin.email,
                        email=EmailAddressInput(
                            email=admin.email, is_verified=admin.email_verified
                        ),
                    ),
                    on_update=UpsertCustomerOnUpdateInput(
                        email=EmailAddressInput(
                            email=admin.email, is_verified=admin.email_verified
                        ),
                    ),
                )
            )
            if customer_result.error is not None:
                raise AccountReviewThreadCreationError(
                    organization.account.id, customer_result.error.message
                )

            match organization.status:
                case OrganizationStatus.INITIAL_REVIEW:
                    title = "Initial Account Review"
                case OrganizationStatus.ONGOING_REVIEW:
                    title = "Ongoing Account Review"
                case _:
                    raise ValueError("Organization is not under review")

            thread_result = await plain.create_thread(
                CreateThreadInput(
                    customer_identifier=self._get_customer_identifier(
                        customer_result, admin.email
                    ),
                    title=title,
                    label_type_ids=["lt_01JFG7F4N67FN3MAWK06FJ8FPG"],
                    components=[
                        ComponentInput(
                            component_text=ComponentTextInput(
                                text=f"The organization `{organization.slug}` should be reviewed, as it hit a threshold."
                            )
                        ),
                        ComponentInput(
                            component_spacer=ComponentSpacerInput(
                                spacer_size=ComponentSpacerSize.M
                            )
                        ),
                        ComponentInput(
                            component_link_button=ComponentLinkButtonInput(
                                link_button_url=settings.generate_backoffice_url(
                                    f"/organizations/{organization.id}"
                                ),
                                link_button_label="Review organization ↗",
                            )
                        ),
                    ],
                )
            )
            if thread_result.error is not None:
                raise AccountReviewThreadCreationError(
                    organization.account.id, thread_result.error.message
                )

    async def create_appeal_review_thread(
        self,
        session: AsyncSession,
        organization: Organization,
        review: OrganizationReview,
    ) -> None:
        """Create Plain ticket for organization appeal review."""
        if not self.enabled:
            return

        user_repository = UserRepository.from_session(session)
        users = await user_repository.get_all_by_organization(organization.id)
        if len(users) == 0:
            raise NoUserFoundError(organization.id)
        user = users[0]

        # Create Plain ticket for appeal review
        async with self._get_plain_client() as plain:
            customer_result = await plain.upsert_customer(
                UpsertCustomerInput(
                    identifier=UpsertCustomerIdentifierInput(email_address=user.email),
                    on_create=UpsertCustomerOnCreateInput(
                        external_id=str(user.id),
                        full_name=user.email,
                        email=EmailAddressInput(
                            email=user.email, is_verified=user.email_verified
                        ),
                    ),
                    on_update=UpsertCustomerOnUpdateInput(
                        email=EmailAddressInput(
                            email=user.email, is_verified=user.email_verified
                        ),
                    ),
                )
            )

            if customer_result.error is not None:
                raise AccountReviewThreadCreationError(
                    user.id, customer_result.error.message
                )

            # Create the thread with detailed appeal information
            thread_result = await plain.create_thread(
                CreateThreadInput(
                    customer_identifier=self._get_customer_identifier(
                        customer_result, user.email
                    ),
                    title=f"Organization Appeal - {organization.slug}",
                    label_type_ids=["lt_01K3QWYTDV7RSS7MM2RC584X41"],
                    components=[
                        ComponentInput(
                            component_text=ComponentTextInput(
                                text=f"The organization `{organization.slug}` has submitted an appeal for review after AI validation {review.verdict}."
                            )
                        ),
                        ComponentInput(
                            component_container=ComponentContainerInput(
                                container_content=[
                                    ComponentContainerContentInput(
                                        component_text=ComponentTextInput(
                                            text=organization.name or organization.slug
                                        )
                                    ),
                                    ComponentContainerContentInput(
                                        component_divider=ComponentDividerInput(
                                            divider_spacing_size=ComponentDividerSpacingSize.M
                                        )
                                    ),
                                    # Organization ID
                                    ComponentContainerContentInput(
                                        component_row=ComponentRowInput(
                                            row_main_content=[
                                                ComponentRowContentInput(
                                                    component_text=ComponentTextInput(
                                                        text="Organization ID",
                                                        text_size=ComponentTextSize.S,
                                                        text_color=ComponentTextColor.MUTED,
                                                    )
                                                ),
                                                ComponentRowContentInput(
                                                    component_text=ComponentTextInput(
                                                        text=str(organization.id)
                                                    )
                                                ),
                                            ],
                                            row_aside_content=[
                                                ComponentRowContentInput(
                                                    component_copy_button=ComponentCopyButtonInput(
                                                        copy_button_value=str(
                                                            organization.id
                                                        ),
                                                        copy_button_tooltip_label="Copy Organization ID",
                                                    )
                                                )
                                            ],
                                        )
                                    ),
                                    # Backoffice Link
                                    ComponentContainerContentInput(
                                        component_link_button=ComponentLinkButtonInput(
                                            link_button_url=settings.generate_backoffice_url(
                                                f"/organizations/{organization.id}"
                                            ),
                                            link_button_label="View in Backoffice",
                                        )
                                    ),
                                ]
                            )
                        ),
                    ],
                )
            )

            if thread_result.error is not None:
                raise AccountReviewThreadCreationError(
                    user.id, thread_result.error.message
                )

    async def create_organization_deletion_thread(
        self,
        session: AsyncSession,
        organization: Organization,
        requesting_user: User,
        blocked_reasons: list[str],
    ) -> None:
        """Create Plain ticket for organization deletion request."""
        if not self.enabled:
            return

        async with self._get_plain_client() as plain:
            customer_result = await plain.upsert_customer(
                UpsertCustomerInput(
                    identifier=UpsertCustomerIdentifierInput(
                        email_address=requesting_user.email
                    ),
                    on_create=UpsertCustomerOnCreateInput(
                        external_id=str(requesting_user.id),
                        full_name=requesting_user.email,
                        email=EmailAddressInput(
                            email=requesting_user.email,
                            is_verified=requesting_user.email_verified,
                        ),
                    ),
                    on_update=UpsertCustomerOnUpdateInput(
                        email=EmailAddressInput(
                            email=requesting_user.email,
                            is_verified=requesting_user.email_verified,
                        ),
                    ),
                )
            )
            if customer_result.error is not None:
                raise AccountReviewThreadCreationError(
                    organization.id, customer_result.error.message
                )

            reasons_text = ", ".join(blocked_reasons) if blocked_reasons else "unknown"

            thread_result = await plain.create_thread(
                CreateThreadInput(
                    customer_identifier=self._get_customer_identifier(
                        customer_result, requesting_user.email
                    ),
                    title=f"Organization Deletion Request - {organization.slug}",
                    label_type_ids=["lt_01JKD9ASBPVX09YYXGHSXZRWSA"],
                    components=[
                        ComponentInput(
                            component_text=ComponentTextInput(
                                text=f"User has requested deletion of organization `{organization.slug}`."
                            )
                        ),
                        ComponentInput(
                            component_text=ComponentTextInput(
                                text=f"Blocked reasons: {reasons_text}",
                                text_color=ComponentTextColor.MUTED,
                            )
                        ),
                        ComponentInput(
                            component_spacer=ComponentSpacerInput(
                                spacer_size=ComponentSpacerSize.M
                            )
                        ),
                        ComponentInput(
                            component_container=ComponentContainerInput(
                                container_content=[
                                    ComponentContainerContentInput(
                                        component_text=ComponentTextInput(
                                            text=organization.name or organization.slug
                                        )
                                    ),
                                    ComponentContainerContentInput(
                                        component_divider=ComponentDividerInput(
                                            divider_spacing_size=ComponentDividerSpacingSize.M
                                        )
                                    ),
                                    ComponentContainerContentInput(
                                        component_row=ComponentRowInput(
                                            row_main_content=[
                                                ComponentRowContentInput(
                                                    component_text=ComponentTextInput(
                                                        text="Organization ID",
                                                        text_size=ComponentTextSize.S,
                                                        text_color=ComponentTextColor.MUTED,
                                                    )
                                                ),
                                                ComponentRowContentInput(
                                                    component_text=ComponentTextInput(
                                                        text=str(organization.id)
                                                    )
                                                ),
                                            ],
                                            row_aside_content=[
                                                ComponentRowContentInput(
                                                    component_copy_button=ComponentCopyButtonInput(
                                                        copy_button_value=str(
                                                            organization.id
                                                        ),
                                                        copy_button_tooltip_label="Copy Organization ID",
                                                    )
                                                )
                                            ],
                                        )
                                    ),
                                    ComponentContainerContentInput(
                                        component_link_button=ComponentLinkButtonInput(
                                            link_button_url=settings.generate_backoffice_url(
                                                f"/organizations/{organization.id}"
                                            ),
                                            link_button_label="View in Backoffice",
                                        )
                                    ),
                                ]
                            )
                        ),
                    ],
                )
            )
            if thread_result.error is not None:
                raise AccountReviewThreadCreationError(
                    organization.id, thread_result.error.message
                )

    async def _get_user_card(
        self, session: AsyncSession, request: CustomerCardsRequest
    ) -> CustomerCard | None:
        email = request.customer.email

        user_repository = UserRepository.from_session(session)
        user = await user_repository.get_by_email(email)

        if user is None:
            return None

        components: list[ComponentInput] = [
            ComponentInput(
                component_container=ComponentContainerInput(
                    container_content=[
                        ComponentContainerContentInput(
                            component_row=ComponentRowInput(
                                row_main_content=[
                                    ComponentRowContentInput(
                                        component_text=ComponentTextInput(
                                            text=user.email
                                        )
                                    ),
                                ],
                                row_aside_content=[
                                    ComponentRowContentInput(
                                        component_link_button=ComponentLinkButtonInput(
                                            link_button_label="Backoffice ↗",
                                            link_button_url=settings.generate_backoffice_url(
                                                f"/users/{user.id}"
                                            ),
                                        )
                                    )
                                ],
                            )
                        ),
                        ComponentContainerContentInput(
                            component_divider=ComponentDividerInput(
                                divider_spacing_size=ComponentDividerSpacingSize.M
                            )
                        ),
                        ComponentContainerContentInput(
                            component_row=ComponentRowInput(
                                row_main_content=[
                                    ComponentRowContentInput(
                                        component_text=ComponentTextInput(
                                            text="ID",
                                            text_size=ComponentTextSize.S,
                                            text_color=ComponentTextColor.MUTED,
                                        )
                                    ),
                                    ComponentRowContentInput(
                                        component_text=ComponentTextInput(
                                            text=str(user.id)
                                        )
                                    ),
                                ],
                                row_aside_content=[
                                    ComponentRowContentInput(
                                        component_copy_button=ComponentCopyButtonInput(
                                            copy_button_value=str(user.id),
                                            copy_button_tooltip_label="Copy User ID",
                                        )
                                    )
                                ],
                            )
                        ),
                        ComponentContainerContentInput(
                            component_spacer=ComponentSpacerInput(
                                spacer_size=ComponentSpacerSize.M
                            )
                        ),
                        ComponentContainerContentInput(
                            component_text=ComponentTextInput(
                                text="Created At",
                                text_size=ComponentTextSize.S,
                                text_color=ComponentTextColor.MUTED,
                            )
                        ),
                        ComponentContainerContentInput(
                            component_text=ComponentTextInput(
                                text=user.created_at.date().isoformat()
                            )
                        ),
                        ComponentContainerContentInput(
                            component_row=ComponentRowInput(
                                row_main_content=[
                                    ComponentRowContentInput(
                                        component_text=ComponentTextInput(
                                            text="Identity Verification",
                                            text_size=ComponentTextSize.S,
                                            text_color=ComponentTextColor.MUTED,
                                        )
                                    ),
                                    ComponentRowContentInput(
                                        component_text=ComponentTextInput(
                                            text=user.identity_verification_status
                                        )
                                    ),
                                ],
                                row_aside_content=[
                                    ComponentRowContentInput(
                                        component_link_button=ComponentLinkButtonInput(
                                            link_button_label="Stripe ↗",
                                            link_button_url=f"https://dashboard.stripe.com/identity/verification-sessions/{user.identity_verification_id}",
                                        )
                                    )
                                ]
                                if user.identity_verification_id
                                else [],
                            )
                        ),
                    ]
                )
            )
        ]

        return CustomerCard(
            key=CustomerCardKey.user,
            timeToLiveSeconds=86400,
            components=[
                component.model_dump(by_alias=True, exclude_none=True)
                for component in components
            ],
        )

    async def _get_organization_card(
        self, session: AsyncSession, request: CustomerCardsRequest
    ) -> CustomerCard | None:
        email = request.customer.email

        statement = (
            select(Organization)
            .join(
                UserOrganization,
                Organization.id == UserOrganization.organization_id,
                isouter=True,
            )
            .join(User, User.id == UserOrganization.user_id)
            .where(func.lower(User.email) == email.lower())
        )
        result = await session.execute(statement)
        organizations = result.unique().scalars().all()

        if len(organizations) == 0:
            return None

        components: list[ComponentInput] = []
        for i, organization in enumerate(organizations):
            components.append(
                ComponentInput(
                    component_container=self._get_organization_component_container(
                        organization
                    )
                )
            )
            if i < len(organizations) - 1:
                components.append(
                    ComponentInput(
                        component_divider=ComponentDividerInput(
                            divider_spacing_size=ComponentDividerSpacingSize.M
                        )
                    )
                )

        return CustomerCard(
            key=CustomerCardKey.organization,
            timeToLiveSeconds=86400,
            components=[
                component.model_dump(by_alias=True, exclude_none=True)
                for component in components
            ],
        )

    def _get_organization_component_container(
        self, organization: Organization
    ) -> ComponentContainerInput:
        return ComponentContainerInput(
            container_content=[
                ComponentContainerContentInput(
                    component_row=ComponentRowInput(
                        row_main_content=[
                            ComponentRowContentInput(
                                component_text=ComponentTextInput(
                                    text=organization.name
                                )
                            ),
                            ComponentRowContentInput(
                                component_text=ComponentTextInput(
                                    text=organization.slug,
                                    text_color=ComponentTextColor.MUTED,
                                )
                            ),
                        ],
                        row_aside_content=[
                            ComponentRowContentInput(
                                component_link_button=ComponentLinkButtonInput(
                                    link_button_label="Backoffice ↗",
                                    link_button_url=settings.generate_backoffice_url(
                                        f"/organizations/{organization.id}"
                                    ),
                                )
                            )
                        ],
                    )
                ),
                ComponentContainerContentInput(
                    component_divider=ComponentDividerInput(
                        divider_spacing_size=ComponentDividerSpacingSize.M
                    )
                ),
                ComponentContainerContentInput(
                    component_row=ComponentRowInput(
                        row_main_content=[
                            ComponentRowContentInput(
                                component_text=ComponentTextInput(
                                    text="ID",
                                    text_size=ComponentTextSize.S,
                                    text_color=ComponentTextColor.MUTED,
                                )
                            ),
                            ComponentRowContentInput(
                                component_text=ComponentTextInput(
                                    text=str(organization.id)
                                )
                            ),
                        ],
                        row_aside_content=[
                            ComponentRowContentInput(
                                component_copy_button=ComponentCopyButtonInput(
                                    copy_button_value=str(organization.id),
                                    copy_button_tooltip_label="Copy Organization ID",
                                )
                            )
                        ],
                    )
                ),
                ComponentContainerContentInput(
                    component_row=ComponentRowInput(
                        row_main_content=[
                            ComponentRowContentInput(
                                component_text=ComponentTextInput(
                                    text="Customer Portal",
                                    text_size=ComponentTextSize.S,
                                    text_color=ComponentTextColor.MUTED,
                                )
                            ),
                            ComponentRowContentInput(
                                component_text=ComponentTextInput(
                                    text=settings.generate_frontend_url(
                                        f"/{organization.slug}/portal"
                                    )
                                )
                            ),
                        ],
                        row_aside_content=[
                            ComponentRowContentInput(
                                component_copy_button=ComponentCopyButtonInput(
                                    copy_button_value=settings.generate_frontend_url(
                                        f"/{organization.slug}/portal"
                                    ),
                                    copy_button_tooltip_label="Copy URL",
                                )
                            )
                        ],
                    )
                ),
                *(
                    [
                        ComponentContainerContentInput(
                            component_row=ComponentRowInput(
                                row_main_content=[
                                    ComponentRowContentInput(
                                        component_text=ComponentTextInput(
                                            text="Support Email",
                                            text_size=ComponentTextSize.S,
                                            text_color=ComponentTextColor.MUTED,
                                        )
                                    ),
                                    ComponentRowContentInput(
                                        component_text=ComponentTextInput(
                                            text=organization.email
                                        )
                                    ),
                                ],
                                row_aside_content=[
                                    ComponentRowContentInput(
                                        component_copy_button=ComponentCopyButtonInput(
                                            copy_button_value=organization.email,
                                            copy_button_tooltip_label="Copy Support Email",
                                        )
                                    )
                                ],
                            )
                        ),
                    ]
                    if organization.email
                    else []
                ),
                ComponentContainerContentInput(
                    component_spacer=ComponentSpacerInput(
                        spacer_size=ComponentSpacerSize.M
                    )
                ),
                ComponentContainerContentInput(
                    component_text=ComponentTextInput(
                        text="Created At",
                        text_size=ComponentTextSize.S,
                        text_color=ComponentTextColor.MUTED,
                    )
                ),
                ComponentContainerContentInput(
                    component_text=ComponentTextInput(
                        text=organization.created_at.date().isoformat()
                    )
                ),
            ]
        )

    async def get_customer_card(
        self, session: AsyncSession, request: CustomerCardsRequest
    ) -> CustomerCard | None:
        email = request.customer.email

        # No need to filter out soft deleted. We want to see them in support.
        statement = select(Customer).where(func.lower(Customer.email) == email.lower())
        result = await session.execute(statement)
        customers = result.unique().scalars().all()

        if len(customers) == 0:
            return None

        def _get_customer_container(customer: Customer) -> ComponentContainerInput:
            country: pycountry.db.Country | None = None
            if customer.billing_address and customer.billing_address.country:
                country = pycountry.countries.get(
                    alpha_2=customer.billing_address.country
                )
            return ComponentContainerInput(
                container_content=[
                    ComponentContainerContentInput(
                        component_text=ComponentTextInput(
                            text=customer.name or customer.email or "Unknown"
                        )
                    ),
                    ComponentContainerContentInput(
                        component_divider=ComponentDividerInput(
                            divider_spacing_size=ComponentDividerSpacingSize.M
                        )
                    ),
                    ComponentContainerContentInput(
                        component_row=ComponentRowInput(
                            row_main_content=[
                                ComponentRowContentInput(
                                    component_text=ComponentTextInput(
                                        text="ID",
                                        text_size=ComponentTextSize.S,
                                        text_color=ComponentTextColor.MUTED,
                                    )
                                ),
                                ComponentRowContentInput(
                                    component_text=ComponentTextInput(
                                        text=str(customer.id)
                                    )
                                ),
                            ],
                            row_aside_content=[
                                ComponentRowContentInput(
                                    component_copy_button=ComponentCopyButtonInput(
                                        copy_button_value=str(customer.id),
                                        copy_button_tooltip_label="Copy Customer ID",
                                    )
                                )
                            ],
                        )
                    ),
                    ComponentContainerContentInput(
                        component_spacer=ComponentSpacerInput(
                            spacer_size=ComponentSpacerSize.M
                        )
                    ),
                    ComponentContainerContentInput(
                        component_text=ComponentTextInput(
                            text="Created At",
                            text_size=ComponentTextSize.S,
                            text_color=ComponentTextColor.MUTED,
                        )
                    ),
                    ComponentContainerContentInput(
                        component_text=ComponentTextInput(
                            text=customer.created_at.date().isoformat()
                        )
                    ),
                    *(
                        [
                            ComponentContainerContentInput(
                                component_spacer=ComponentSpacerInput(
                                    spacer_size=ComponentSpacerSize.M
                                )
                            ),
                            ComponentContainerContentInput(
                                component_row=ComponentRowInput(
                                    row_main_content=[
                                        ComponentRowContentInput(
                                            component_text=ComponentTextInput(
                                                text="Country",
                                                text_size=ComponentTextSize.S,
                                                text_color=ComponentTextColor.MUTED,
                                            )
                                        ),
                                        ComponentRowContentInput(
                                            component_text=ComponentTextInput(
                                                text=country.name,
                                            )
                                        ),
                                    ],
                                    row_aside_content=[
                                        ComponentRowContentInput(
                                            component_text=ComponentTextInput(
                                                text=country.flag
                                            )
                                        )
                                    ],
                                )
                            ),
                        ]
                        if country
                        else []
                    ),
                    ComponentContainerContentInput(
                        component_spacer=ComponentSpacerInput(
                            spacer_size=ComponentSpacerSize.M
                        )
                    ),
                    ComponentContainerContentInput(
                        component_row=ComponentRowInput(
                            row_main_content=[
                                ComponentRowContentInput(
                                    component_text=ComponentTextInput(
                                        text="Stripe Customer ID",
                                        text_size=ComponentTextSize.S,
                                        text_color=ComponentTextColor.MUTED,
                                    )
                                ),
                                ComponentRowContentInput(
                                    component_text=ComponentTextInput(
                                        text=customer.stripe_customer_id or "N/A",
                                    )
                                ),
                            ],
                            row_aside_content=[
                                ComponentRowContentInput(
                                    component_link_button=ComponentLinkButtonInput(
                                        link_button_label="Stripe ↗",
                                        link_button_url=f"https://dashboard.stripe.com/customers/{customer.stripe_customer_id}",
                                    )
                                )
                            ],
                        )
                    ),
                ]
            )

        components: list[ComponentInput] = []
        for i, customer in enumerate(customers):
            components.append(
                ComponentInput(component_container=_get_customer_container(customer))
            )
            if i < len(customers) - 1:
                components.append(
                    ComponentInput(
                        component_divider=ComponentDividerInput(
                            divider_spacing_size=ComponentDividerSpacingSize.M
                        )
                    )
                )

        return CustomerCard(
            key=CustomerCardKey.customer,
            timeToLiveSeconds=86400,
            components=[
                component.model_dump(by_alias=True, exclude_none=True)
                for component in components
            ],
        )

    async def _get_order_card(
        self, session: AsyncSession, request: CustomerCardsRequest
    ) -> CustomerCard | None:
        email = request.customer.email

        statement = (
            (
                select(Order)
                .join(Customer, onclause=Customer.id == Order.customer_id)
                .join(Product, onclause=Product.id == Order.product_id, isouter=True)
                .where(func.lower(Customer.email) == email.lower())
            )
            .order_by(Order.created_at.desc())
            .limit(3)
            .options(
                contains_eager(Order.product),
                contains_eager(Order.customer).joinedload(Customer.organization),
            )
        )
        result = await session.execute(statement)
        orders = result.unique().scalars().all()

        if len(orders) == 0:
            return None

        def _get_order_container(order: Order) -> ComponentContainerInput:
            organization = order.customer.organization

            return ComponentContainerInput(
                container_content=[
                    ComponentContainerContentInput(
                        component_row=ComponentRowInput(
                            row_main_content=[
                                ComponentRowContentInput(
                                    component_text=ComponentTextInput(
                                        text=order.description
                                    )
                                ),
                            ],
                            row_aside_content=[
                                ComponentRowContentInput(
                                    component_link_button=ComponentLinkButtonInput(
                                        link_button_label="Backoffice ↗",
                                        link_button_url=settings.generate_backoffice_url(
                                            f"/orders/{order.id}"
                                        ),
                                    )
                                )
                            ],
                        )
                    ),
                    ComponentContainerContentInput(
                        component_divider=ComponentDividerInput(
                            divider_spacing_size=ComponentDividerSpacingSize.M
                        )
                    ),
                    ComponentContainerContentInput(
                        component_row=ComponentRowInput(
                            row_main_content=[
                                ComponentRowContentInput(
                                    component_text=ComponentTextInput(
                                        text="Organization",
                                        text_size=ComponentTextSize.S,
                                        text_color=ComponentTextColor.MUTED,
                                    )
                                ),
                                ComponentRowContentInput(
                                    component_text=ComponentTextInput(
                                        text=organization.name
                                    )
                                ),
                            ],
                            row_aside_content=[
                                ComponentRowContentInput(
                                    component_link_button=ComponentLinkButtonInput(
                                        link_button_label="Backoffice ↗",
                                        link_button_url=settings.generate_backoffice_url(
                                            f"/organizations/{organization.id}"
                                        ),
                                    )
                                )
                            ],
                        )
                    ),
                    ComponentContainerContentInput(
                        component_spacer=ComponentSpacerInput(
                            spacer_size=ComponentSpacerSize.M
                        )
                    ),
                    ComponentContainerContentInput(
                        component_row=ComponentRowInput(
                            row_main_content=[
                                ComponentRowContentInput(
                                    component_text=ComponentTextInput(
                                        text="Customer Portal",
                                        text_size=ComponentTextSize.S,
                                        text_color=ComponentTextColor.MUTED,
                                    )
                                ),
                                ComponentRowContentInput(
                                    component_text=ComponentTextInput(
                                        text=settings.generate_frontend_url(
                                            f"/{organization.slug}/portal"
                                        )
                                    )
                                ),
                            ],
                            row_aside_content=[
                                ComponentRowContentInput(
                                    component_copy_button=ComponentCopyButtonInput(
                                        copy_button_value=settings.generate_frontend_url(
                                            f"/{organization.slug}/portal"
                                        ),
                                        copy_button_tooltip_label="Copy URL",
                                    )
                                )
                            ],
                        )
                    ),
                    *(
                        [
                            ComponentContainerContentInput(
                                component_row=ComponentRowInput(
                                    row_main_content=[
                                        ComponentRowContentInput(
                                            component_text=ComponentTextInput(
                                                text="Support Email",
                                                text_size=ComponentTextSize.S,
                                                text_color=ComponentTextColor.MUTED,
                                            )
                                        ),
                                        ComponentRowContentInput(
                                            component_text=ComponentTextInput(
                                                text=organization.email
                                            )
                                        ),
                                    ],
                                    row_aside_content=[
                                        ComponentRowContentInput(
                                            component_copy_button=ComponentCopyButtonInput(
                                                copy_button_value=organization.email,
                                                copy_button_tooltip_label="Copy Support Email",
                                            )
                                        )
                                    ],
                                )
                            ),
                        ]
                        if organization.email
                        else []
                    ),
                    ComponentContainerContentInput(
                        component_spacer=ComponentSpacerInput(
                            spacer_size=ComponentSpacerSize.M
                        )
                    ),
                    ComponentContainerContentInput(
                        component_row=ComponentRowInput(
                            row_main_content=[
                                ComponentRowContentInput(
                                    component_text=ComponentTextInput(
                                        text="ID",
                                        text_size=ComponentTextSize.S,
                                        text_color=ComponentTextColor.MUTED,
                                    )
                                ),
                                ComponentRowContentInput(
                                    component_text=ComponentTextInput(
                                        text=str(order.id)
                                    )
                                ),
                            ],
                            row_aside_content=[
                                ComponentRowContentInput(
                                    component_copy_button=ComponentCopyButtonInput(
                                        copy_button_value=str(order.id),
                                        copy_button_tooltip_label="Copy Order ID",
                                    )
                                )
                            ],
                        )
                    ),
                    ComponentContainerContentInput(
                        component_spacer=ComponentSpacerInput(
                            spacer_size=ComponentSpacerSize.M
                        )
                    ),
                    ComponentContainerContentInput(
                        component_text=ComponentTextInput(
                            text="Date",
                            text_size=ComponentTextSize.S,
                            text_color=ComponentTextColor.MUTED,
                        )
                    ),
                    ComponentContainerContentInput(
                        component_text=ComponentTextInput(
                            text=order.created_at.date().isoformat()
                        )
                    ),
                    ComponentContainerContentInput(
                        component_spacer=ComponentSpacerInput(
                            spacer_size=ComponentSpacerSize.M
                        )
                    ),
                    ComponentContainerContentInput(
                        component_text=ComponentTextInput(
                            text="Billing Reason",
                            text_size=ComponentTextSize.S,
                            text_color=ComponentTextColor.MUTED,
                        )
                    ),
                    ComponentContainerContentInput(
                        component_text=ComponentTextInput(text=order.billing_reason)
                    ),
                    ComponentContainerContentInput(
                        component_divider=ComponentDividerInput(
                            divider_spacing_size=ComponentDividerSpacingSize.M
                        )
                    ),
                    ComponentContainerContentInput(
                        component_spacer=ComponentSpacerInput(
                            spacer_size=ComponentSpacerSize.M
                        )
                    ),
                    ComponentContainerContentInput(
                        component_text=ComponentTextInput(
                            text="Amount",
                            text_size=ComponentTextSize.S,
                            text_color=ComponentTextColor.MUTED,
                        )
                    ),
                    ComponentContainerContentInput(
                        component_text=ComponentTextInput(
                            text=format_currency(
                                order.net_amount / 100,
                                order.currency.upper(),
                                locale="en_US",
                            )
                        )
                    ),
                    ComponentContainerContentInput(
                        component_text=ComponentTextInput(
                            text="Tax Amount",
                            text_size=ComponentTextSize.S,
                            text_color=ComponentTextColor.MUTED,
                        )
                    ),
                    ComponentContainerContentInput(
                        component_text=ComponentTextInput(
                            text=format_currency(
                                order.tax_amount / 100,
                                order.currency.upper(),
                                locale="en_US",
                            )
                        )
                    ),
                ]
            )

        components: list[ComponentInput] = []
        for i, order in enumerate(orders):
            components.append(
                ComponentInput(component_container=_get_order_container(order))
            )
            if i < len(orders) - 1:
                components.append(
                    ComponentInput(
                        component_divider=ComponentDividerInput(
                            divider_spacing_size=ComponentDividerSpacingSize.M
                        )
                    )
                )

        return CustomerCard(
            key=CustomerCardKey.order,
            timeToLiveSeconds=86400,
            components=[
                component.model_dump(by_alias=True, exclude_none=True)
                for component in components
            ],
        )

    async def _get_snippets_card(
        self, session: AsyncSession, request: CustomerCardsRequest
    ) -> CustomerCard | None:
        email = request.customer.email

        statement = (
            select(Organization)
            .join(Customer, Customer.organization_id == Organization.id)
            .where(func.lower(Customer.email) == email.lower())
        )
        result = await session.execute(statement)
        organizations = result.unique().scalars().all()

        if len(organizations) == 0:
            return None

        snippets: list[tuple[str, str]] = [
            (
                "Looping In",
                (
                    "I'm looping in the {organization_name} team to the conversation so that they can help you."
                ),
            ),
            (
                "Looping In with guidelines",
                (
                    "I'm looping in the {organization_name} team to the conversation so that they can help you. "
                    "Please allow them up to 48 hours to get back to you ([guidelines for merchants on Polar](https://polar.sh/docs/merchant-of-record/account-reviews#operational-guidelines))."
                ),
            ),
            (
                "Cancellation Portal",
                (
                    "You can perform the cancellation on the following URL: https://polar.sh/{organization_slug}/portal\n"
                ),
            ),
            (
                "Invoice Generation",
                (
                    "You can generate the invoice on the following URL: https://polar.sh/{organization_slug}/portal\n"
                ),
            ),
            (
                "Follow-up 48 hours",
                (
                    "I'm looping in the {organization_name} team again to the conversation. "
                    "Please allow them another 48 hours to get back to you before we [proceed with the documented resolution](https://polar.sh/docs/merchant-of-record/account-reviews#expected-responsiveness)."
                ),
            ),
            (
                "Follow-up Reply All",
                (
                    "I'm looping in the {organization_name} team again to the conversation. "
                    'Please use "Reply All" so as to keep everyone involved in the conversation.'
                ),
            ),
            (
                "Subscription Cancellation",
                ("I have cancelled the subscription immediately."),
            ),
        ]

        def _get_snippet_container(
            organization: Organization,
        ) -> ComponentContainerInput:
            snippets_rows: list[ComponentContainerContentInput] = [
                ComponentContainerContentInput(
                    component_text=ComponentTextInput(
                        text=f"Snippets for {organization.name}",
                    )
                ),
                ComponentContainerContentInput(
                    component_divider=ComponentDividerInput(
                        divider_spacing_size=ComponentDividerSpacingSize.M
                    )
                ),
            ]
            for i, (snippet_name, snippet_text) in enumerate(snippets):
                text = snippet_text.format(
                    organization_name=organization.name,
                    organization_slug=organization.slug,
                )
                snippets_rows.append(
                    ComponentContainerContentInput(
                        component_row=ComponentRowInput(
                            row_main_content=[
                                ComponentRowContentInput(
                                    component_text=ComponentTextInput(
                                        text_size=ComponentTextSize.S,
                                        text_color=ComponentTextColor.MUTED,
                                        text=snippet_name,
                                    ),
                                ),
                                ComponentRowContentInput(
                                    component_text=ComponentTextInput(text=text)
                                ),
                            ],
                            row_aside_content=[
                                ComponentRowContentInput(
                                    component_copy_button=ComponentCopyButtonInput(
                                        copy_button_value=text,
                                        copy_button_tooltip_label="Copy Snippet",
                                    )
                                )
                            ],
                        )
                    )
                )
                if i < len(snippets) - 1:
                    snippets_rows.append(
                        ComponentContainerContentInput(
                            component_spacer=ComponentSpacerInput(
                                spacer_size=ComponentSpacerSize.M
                            )
                        )
                    )

            return ComponentContainerInput(container_content=snippets_rows)

        components: list[ComponentInput] = []
        for i, organization in enumerate(organizations):
            components.append(
                ComponentInput(component_container=_get_snippet_container(organization))
            )
            if i < len(organizations) - 1:
                components.append(
                    ComponentInput(
                        component_divider=ComponentDividerInput(
                            divider_spacing_size=ComponentDividerSpacingSize.M
                        )
                    )
                )

        return CustomerCard(
            key=CustomerCardKey.snippets,
            timeToLiveSeconds=86400,
            components=[
                component.model_dump(by_alias=True, exclude_none=True)
                for component in components
            ],
        )

    def _get_customer_identifier(
        self,
        customer_result: UpsertCustomerUpsertCustomer,
        email: str,
    ) -> CustomerIdentifierInput:
        """
        Get customer identifier for Plain thread creation.

        Prefers external_id if set on the customer result, otherwise falls back to email.
        This handles cases where external_id might not be set on the Plain customer.
        """
        if customer_result.customer is None:
            raise ValueError(
                "Customer not found when creating thread", customer_result, email
            )

        if customer_result.customer.external_id:
            return CustomerIdentifierInput(
                external_id=customer_result.customer.external_id
            )

        return CustomerIdentifierInput(email_address=email)

    async def check_thread_exists(self, customer_email: str, thread_title: str) -> bool:
        """
        Check if a thread with the given title exists for a customer.
        Only considers threads that are not done/closed.
        """
        if not self.enabled:
            log.warning("Plain integration is disabled, assuming no thread exists")
            return False

        log.info("Checking thread existence", customer_email=customer_email)

        async with self._get_plain_client() as plain:
            user = await plain.customer_by_email(email=customer_email)
            log.info("User found", user_id=user)
            if not user:
                log.warning("User not found", email=customer_email)
                return False
            filters = ThreadsFilter(customer_ids=[user.id])
            threads = await plain.threads(filters=filters)
            nr_threads = 0
            for edge in threads.edges:
                thread = edge.node
                if thread.title == thread_title:
                    nr_threads += 1
            log.info(f"There are {nr_threads} threads for user {customer_email}")
            return nr_threads > 0

    @contextlib.asynccontextmanager
    async def _get_plain_client(self) -> AsyncIterator[Plain]:
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {settings.PLAIN_TOKEN}"},
            # Set a MockTransport if not enabled
            # Basically, we disable Plain requests.
            transport=(
                httpx.MockTransport(lambda _: httpx.Response(200))
                if not self.enabled
                else None
            ),
        ) as client:
            async with Plain(
                "https://core-api.uk.plain.com/graphql/v1", http_client=client
            ) as plain:
                yield plain

    async def create_manual_organization_thread(
        self,
        session: AsyncSession,
        organization: Organization,
        admin: User,
        title: str,
    ) -> str:
        """Create a manual thread for an organization with the admin user."""
        if not self.enabled:
            return ""

        async with self._get_plain_client() as plain:
            customer_result = await plain.upsert_customer(
                UpsertCustomerInput(
                    identifier=UpsertCustomerIdentifierInput(email_address=admin.email),
                    on_create=UpsertCustomerOnCreateInput(
                        external_id=str(admin.id),
                        full_name=admin.email,
                        email=EmailAddressInput(
                            email=admin.email, is_verified=admin.email_verified
                        ),
                    ),
                    on_update=UpsertCustomerOnUpdateInput(
                        email=EmailAddressInput(
                            email=admin.email, is_verified=admin.email_verified
                        ),
                    ),
                )
            )
            if customer_result.error is not None:
                raise AccountReviewThreadCreationError(
                    organization.id, customer_result.error.message
                )

            thread_result = await plain.create_thread(
                CreateThreadInput(
                    customer_identifier=self._get_customer_identifier(
                        customer_result, admin.email
                    ),
                    title=title,
                    components=[
                        ComponentInput(
                            component_container=ComponentContainerInput(
                                container_content=[
                                    ComponentContainerContentInput(
                                        component_text=ComponentTextInput(
                                            text=organization.name or organization.slug
                                        )
                                    ),
                                    ComponentContainerContentInput(
                                        component_divider=ComponentDividerInput(
                                            divider_spacing_size=ComponentDividerSpacingSize.M
                                        )
                                    ),
                                    # Organization ID
                                    ComponentContainerContentInput(
                                        component_row=ComponentRowInput(
                                            row_main_content=[
                                                ComponentRowContentInput(
                                                    component_text=ComponentTextInput(
                                                        text="Organization ID",
                                                        text_size=ComponentTextSize.S,
                                                        text_color=ComponentTextColor.MUTED,
                                                    )
                                                ),
                                                ComponentRowContentInput(
                                                    component_text=ComponentTextInput(
                                                        text=str(organization.id)
                                                    )
                                                ),
                                            ],
                                            row_aside_content=[
                                                ComponentRowContentInput(
                                                    component_copy_button=ComponentCopyButtonInput(
                                                        copy_button_value=str(
                                                            organization.id
                                                        ),
                                                        copy_button_tooltip_label="Copy Organization ID",
                                                    )
                                                )
                                            ],
                                        )
                                    ),
                                    # Next Review Threshold
                                    ComponentContainerContentInput(
                                        component_row=ComponentRowInput(
                                            row_main_content=[
                                                ComponentRowContentInput(
                                                    component_text=ComponentTextInput(
                                                        text="Next Review Threshold",
                                                        text_size=ComponentTextSize.S,
                                                        text_color=ComponentTextColor.MUTED,
                                                    )
                                                ),
                                                ComponentRowContentInput(
                                                    component_text=ComponentTextInput(
                                                        text=format_currency(
                                                            organization.next_review_threshold
                                                            / 100,
                                                            "USD",
                                                            locale="en_US",
                                                        )
                                                    )
                                                ),
                                            ],
                                            row_aside_content=[],
                                        )
                                    ),
                                    # Backoffice Link
                                    ComponentContainerContentInput(
                                        component_link_button=ComponentLinkButtonInput(
                                            link_button_url=settings.generate_backoffice_url(
                                                f"/organizations/{organization.id}"
                                            ),
                                            link_button_label="View in Backoffice",
                                        )
                                    ),
                                ]
                            )
                        ),
                    ],
                )
            )

            if thread_result.error is not None:
                raise AccountReviewThreadCreationError(
                    organization.id, thread_result.error.message
                )

            if thread_result.thread is None:
                raise AccountReviewThreadCreationError(
                    organization.id, "Failed to create thread: no thread returned"
                )

            return thread_result.thread.id


plain = PlainService()


# Webhook handling helper functions


def _map_webhook_thread_status(
    status: PlainWebhookThreadStatus | None,
) -> PlainThreadStatus:
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
            existing_thread.status = _map_webhook_thread_status(thread_data.status)
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
            status=_map_webhook_thread_status(thread_data.status),
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
                user_uuid = uuid.UUID(external_id)
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
            statement = select(User).where(
                func.lower(User.email) == customer_email.lower()
            )
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
