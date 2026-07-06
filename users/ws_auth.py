from http.cookies import SimpleCookie

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser

from users.authentication import CookieJWTAuthentication


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
        except Exception:
            return AnonymousUser()
