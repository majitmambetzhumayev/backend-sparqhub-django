# users/views.py
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.response import Response
from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from rest_framework import generics, status
from .serializers import UserRegisterSerializer, CurrentUserSerializer
from users.authentication import CookieJWTAuthentication
import logging


logger = logging.getLogger(__name__)

@method_decorator(ensure_csrf_cookie, name='dispatch')
class CookieTokenObtainPairView(TokenObtainPairView):
    def post(self, request, *args, **kwargs):
        resp = super().post(request, *args, **kwargs)
        data = resp.data
        cookie_kwargs = {
            'httponly': True,
            'secure': not settings.DEBUG,
            'samesite': 'Lax',            # safe for same-site dev
            'path': '/',
        }
        resp.set_cookie('access_token', data['access'], max_age=900, **cookie_kwargs)
        resp.set_cookie('refresh_token', data['refresh'], max_age=86400, **cookie_kwargs)
        resp.data = {'user': request.user.username}
        return resp



class CurrentUserAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        logger.debug(f"Incoming cookies: {request.COOKIES}")
        logger.debug(f"Incoming Authorization header: {request.META.get('HTTP_AUTHORIZATION')}")

        user = request.user
        if user and not user.is_anonymous:
            logger.info(f"Authenticated request.user: id={user.id}, username={user.username}")
            serializer = CurrentUserSerializer(user)
            return Response({"user": serializer.data})
        else:
            logger.warning("CurrentUserAPIView: request.user is anonymous or not set")
            return Response({"detail": "Not authenticated"}, status=401)
        

@method_decorator(csrf_exempt, name='dispatch')        
class RegisterAPIView(generics.CreateAPIView):
    serializer_class = UserRegisterSerializer
    permission_classes = [AllowAny]
    authentication_classes = []  # Public endpoint
    

    def create(self, request, *args, **kwargs):
        """
        Upon successful registration, also issue JWTs as HttpOnly cookies.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # Generate tokens for the newly created user
        refresh = RefreshToken.for_user(user)
        access_token = str(refresh.access_token)
        refresh_token = str(refresh)
        
        resp = Response(
            {"detail": "User registered successfully."},
            status=status.HTTP_201_CREATED,
            )
        # Set the access and refresh tokens as HttpOnly cookies

        # Build response containing minimal user info
        resp.set_cookie(
            "access_token",
            access_token,
            max_age=900,
            httponly=True,
            secure=not settings.DEBUG,     # Must be true for SameSite=None
            samesite='None',               # Allow cross-site AJAX & SPA fetches
            path='/',
        )
        resp.set_cookie(
            "refresh_token",
            refresh_token,
            max_age=86400,
            httponly=True,
            secure=not settings.DEBUG,
            samesite='None',
            path='/',
        )

        return resp
    
@method_decorator(csrf_exempt, name='dispatch')
class LogoutAPIView(APIView):
    authentication_classes = []      # no DRF auth checks here
    permission_classes     = [AllowAny]

    def post(self, request):
        resp = Response(status=204)
        # delete JWT cookies
        resp.delete_cookie('access_token',  path='/')
        resp.delete_cookie('refresh_token', path='/')
        # delete Django session cookie
        resp.delete_cookie('sessionid',     path='/')
        return resp

class CookieTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        resp = super().post(request, *args, **kwargs)
        data = resp.data
        cookie_kwargs = {
            'httponly': True,
            'secure': not settings.DEBUG,
            'samesite': 'Lax',
            'path': '/',
        }
        resp.set_cookie('access_token', data['access'], max_age=900, **cookie_kwargs)
        # optionally set new refresh too if returned
        return resp