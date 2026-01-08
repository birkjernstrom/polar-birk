import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from polar.integrations.plain.ai_drafting import (
    DraftResponse,
    PlainAIDraftingService,
)
from polar.models import PlainMessage, PlainThread
from polar.models.plain import (
    PlainMessageChannel,
    PlainMessageDirection,
    PlainMessageSenderType,
    PlainThreadStatus,
)


class TestPlainAIDraftingService:
    """Tests for the PlainAIDraftingService class."""

    def test_service_disabled_without_api_key(self) -> None:
        """Service should be disabled when OPENAI_API_KEY is not set."""
        with patch("polar.integrations.plain.ai_drafting.settings") as mock_settings:
            mock_settings.PLAIN_AI_DRAFTING_ENABLED = True
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.PLAIN_TOKEN = "test_token"

            service = PlainAIDraftingService()

            assert service.enabled is False
            assert service.agent is None

    def test_service_disabled_without_plain_token(self) -> None:
        """Service should be disabled when PLAIN_TOKEN is not set."""
        with patch("polar.integrations.plain.ai_drafting.settings") as mock_settings:
            mock_settings.PLAIN_AI_DRAFTING_ENABLED = True
            mock_settings.OPENAI_API_KEY = "test_key"
            mock_settings.PLAIN_TOKEN = ""

            service = PlainAIDraftingService()

            assert service.enabled is False
            assert service.agent is None

    def test_service_disabled_by_config(self) -> None:
        """Service should be disabled when PLAIN_AI_DRAFTING_ENABLED is False."""
        with patch("polar.integrations.plain.ai_drafting.settings") as mock_settings:
            mock_settings.PLAIN_AI_DRAFTING_ENABLED = False
            mock_settings.OPENAI_API_KEY = "test_key"
            mock_settings.PLAIN_TOKEN = "test_token"

            service = PlainAIDraftingService()

            assert service.enabled is False
            assert service.agent is None


@pytest.mark.asyncio
class TestPlainAIDraftingServiceGenerateDraft:
    """Tests for generate_draft_response method."""

    async def test_returns_none_when_disabled(self) -> None:
        """Should return None when service is disabled."""
        with patch("polar.integrations.plain.ai_drafting.settings") as mock_settings:
            mock_settings.PLAIN_AI_DRAFTING_ENABLED = False
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.PLAIN_TOKEN = ""

            service = PlainAIDraftingService()

            thread = MagicMock(spec=PlainThread)
            message = MagicMock(spec=PlainMessage)
            message.content = "Test message"

            result = await service.generate_draft_response(message, thread)

            assert result is None

    async def test_returns_none_for_empty_message(self) -> None:
        """Should return None for empty messages."""
        with patch("polar.integrations.plain.ai_drafting.settings") as mock_settings:
            mock_settings.PLAIN_AI_DRAFTING_ENABLED = True
            mock_settings.OPENAI_API_KEY = "test_key"
            mock_settings.PLAIN_TOKEN = "test_token"
            mock_settings.OPENAI_MODEL = "gpt-4"

            # Mock the Agent to avoid actual API calls
            with patch(
                "polar.integrations.plain.ai_drafting.Agent"
            ) as mock_agent_class:
                service = PlainAIDraftingService()
                service.enabled = True  # Force enable

                thread = MagicMock(spec=PlainThread)
                message = MagicMock(spec=PlainMessage)
                message.content = ""

                result = await service.generate_draft_response(message, thread)

                assert result is None

    async def test_returns_none_for_none_content(self) -> None:
        """Should return None for messages with None content."""
        with patch("polar.integrations.plain.ai_drafting.settings") as mock_settings:
            mock_settings.PLAIN_AI_DRAFTING_ENABLED = True
            mock_settings.OPENAI_API_KEY = "test_key"
            mock_settings.PLAIN_TOKEN = "test_token"
            mock_settings.OPENAI_MODEL = "gpt-4"

            with patch("polar.integrations.plain.ai_drafting.Agent"):
                service = PlainAIDraftingService()
                service.enabled = True

                thread = MagicMock(spec=PlainThread)
                message = MagicMock(spec=PlainMessage)
                message.content = None

                result = await service.generate_draft_response(message, thread)

                assert result is None


