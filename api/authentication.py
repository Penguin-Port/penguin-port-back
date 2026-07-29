import jwt
from rest_framework import authentication, exceptions

from identity.tokens import user_from_access_token


class JWTAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode()
        if not header:
            return None
        parts = header.split()
        if len(parts) != 2 or parts[0] != self.keyword:
            return None
        try:
            user = user_from_access_token(parts[1])
        except (jwt.InvalidTokenError, ValueError, TypeError):
            raise exceptions.AuthenticationFailed("Access Token이 유효하지 않습니다.")
        return user, parts[1]
