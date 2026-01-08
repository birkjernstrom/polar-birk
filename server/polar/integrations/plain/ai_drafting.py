"""AI-powered response drafting for Plain support threads.

This module provides AI-generated draft responses for incoming customer messages
using Polar's documentation as context. Drafts are saved as notes on Plain threads
for support staff to review and copy-paste.
"""

import asyncio
from typing import Literal

import httpx
import structlog
from pydantic import Field
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from polar.config import settings
from polar.kit.schemas import Schema
from polar.models import PlainMessage, PlainThread

log = structlog.get_logger(__name__)


class DraftResponse(Schema):
    """AI-generated draft response for a customer message."""

    draft: str = Field(
        ...,
        description="A friendly, helpful draft response to the customer's message. Should be professional, empathetic, and address the customer's question or concern directly.",
    )
    confidence: Literal["high", "medium", "low"] = Field(
        ...,
        description="How confident the AI is that this response will be helpful. 'high' means the question is clearly about Polar features covered in documentation. 'medium' means partial match. 'low' means the question may need human expertise.",
    )
    category: str = Field(
        ...,
        description="Category of the customer's question, e.g., 'billing', 'refunds', 'checkout', 'subscriptions', 'webhooks', 'api', 'account', 'general'.",
    )


POLAR_KNOWLEDGE_BASE = """
# Polar Support Knowledge Base

## About Polar
Polar is an open source payment infrastructure platform for developers. It acts as a Merchant of Record (MoR), meaning Polar is the reseller of all digital goods and services sold through the platform.

## Transaction Fees
- Base fee: 4% + 40¢ on all transactions
- +1.5% for international cards (non-US)
- +0.5% for subscription payments
- Polar covers Stripe's base fees (2.9% + 30¢) within their fee

## Refunds
- Full and partial refunds are supported
- Go to order details > Refund section to issue refunds
- Payment fees are NOT refunded (industry standard - credit card networks charge regardless)
- Example: A $30 order costs ~$1.6 in fees. Customer gets $30 refund, but $1.6 fee remains
- Polar reserves the right to issue refunds within 60 days to prevent chargebacks
- For subscriptions, you must cancel the subscription first - benefits are revoked when subscription is revoked

## Disputes & Chargebacks
- Disputes cost $15 per dispute regardless of outcome
- This fee is charged by credit card networks and cannot be refunded
- Polar monitors chargeback rates and may intervene if rates exceed 0.4%
- Credit card networks consider 0.7% chargeback rate excessive

## Account Reviews
- First payout requires: business survey + identity verification (KYC)
- Continuous reviews occur at certain sales thresholds
- Reviews typically complete within a week
- Payouts are paused during reviews but customers can still purchase

## Customer Portal
- Customers can access their purchases at: https://polar.sh/{organization_slug}/portal
- Portal allows: viewing purchases, managing subscriptions, downloading invoices
- Subscription cancellations can be done through the portal

## Checkout
- Embedded checkout available with code snippet or JavaScript library
- Supports Apple Pay and Google Pay (with domain validation for embedded)
- Checkout Links can be created for easy sharing

## Webhooks
- Follow Standard Webhooks specification
- SDKs available for TypeScript and Python with built-in validation
- Webhook secret must be base64 encoded for custom validation
- 10 retry attempts with exponential backoff on failure
- 20 second timeout per request

## Acceptable Use Policy
Polar focuses exclusively on digital products. Prohibited:
- Physical goods
- Human services (consulting, marketing, etc.)
- Adult content
- Gambling
- Financial services/advice
- Illegal or age-restricted content
- Low-quality products

## Operational Guidelines
- Merchants must respond to support communications within 48 hours
- Failure to respond may result in refunds and warnings
- Test transactions must use sandbox or 100% discounts (no real money testing)

## Common Customer Portal URLs
- Portal: https://polar.sh/{org}/portal
- Cancellation: Managed through portal subscription management

## Contact
- Support email: support@polar.sh
- Documentation: https://polar.sh/docs
"""

SYSTEM_PROMPT = """
You are a friendly and helpful support agent for Polar, a payment infrastructure platform for developers.

Your task is to draft a response to a customer support message. The response should be:
1. Friendly and empathetic - acknowledge the customer's situation
2. Clear and concise - get to the point while being helpful
3. Accurate - only provide information you're confident about based on the knowledge base
4. Professional but warm - not robotic or overly formal

Guidelines:
- If you can answer the question from the knowledge base, do so with confidence
- If the question is partially covered, answer what you can and note what might need human follow-up
- If you're unsure, it's better to acknowledge that and suggest human follow-up
- Never make up information not in the knowledge base
- Include relevant links when helpful (e.g., customer portal URL)
- For technical questions you can't answer, suggest they check the documentation or that a team member will follow up

Start your response directly - don't begin with "Hi [Name]," since the support agent will add their own greeting.
"""


