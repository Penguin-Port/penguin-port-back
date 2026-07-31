import base64
import hashlib
import importlib.util

from django.conf import settings


def _fernet():
    if importlib.util.find_spec("cryptography") is None:
        return None
    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def encrypt_phone(phone: str | None) -> str:
    if not phone:
        return ""
    fernet = _fernet()
    if fernet is None:
        # 로컬 최소 의존성 환경에서는 원문을 저장하지 않는다.
        return ""
    normalized = "".join(character for character in phone if character.isdigit())
    return fernet.encrypt(normalized.encode()).decode()


def decrypt_phone(ciphertext: str) -> str | None:
    if not ciphertext:
        return None
    fernet = _fernet()
    if fernet is None:
        raise RuntimeError("cryptography 패키지가 필요합니다.")
    return fernet.decrypt(ciphertext.encode()).decode()
