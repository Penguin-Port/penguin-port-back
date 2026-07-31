from django.core import signing


PORTAL_SESSION_SALT = "smart-wifi-pass.portal-session"


def create_portal_session(*, customer_key: str, store_id: str, max_age_seconds: int = 86400):
    return signing.dumps(
        {
            "customerKey": customer_key,
            "storeId": store_id,
            "maxAge": max_age_seconds,
        },
        salt=PORTAL_SESSION_SALT,
        compress=True,
    )


def read_portal_session(request):
    token = request.headers.get("X-Portal-Session", "")
    if not token:
        raise PermissionError("X-Portal-Session 헤더가 필요합니다.")
    try:
        payload = signing.loads(token, salt=PORTAL_SESSION_SALT, max_age=86400)
    except signing.BadSignature as exc:
        raise PermissionError("Portal Session이 유효하지 않거나 만료되었습니다.") from exc
    return payload
