# users/authentication.py (new file)
from rest_framework_simplejwt.authentication import JWTAuthentication

class CookieJWTAuthentication(JWTAuthentication):
    """
    Extend SimpleJWT to read JWT from an HttpOnly cookie 
    named 'access_token' if Authorization header is missing.
    """
    def authenticate(self, request):
        # First try the normal header-based authentication
        user_auth_tuple = super().authenticate(request)
        if user_auth_tuple is not None:
            return user_auth_tuple

        # Fall back to cookie-based
        raw_token = request.COOKIES.get('access_token')
        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)
        return self.get_user(validated_token), validated_token
