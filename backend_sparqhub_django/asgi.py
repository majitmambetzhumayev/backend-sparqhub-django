import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend_sparqhub_django.settings')
# 1️⃣ Initialize Django ASGI application first, so the app registry is ready.
django_asgi_app = get_asgi_application()  # :contentReference[oaicite:0]{index=0}

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
import chat_messages.routing  # Safe now—Django is fully loaded

application = ProtocolTypeRouter({
    # HTTP → Django views
    "http": django_asgi_app,
    # WebSocket → Channels consumers
    "websocket": AuthMiddlewareStack(
        URLRouter(chat_messages.routing.websocket_urlpatterns)
    ),
})
