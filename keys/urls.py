from django.urls import path, include
from rest_framework.routers import DefaultRouter
from keys.views import APIKeyViewSet

router = DefaultRouter()
router.register(r'apikeys', APIKeyViewSet, basename='apikey')

urlpatterns = [
    # ... other URL patterns ...
    path('api/', include(router.urls)),
]
