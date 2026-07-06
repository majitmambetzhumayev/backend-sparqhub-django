# chat_messages/routing.py
from django.urls       import re_path
from .consumers        import ConversationConsumer

websocket_urlpatterns = [
    re_path(r"^ws/conversations/$", ConversationConsumer.as_asgi()),
]
