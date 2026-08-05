import hashlib
import hmac

from app.config import settings


def normalize_phone(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    if not 8 <= len(digits) <= 15:
        raise ValueError("전화번호 형식이 올바르지 않습니다.")
    return digits


def phone_lookup_hash(value: str) -> str:
    normalized = normalize_phone(value)
    return hmac.new(
        settings.phone_lookup_secret.encode(),
        normalized.encode(),
        hashlib.sha256,
    ).hexdigest()


def phone_last4(value: str) -> str:
    return normalize_phone(value)[-4:]


def masked_phone(value: str) -> str:
    normalized = normalize_phone(value)
    if len(normalized) <= 7:
        return "*" * max(0, len(normalized) - 2) + normalized[-2:]
    return f"{normalized[:3]}-****-{normalized[-4:]}"


def customer_key(*, member_id: str | None = None, phone: str | None = None) -> str:
    if member_id:
        return f"member:{member_id}"
    if phone:
        return f"phone:{phone_lookup_hash(phone)}"
    raise ValueError("memberId 또는 phone이 필요합니다.")
