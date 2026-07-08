from django.contrib.auth import get_user_model
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser, IsAuthenticated, AllowAny
from rest_framework.views import APIView
from rest_framework import generics, mixins, status, viewsets
from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie

from .serializers import (
    AdminUserSerializer,
    EmailVerifiedTokenObtainPairSerializer,
    UserRegisterSerializer,
    CurrentUserSerializer,
)
from .services import confirm_email, confirm_password_reset, request_password_reset, send_confirmation_email


def _set_auth_cookies(response, access, refresh=None):
    secure = not settings.DEBUG
    domain = settings.COOKIE_DOMAIN
    response.set_cookie('access_token', access, max_age=900, httponly=True, secure=secure, samesite='Lax', path='/', domain=domain)
    if refresh:
        response.set_cookie('refresh_token', refresh, max_age=86400, httponly=True, secure=secure, samesite='Lax', path='/', domain=domain)


@method_decorator(ensure_csrf_cookie, name='dispatch')
@method_decorator(csrf_exempt, name='dispatch')
class CookieTokenObtainPairView(TokenObtainPairView):
    serializer_class = EmailVerifiedTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        _set_auth_cookies(response, response.data['access'], response.data['refresh'])
        response.data = {'user': request.user.username}
        return response


@method_decorator(csrf_exempt, name='dispatch')
class CookieTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        _set_auth_cookies(response, response.data['access'])
        return response


@method_decorator(csrf_exempt, name='dispatch')
class RegisterAPIView(generics.CreateAPIView):
    serializer_class = UserRegisterSerializer
    permission_classes = [AllowAny]
    authentication_classes = []

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        send_confirmation_email(user)
        # No cookies set here — login is gated on email_verified
        # (CookieTokenObtainPairView), so registering doesn't log the user
        # in until they've confirmed their email.
        return Response(
            {'detail': 'Registration successful. Please check your email to confirm your account.'},
            status=status.HTTP_201_CREATED,
        )


@method_decorator(csrf_exempt, name='dispatch')
class LogoutAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        response = Response(status=status.HTTP_204_NO_CONTENT)
        # delete_cookie must be called with the same domain the cookie was
        # originally set with, or the browser treats it as a different
        # cookie and the real one never actually gets cleared.
        domain = settings.COOKIE_DOMAIN
        response.delete_cookie('access_token', path='/', domain=domain)
        response.delete_cookie('refresh_token', path='/', domain=domain)
        response.delete_cookie('sessionid', path='/', domain=domain)
        return response


@method_decorator(csrf_exempt, name='dispatch')
class PasswordResetRequestAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email', '')
        if email:
            request_password_reset(email)
        # Always the same response, whether or not the email is registered —
        # the point of this endpoint is to not leak that information.
        return Response({'detail': 'If that email is registered, a reset link has been sent.'})


@method_decorator(csrf_exempt, name='dispatch')
class PasswordResetConfirmAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        uid = request.data.get('uid', '')
        token = request.data.get('token', '')
        new_password = request.data.get('new_password', '')
        if not (uid and token and new_password):
            return Response({'error': 'uid, token and new_password are required.'}, status=status.HTTP_400_BAD_REQUEST)
        if len(new_password) < 8:
            return Response({'error': 'Password must be at least 8 characters.'}, status=status.HTTP_400_BAD_REQUEST)
        if not confirm_password_reset(uid, token, new_password):
            return Response({'error': 'This reset link is invalid or has expired.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'detail': 'Password has been reset.'})


@method_decorator(csrf_exempt, name='dispatch')
class EmailConfirmAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        uid = request.data.get('uid', '')
        token = request.data.get('token', '')
        if not (uid and token):
            return Response({'error': 'uid and token are required.'}, status=status.HTTP_400_BAD_REQUEST)
        if not confirm_email(uid, token):
            return Response({'error': 'This confirmation link is invalid or has expired.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'detail': 'Email confirmed. You can now log in.'})


class CurrentUserAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = CurrentUserSerializer(request.user)
        return Response({'user': serializer.data})


class AdminUserViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Staff-only user management: list/view/edit users, including their
    credits balance. No delete, no role changes — deliberately scoped to
    what's needed rather than a full admin CRUD surface."""
    permission_classes = [IsAdminUser]
    serializer_class = AdminUserSerializer
    queryset = get_user_model().objects.all().order_by('username')
