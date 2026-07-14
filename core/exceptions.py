# core/exceptions.py
import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    """DRF's default handler only formats APIException/Http404/PermissionDenied
    subclasses — anything else (a bug in a view/service reached without its
    own try/except) previously fell through to Django's generic 500 handling,
    violating this repo's own convention of always returning structured JSON
    errors (CLAUDE.md: 'Return structured JSON errors: {"error": "<message>"}
    for 4xx, {"error": "<message>"} for 5xx')."""
    response = drf_exception_handler(exc, context)
    if response is not None:
        return response
    logger.exception("Unhandled exception in view %s", context.get('view'))
    return Response({'error': 'An unexpected error occurred.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
