import hashlib
import hmac

import structlog
from fastapi import Depends, Header, HTTPException, Request
from pydantic import ValidationError

from polar.config import settings
from polar.postgres import AsyncSession, get_db_session
from polar.routing import APIRouter

from .schemas import CustomerCardsRequest, CustomerCardsResponse, PlainWebhookRequest
from .service import plain as plain_service
from .webhook_service import plain_webhook_service

log = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/integrations/plain", tags=["integrations_plain"], include_in_schema=False
)


def _verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    expected_signature = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_signature, signature)


@router.post("/cards")
async def get_cards(
    request: Request,
    customer_cards_request: CustomerCardsRequest,
    plain_request_signature: str = Header(...),
    session: AsyncSession = Depends(get_db_session),
) -> CustomerCardsResponse:
    secret = settings.PLAIN_REQUEST_SIGNING_SECRET
    if secret is None:
        raise HTTPException(status_code=404)

    raw_body = await request.body()
    if not _verify_signature(raw_body, plain_request_signature, secret):
        raise HTTPException(status_code=403)

    return await plain_service.get_cards(session, customer_cards_request)


@router.post("/webhook", status_code=202)
async def webhook(
    request: Request,
    plain_request_signature: str = Header(..., alias="plain-request-signature"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    secret = settings.PLAIN_REQUEST_SIGNING_SECRET
    if secret is None:
        log.warning("Plain webhook received but PLAIN_REQUEST_SIGNING_SECRET not set")
        raise HTTPException(status_code=404)

    raw_body = await request.body()

    if not _verify_signature(raw_body, plain_request_signature, secret):
        log.warning("Plain webhook signature verification failed")
        raise HTTPException(status_code=403)

    try:
        webhook_request = PlainWebhookRequest.model_validate_json(raw_body)
    except ValidationError as e:
        log.warning("Failed to parse Plain webhook payload", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid webhook payload") from e

    log.info(
        "Received Plain webhook",
        webhook_id=webhook_request.id,
        event_type=webhook_request.payload.event_type,
    )

    await plain_webhook_service.handle_webhook(session, webhook_request)

    return {"status": "ok"}
