from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from django.http import JsonResponse

# Auth & CSRF
from users.views import (
    AdminUserViewSet,
    CookieTokenObtainPairView,
    CookieTokenRefreshView,
    LogoutAPIView,
    CurrentUserAPIView,
    PasswordResetConfirmAPIView,
    PasswordResetRequestAPIView,
    RegisterAPIView,
)
from core.views import CsrfTokenView
from threads.views import ThreadListAPIView, ThreadDetailAPIView

# Assistants viewset
from assistants.views import AssistantViewSet, AvailableProvidersAPIView

# API keys viewset
from keys.views import APIKeyViewSet

# Projects viewset
from projects.views import ProjectViewSet

# MCP servers viewset
from mcp_client.views import MCPServerViewSet

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
    path('api/auth/password-reset/request/', PasswordResetRequestAPIView.as_view(), name='password-reset-request'),
    path('api/auth/password-reset/confirm/', PasswordResetConfirmAPIView.as_view(), name='password-reset-confirm'),
    path('api/csrf/',          CsrfTokenView.as_view(),            name='csrf'),
]

# Thread list & detail
urlpatterns += [
    path('api/threads/', ThreadListAPIView.as_view(), name='thread-list'),
    path('api/threads/<int:pk>/', ThreadDetailAPIView.as_view(), name='thread-detail'),
]

# Main API router for viewsets
router = DefaultRouter()
router.register('assistants', AssistantViewSet, basename='assistant')
router.register('apikeys', APIKeyViewSet, basename='apikey')
router.register('projects', ProjectViewSet, basename='project')
router.register('mcp-servers', MCPServerViewSet, basename='mcpserver')
router.register('admin/users', AdminUserViewSet, basename='admin-user')
urlpatterns += [
    path('api/', include(router.urls)),
    path('api/providers/', AvailableProvidersAPIView.as_view(), name='providers'),
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

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

    # Dev-only: hit this to confirm Sentry is actually receiving events.
    # Gated behind DEBUG so it can never ship as a live, unauthenticated
    # crash-on-demand endpoint.
    def _sentry_debug(request):
        1 / 0

    urlpatterns += [path('sentry-debug/', _sentry_debug)]
