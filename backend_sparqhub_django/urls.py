from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from assistants.views import AssistantViewSet
from core.views import QuickChatDataAPIView
from keys.views import APIKeyViewSet

router = DefaultRouter()
router.register(r'assistants', AssistantViewSet, basename='assistant')
router.register(r'apikeys', APIKeyViewSet, basename='apikey')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/quick-chat/', QuickChatDataAPIView.as_view(), name='quick-chat-data'),
    path('api/', include(router.urls)),
    path('api/', include('chat_messages.urls')),
    # Remove the duplicate keys inclusion if router already covers it.
]
