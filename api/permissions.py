import hashlib
import hmac
import os
from datetime import datetime, timezone

from django.core.cache import cache
from rest_framework.permissions import BasePermission


class PosHMACPermission(BasePermission):
    message = "POS 요청 서명이 유효하지 않습니다."

    def has_permission(self, request, view):
        secret = os.getenv("POS_HMAC_SECRET")
        if not secret:
            return True

        timestamp = request.headers.get("X-Timestamp", "")
        nonce = request.headers.get("X-Nonce", "")
        signature = request.headers.get("X-Signature", "")
        if not timestamp or not nonce or not signature:
            return False
        try:
            requested_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return False
        if abs((datetime.now(timezone.utc) - requested_at).total_seconds()) > 300:
            return False
        nonce_key = f"pos-nonce:{nonce}"
        if cache.get(nonce_key):
            return False
        expected = hmac.new(
            secret.encode(),
            timestamp.encode() + nonce.encode() + request.body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return False
        cache.set(nonce_key, True, timeout=600)
        return True
