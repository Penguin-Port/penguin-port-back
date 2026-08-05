import hashlib
import hmac
import os
import secrets
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone

from identity.models import VerificationChallenge
from operations.models import Notification
from operations.services import send_demo_notification
from orders.models import Order
from orders.services import phone_customer_key


VERIFICATION_TICKET_SALT = "smart-wifi-pass.verification-ticket"


def create_verification_ticket(order: Order):
    return signing.dumps(
        {
            "orderId": str(order.id),
            "storeId": str(order.store_id),
            "customerKey": order.customer_key,
        },
        salt=VERIFICATION_TICKET_SALT,
        compress=True,
    )


def read_verification_ticket(ticket: str):
    return signing.loads(ticket, salt=VERIFICATION_TICKET_SALT, max_age=600)


def _hash_code(challenge_id, code: str):
    return hmac.new(
        settings.SECRET_KEY.encode(),
        f"{challenge_id}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


@transaction.atomic
def start_verification(*, verification_ticket: str, phone: str):
    try:
        ticket = read_verification_ticket(verification_ticket)
    except signing.BadSignature as exc:
        raise ValueError("인증 Ticket이 유효하지 않거나 만료되었습니다.") from exc

    order = Order.objects.select_related("store").get(
        id=ticket["orderId"],
        store_id=ticket["storeId"],
    )
    customer_key = phone_customer_key(phone)
    if not secrets.compare_digest(customer_key, ticket["customerKey"]):
        raise ValueError("주문에 연결된 전화번호와 일치하지 않습니다.")

    challenge = VerificationChallenge.objects.create(
        store=order.store,
        order=order,
        customer_key=customer_key,
        phone_last4="".join(character for character in phone if character.isdigit())[-4:],
        code_hash="pending",
        expires_at=timezone.now() + timedelta(minutes=3),
    )
    is_demo_provider = os.getenv("NOTIFICATION_PROVIDER", "DEMO").upper() == "DEMO"
    code = (
        settings.DEMO_OTP_CODE
        if is_demo_provider and settings.DEMO_OTP_CODE
        else f"{secrets.randbelow(1_000_000):06d}"
    )
    challenge.code_hash = _hash_code(challenge.id, code)
    challenge.save(update_fields=["code_hash"])
    send_demo_notification(
        store=order.store,
        channel=Notification.Channel.SMS,
        template="OTP_CODE",
        destination_last4=challenge.phone_last4,
        destination=phone,
        message_body=f"인증번호: {code}",
        payload={
            "challengeId": str(challenge.id),
            "demoCode": code if is_demo_provider else None,
        },
    )
    return challenge, code if is_demo_provider else None


@transaction.atomic
def confirm_verification(*, challenge_id, code: str):
    challenge = (
        VerificationChallenge.objects.select_for_update()
        .select_related("store", "order")
        .get(id=challenge_id)
    )
    if challenge.status != VerificationChallenge.Status.PENDING:
        raise ValueError("이미 종료된 인증 요청입니다.")
    if challenge.expires_at <= timezone.now():
        challenge.status = VerificationChallenge.Status.EXPIRED
        challenge.save(update_fields=["status"])
        raise ValueError("인증번호가 만료되었습니다.")
    if challenge.attempts >= challenge.max_attempts:
        challenge.status = VerificationChallenge.Status.LOCKED
        challenge.save(update_fields=["status"])
        raise ValueError("인증 시도 횟수를 초과했습니다.")

    challenge.attempts += 1
    if not secrets.compare_digest(challenge.code_hash, _hash_code(challenge.id, code)):
        if challenge.attempts >= challenge.max_attempts:
            challenge.status = VerificationChallenge.Status.LOCKED
        challenge.save(update_fields=["attempts", "status"])
        raise ValueError("인증번호가 일치하지 않습니다.")

    challenge.status = VerificationChallenge.Status.VERIFIED
    challenge.verified_at = timezone.now()
    challenge.save(update_fields=["attempts", "status", "verified_at"])
    return challenge
