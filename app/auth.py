import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Header, HTTPException

from app.config import settings


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 160_000)
    return f"pbkdf2$160000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, rounds, salt_hex, digest_hex = encoded.split("$", 3)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(actual.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def issue_token(payload: dict[str, Any], *, minutes: int) -> str:
    claims = {**payload, "exp": datetime.now(timezone.utc) + timedelta(minutes=minutes)}
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="토큰이 유효하지 않습니다.") from exc


def require_admin(authorization: str = Header(...)) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer 토큰이 필요합니다.")
    claims = decode_token(authorization.removeprefix("Bearer ").strip())
    if claims.get("kind") != "admin":
        raise HTTPException(status_code=401, detail="관리자 토큰이 아닙니다.")
    return claims


def require_portal_session(x_portal_session: str = Header(...)) -> dict[str, Any]:
    if not x_portal_session:
        raise HTTPException(status_code=401, detail="X-Portal-Session 헤더가 필요합니다.")
    claims = decode_token(x_portal_session)
    if claims.get("kind") != "portal":
        raise HTTPException(status_code=401, detail="Portal Session이 아닙니다.")
    return claims


def require_demo_key(x_demo_key: str = Header(...)) -> None:
    if not x_demo_key or not hmac.compare_digest(x_demo_key, settings.demo_key):
        raise HTTPException(status_code=401, detail="X-Demo-Key가 유효하지 않습니다.")
