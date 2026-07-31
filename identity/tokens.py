import hashlib
import secrets
from datetime import timedelta

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from identity.models import RefreshTokenSession


ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=7)
ALGORITHM = "HS256"


def _encode(*, user_id, token_type: str, jti: str, expires_at):
    now = timezone.now()
    return jwt.encode(
        {
            "sub": str(user_id),
            "type": token_type,
            "jti": jti,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        },
        settings.SECRET_KEY,
        algorithm=ALGORITHM,
    )


def decode_token(token: str, *, expected_type: str):
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("토큰 유형이 올바르지 않습니다.")
    return payload


@transaction.atomic
def issue_token_pair(user, *, rotated_from=None):
    now = timezone.now()
    refresh_jti = secrets.token_urlsafe(24)
    refresh_expires_at = now + REFRESH_TTL
    session = RefreshTokenSession.objects.create(
        user=user,
        jti_hash=hashlib.sha256(refresh_jti.encode()).hexdigest(),
        expires_at=refresh_expires_at,
        rotated_from=rotated_from,
    )
    access_jti = secrets.token_urlsafe(16)
    return {
        "accessToken": _encode(
            user_id=user.id,
            token_type="access",
            jti=access_jti,
            expires_at=now + ACCESS_TTL,
        ),
        "accessExpiresIn": int(ACCESS_TTL.total_seconds()),
        "refreshToken": _encode(
            user_id=user.id,
            token_type="refresh",
            jti=refresh_jti,
            expires_at=refresh_expires_at,
        ),
        "refreshExpiresIn": int(REFRESH_TTL.total_seconds()),
        "session": session,
    }


@transaction.atomic
def rotate_refresh_token(token: str):
    payload = decode_token(token, expected_type="refresh")
    jti_hash = hashlib.sha256(payload["jti"].encode()).hexdigest()
    session = (
        RefreshTokenSession.objects.select_for_update()
        .select_related("user")
        .get(jti_hash=jti_hash)
    )
    if session.revoked_at is not None:
        RefreshTokenSession.objects.filter(
            user=session.user, revoked_at__isnull=True
        ).update(revoked_at=timezone.now())
        raise ValueError("이미 사용된 Refresh Token입니다.")
    if session.expires_at <= timezone.now():
        raise ValueError("Refresh Token이 만료되었습니다.")
    session.revoked_at = timezone.now()
    session.save(update_fields=["revoked_at"])
    return issue_token_pair(session.user, rotated_from=session)


@transaction.atomic
def revoke_refresh_token(token: str):
    payload = decode_token(token, expected_type="refresh")
    jti_hash = hashlib.sha256(payload["jti"].encode()).hexdigest()
    return RefreshTokenSession.objects.filter(
        jti_hash=jti_hash, revoked_at__isnull=True
    ).update(revoked_at=timezone.now())


def user_from_access_token(token: str):
    payload = decode_token(token, expected_type="access")
    return get_user_model().objects.get(id=payload["sub"], is_active=True)
