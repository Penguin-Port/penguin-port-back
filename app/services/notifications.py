from __future__ import annotations

import secrets
from typing import Any

from sqlalchemy.orm import Session

from app.models import DemoMessage
from app.security import masked_phone
from integrations.providers import DeliveryResult, ProviderError, get_notification_provider


def generate_otp(*, configured_code: str, provider_name: str) -> str:
    if configured_code and provider_name == "DEMO":
        return configured_code
    return f"{secrets.randbelow(1_000_000):06d}"


def send_otp(
    db: Session,
    *,
    store_id: str,
    phone: str,
    code: str,
    challenge_id: str,
) -> DeliveryResult:
    provider = get_notification_provider()
    body = f"인증번호: {code}"
    try:
        delivery = provider.send_sms(
            destination=phone,
            body=body,
            payload={"challengeId": challenge_id},
        )
    except ProviderError:
        raise

    # The demo inbox keeps only the masked destination. It is also useful when
    # a real provider is selected and the operator wants a local delivery log.
    db.add(
        DemoMessage(
            store_id=store_id,
            channel="SMS",
            destination=masked_phone(phone),
            body=body,
            payload={
                "challengeId": challenge_id,
                "provider": delivery.provider,
                "providerReference": delivery.reference,
                "demoCode": code if delivery.provider == "DEMO" else None,
            },
        )
    )
    return delivery


def send_message(
    *,
    phone: str,
    body: str,
    payload: dict[str, Any] | None = None,
) -> DeliveryResult:
    provider = get_notification_provider()
    return provider.send_sms(destination=phone, body=body, payload=payload or {})