class PlainAIDraftingService:
    """Service for generating AI draft responses for Plain support messages."""

    def __init__(self) -> None:
        self.enabled = (
            settings.PLAIN_AI_DRAFTING_ENABLED
            and bool(settings.OPENAI_API_KEY)
            and bool(settings.PLAIN_TOKEN)
        )
        if self.enabled:
            provider = OpenAIProvider(api_key=settings.OPENAI_API_KEY)
            self.model = OpenAIChatModel(settings.OPENAI_MODEL, provider=provider)
            self.agent = Agent(
                self.model,
                output_type=DraftResponse,
                system_prompt=SYSTEM_PROMPT,
            )
        else:
            self.model = None
            self.agent = None

    async def generate_draft_response(
        self,
        message: PlainMessage,
        thread: PlainThread,
        timeout_seconds: int = 30,
    ) -> DraftResponse | None:
        """Generate a draft response for an inbound customer message.

        Args:
            message: The inbound PlainMessage from the customer
            thread: The PlainThread this message belongs to
            timeout_seconds: Maximum time to wait for AI response

        Returns:
            DraftResponse with the generated draft, or None if generation failed
        """
        if not self.enabled or self.agent is None:
            log.warning("AI drafting is disabled - missing API keys")
            return None

        if message.content is None or not message.content.strip():
            log.debug("Skipping draft generation for empty message")
            return None

        try:
            # Build context about the thread and customer
            thread_context = self._build_thread_context(thread)

            prompt = f"""
{POLAR_KNOWLEDGE_BASE}

---

THREAD CONTEXT:
{thread_context}

CUSTOMER MESSAGE:
{message.content}

Generate a draft response to help this customer.
"""

            result = await asyncio.wait_for(
                self.agent.run(prompt), timeout=timeout_seconds
            )

            log.info(
                "Generated draft response",
                thread_id=str(thread.id),
                message_id=str(message.id),
                confidence=result.output.confidence,
                category=result.output.category,
            )

            return result.output

        except TimeoutError:
            log.warning(
                "AI draft generation timed out",
                thread_id=str(thread.id),
                message_id=str(message.id),
                timeout_seconds=timeout_seconds,
            )
            return None
        except Exception:
            log.exception(
                "Error generating AI draft response",
                thread_id=str(thread.id),
                message_id=str(message.id),
            )
            return None

    async def create_draft_note(
        self,
        thread_plain_id: str,
        draft: DraftResponse,
    ) -> bool:
        """Create a note on the Plain thread with the draft response.

        Args:
            thread_plain_id: The Plain thread ID to add the note to
            draft: The generated draft response

        Returns:
            True if the note was created successfully, False otherwise
        """
        if not settings.PLAIN_TOKEN:
            log.warning("Cannot create Plain note - PLAIN_TOKEN not configured")
            return False

        # Format the note content with metadata
        confidence_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}[draft.confidence]

        note_content = f"""**AI Draft Response** {confidence_emoji} ({draft.confidence} confidence)
Category: {draft.category}

---

{draft.draft}

---
_This is an AI-generated draft. Please review and personalize before sending._"""

        # GraphQL mutation to create a note
        mutation = """
        mutation CreateNote($input: CreateNoteInput!) {
            createNote(input: $input) {
                note {
                    id
                }
                error {
                    message
                    code
                }
            }
        }
        """

        variables = {
            "input": {
                "threadId": thread_plain_id,
                "text": note_content,
            }
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://core-api.uk.plain.com/graphql/v1",
                    json={"query": mutation, "variables": variables},
                    headers={
                        "Authorization": f"Bearer {settings.PLAIN_TOKEN}",
                        "Content-Type": "application/json",
                    },
                    timeout=10.0,
                )

                if response.status_code != 200:
                    log.error(
                        "Failed to create Plain note",
                        status_code=response.status_code,
                        response=response.text,
                    )
                    return False

                result = response.json()
                if "errors" in result:
                    log.error(
                        "GraphQL error creating Plain note",
                        errors=result["errors"],
                    )
                    return False

                data = result.get("data", {}).get("createNote", {})
                if data.get("error"):
                    log.error(
                        "Plain API error creating note",
                        error=data["error"],
                    )
                    return False

                log.info(
                    "Created AI draft note on Plain thread",
                    thread_plain_id=thread_plain_id,
                    note_id=data.get("note", {}).get("id"),
                )
                return True

        except Exception:
            log.exception(
                "Error creating Plain note",
                thread_plain_id=thread_plain_id,
            )
            return False

    async def process_inbound_message(
        self,
        message: PlainMessage,
        thread: PlainThread,
    ) -> bool:
        """Process an inbound message and create a draft note if appropriate.

        Args:
            message: The inbound PlainMessage from the customer
            thread: The PlainThread this message belongs to

        Returns:
            True if a draft was generated and posted, False otherwise
        """
        if not self.enabled:
            return False

        # Generate the draft response
        draft = await self.generate_draft_response(message, thread)
        if draft is None:
            return False

        # Create the note on Plain
        return await self.create_draft_note(thread.plain_id, draft)

    def _build_thread_context(self, thread: PlainThread) -> str:
        """Build context string about the thread for the AI."""
        context_parts = []

        if thread.title:
            context_parts.append(f"Thread Title: {thread.title}")

        if thread.customer_email:
            context_parts.append(f"Customer Email: {thread.customer_email}")

        if thread.labels:
            context_parts.append(f"Labels: {', '.join(thread.labels)}")

        if thread.status:
            context_parts.append(f"Status: {thread.status}")

        if thread.user_id:
            context_parts.append("Customer is a registered Polar user")

        if thread.organization_id:
            context_parts.append("Customer is associated with an organization")

        if thread.customer_id:
            context_parts.append("Customer has made purchases on Polar")

        return "\n".join(context_parts) if context_parts else "No additional context"


# Global singleton instance
plain_ai_drafting_service = PlainAIDraftingService()
