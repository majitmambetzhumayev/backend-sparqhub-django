import logging
from http.cookies import SimpleCookie

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import InvalidToken

from users.authentication import CookieJWTAuthentication

logger = logging.getLogger(__name__)


class JWTAuthMiddleware:
    """
    Channels' AuthMiddlewareStack authenticates via Django's session cookie.
    This app authenticates via a JWT stored in the 'access_token' httpOnly
    cookie (see CookieJWTAuthentication), so WebSocket connections need their
    own middleware to resolve scope["user"] the same way.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        scope["user"] = await self._authenticate(scope)
        return await self.app(scope, receive, send)

    @database_sync_to_async
    def _authenticate(self, scope):
        headers = dict(scope.get("headers", []))
        cookie_header = headers.get(b"cookie", b"").decode()
        cookies = SimpleCookie()
        cookies.load(cookie_header)

        if "access_token" not in cookies:
            return AnonymousUser()

        auth = CookieJWTAuthentication()
        try:
            validated_token = auth.get_validated_token(cookies["access_token"].value)
            return auth.get_user(validated_token)
        except (InvalidToken, AuthenticationFailed):
            # Expired/malformed token, unknown/inactive user — the everyday
            # "not really logged in" case, not worth logging.
            return AnonymousUser()
        except Exception:
            # Anything else (e.g. a DB error resolving the user) is not an
            # expected auth failure and would otherwise vanish with zero
            # trace, indistinguishable from a normal anonymous connection.
            logger.exception("Unexpected error authenticating WebSocket connection")
            return AnonymousUser()
