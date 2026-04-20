from django.urls import path

from .views import MemoryEntryCreateView

urlpatterns = [
    path('memories/', MemoryEntryCreateView.as_view(), name='memory-create'),
]