@pytest.mark.asyncio
class TestPlainAIDraftingServiceCreateNote:
    """Tests for create_draft_note method."""

    async def test_returns_false_without_plain_token(self) -> None:
        """Should return False when PLAIN_TOKEN is not configured."""
        with patch("polar.integrations.plain.ai_drafting.settings") as mock_settings:
            mock_settings.PLAIN_TOKEN = None

            service = PlainAIDraftingService()
            service.enabled = False

            draft = DraftResponse(
                draft="Test draft",
                confidence="high",
                category="general",
            )

            result = await service.create_draft_note("th_123", draft)

            assert result is False

    async def test_creates_note_successfully(self) -> None:
        """Should create note and return True on success."""
        with patch("polar.integrations.plain.ai_drafting.settings") as mock_settings:
            mock_settings.PLAIN_TOKEN = "test_token"

            with patch("polar.integrations.plain.ai_drafting.httpx.AsyncClient") as mock_client_class:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {
                    "data": {
                        "createNote": {
                            "note": {"id": "note_123"},
                            "error": None,
                        }
                    }
                }

                mock_client = AsyncMock()
                mock_client.post.return_value = mock_response
                mock_client_class.return_value.__aenter__.return_value = mock_client

                service = PlainAIDraftingService()
                service.enabled = False  # We're testing create_draft_note independently

                draft = DraftResponse(
                    draft="Test draft response",
                    confidence="high",
                    category="billing",
                )

                result = await service.create_draft_note("th_123", draft)

                assert result is True
                mock_client.post.assert_called_once()

    async def test_returns_false_on_http_error(self) -> None:
        """Should return False when HTTP request fails."""
        with patch("polar.integrations.plain.ai_drafting.settings") as mock_settings:
            mock_settings.PLAIN_TOKEN = "test_token"

            with patch("polar.integrations.plain.ai_drafting.httpx.AsyncClient") as mock_client_class:
                mock_response = MagicMock()
                mock_response.status_code = 500
                mock_response.text = "Internal Server Error"

                mock_client = AsyncMock()
                mock_client.post.return_value = mock_response
                mock_client_class.return_value.__aenter__.return_value = mock_client

                service = PlainAIDraftingService()
                service.enabled = False

                draft = DraftResponse(
                    draft="Test draft",
                    confidence="medium",
                    category="refunds",
                )

                result = await service.create_draft_note("th_123", draft)

                assert result is False

    async def test_returns_false_on_graphql_error(self) -> None:
        """Should return False when GraphQL returns errors."""
        with patch("polar.integrations.plain.ai_drafting.settings") as mock_settings:
            mock_settings.PLAIN_TOKEN = "test_token"

            with patch("polar.integrations.plain.ai_drafting.httpx.AsyncClient") as mock_client_class:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {
                    "errors": [{"message": "Thread not found"}]
                }

                mock_client = AsyncMock()
                mock_client.post.return_value = mock_response
                mock_client_class.return_value.__aenter__.return_value = mock_client

                service = PlainAIDraftingService()
                service.enabled = False

                draft = DraftResponse(
                    draft="Test draft",
                    confidence="low",
                    category="webhooks",
                )

                result = await service.create_draft_note("th_123", draft)

                assert result is False


@pytest.mark.asyncio
class TestPlainAIDraftingServiceProcessInbound:
    """Tests for process_inbound_message method."""

    async def test_returns_false_when_disabled(self) -> None:
        """Should return False when service is disabled."""
        with patch("polar.integrations.plain.ai_drafting.settings") as mock_settings:
            mock_settings.PLAIN_AI_DRAFTING_ENABLED = False
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.PLAIN_TOKEN = ""

            service = PlainAIDraftingService()

            thread = MagicMock(spec=PlainThread)
            message = MagicMock(spec=PlainMessage)

            result = await service.process_inbound_message(message, thread)

            assert result is False


class TestBuildThreadContext:
    """Tests for _build_thread_context helper method."""

    def test_builds_context_with_all_fields(self) -> None:
        """Should build context string with all available fields."""
        with patch("polar.integrations.plain.ai_drafting.settings") as mock_settings:
            mock_settings.PLAIN_AI_DRAFTING_ENABLED = False
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.PLAIN_TOKEN = ""

            service = PlainAIDraftingService()

            thread = MagicMock(spec=PlainThread)
            thread.title = "Help with refund"
            thread.customer_email = "customer@example.com"
            thread.labels = ["billing", "urgent"]
            thread.status = PlainThreadStatus.open
            thread.user_id = "user_123"
            thread.organization_id = "org_456"
            thread.customer_id = "cust_789"

            context = service._build_thread_context(thread)

            assert "Help with refund" in context
            assert "customer@example.com" in context
            assert "billing, urgent" in context
            assert "open" in context
            assert "registered Polar user" in context
            assert "associated with an organization" in context
            assert "made purchases" in context

    def test_builds_context_with_minimal_fields(self) -> None:
        """Should return appropriate message when no context available."""
        with patch("polar.integrations.plain.ai_drafting.settings") as mock_settings:
            mock_settings.PLAIN_AI_DRAFTING_ENABLED = False
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.PLAIN_TOKEN = ""

            service = PlainAIDraftingService()

            thread = MagicMock(spec=PlainThread)
            thread.title = None
            thread.customer_email = None
            thread.labels = []
            thread.status = None
            thread.user_id = None
            thread.organization_id = None
            thread.customer_id = None

            context = service._build_thread_context(thread)

            assert context == "No additional context"


class TestDraftResponseSchema:
    """Tests for the DraftResponse schema."""

    def test_valid_draft_response(self) -> None:
        """Should create valid DraftResponse with all fields."""
        draft = DraftResponse(
            draft="Thank you for reaching out! I understand you'd like to request a refund.",
            confidence="high",
            category="refunds",
        )

        assert draft.draft.startswith("Thank you")
        assert draft.confidence == "high"
        assert draft.category == "refunds"

    def test_confidence_values(self) -> None:
        """Should only accept valid confidence values."""
        for confidence in ["high", "medium", "low"]:
            draft = DraftResponse(
                draft="Test",
                confidence=confidence,  # type: ignore
                category="general",
            )
            assert draft.confidence == confidence
