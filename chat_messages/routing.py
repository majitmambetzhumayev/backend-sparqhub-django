# chat_messages/routing.py
from django.urls       import re_path
from .consumers        import QuickChatConsumer

websocket_urlpatterns = [
    re_path(r"^ws/quickchat/$", QuickChatConsumer.as_asgi()),
]
