from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from django.http import JsonResponse

# Auth & CSRF
from users.views import (
    CookieTokenObtainPairView,
    CookieTokenRefreshView,
    LogoutAPIView,
    CurrentUserAPIView,
    RegisterAPIView,
)
from core.views import CsrfTokenView, QuickChatDataAPIView
from threads.views import ThreadListAPIView

# Assistants & API Keys viewsets
from assistants.views import AssistantViewSet
from keys.views import APIKeyViewSet

# Threading and messaging
# chat_messages/urls.py defines:
#   path('threads/<int:thread_id>/messages/', SendMessageAPIView.as_view(), name='message-list')

urlpatterns = []

# Admin
urlpatterns += [
    path('admin/', admin.site.urls),
]

# Authentication & CSRF
urlpatterns += [
    path('api/auth/login/',    CookieTokenObtainPairView.as_view(), name='auth-login'),
    path('api/auth/register/', RegisterAPIView.as_view(),         name='auth-register'),
    path('api/auth/refresh/',  CookieTokenRefreshView.as_view(),   name='auth-refresh'),
    path('api/auth/logout/',   LogoutAPIView.as_view(),            name='auth-logout'),
    path('api/auth/me/',       CurrentUserAPIView.as_view(),       name='auth-me'),
    path('api/csrf/',          CsrfTokenView.as_view(),            name='csrf'),
]

# Quick-Chat metadata endpoint
urlpatterns += [
    path('api/quick-chat/', QuickChatDataAPIView.as_view(), name='quick-chat'),
]

# Thread list
urlpatterns += [
    path('api/threads/', ThreadListAPIView.as_view(), name='thread-list'),
]

# Main API router for viewsets
router = DefaultRouter()
router.register('assistants', AssistantViewSet, basename='assistant')
router.register('apikeys',    APIKeyViewSet,    basename='apikey')
urlpatterns += [
    path('api/', include(router.urls)),
]

# Chat messages URLs (nested under /api/)
urlpatterns += [
    path('api/', include('chat_messages.urls')),
]

# Librarian (RAG memories)
urlpatterns += [
    path('api/', include('librarian.urls')),
]

# Healthcheck
def healthcheck(request):
    return JsonResponse({"status": "ok"})

urlpatterns += [
    path('api/healthcheck/', healthcheck, name='healthcheck'),
]
