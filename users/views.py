from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from rest_framework import generics, status
from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie

from .serializers import UserRegisterSerializer, CurrentUserSerializer


def _set_auth_cookies(response, access, refresh=None):
    secure = not settings.DEBUG 
    response.set_cookie('access_token', access, max_age=900, httponly=True, secure=secure, samesite='Lax', path='/')
    if refresh:
        response.set_cookie('refresh_token', refresh, max_age=86400, httponly=True, secure=secure, samesite='Lax', path='/')


@method_decorator(ensure_csrf_cookie, name='dispatch')
class CookieTokenObtainPairView(TokenObtainPairView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        _set_auth_cookies(response, response.data['access'], response.data['refresh'])
        response.data = {'user': request.user.username}
        return response


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

        refresh = RefreshToken.for_user(user)
        response = Response({'detail': 'User registered successfully.'}, status=status.HTTP_201_CREATED)
        _set_auth_cookies(response, str(refresh.access_token), str(refresh))
        return response


@method_decorator(csrf_exempt, name='dispatch')
class LogoutAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        response = Response(status=status.HTTP_204_NO_CONTENT)
        response.delete_cookie('access_token', path='/')
        response.delete_cookie('refresh_token', path='/')
        response.delete_cookie('sessionid', path='/')
        return response


class CurrentUserAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = CurrentUserSerializer(request.user)
        return Response({'user': serializer.data})
