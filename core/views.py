#core/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from django.views.decorators.csrf import ensure_csrf_cookie

from rest_framework.permissions import AllowAny
from django.utils.decorators import method_decorator


@method_decorator(ensure_csrf_cookie, name='dispatch')
class CsrfTokenView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        return Response({'detail': 'CSRF cookie set'})
