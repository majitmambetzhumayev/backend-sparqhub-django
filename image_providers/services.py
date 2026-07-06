# image_providers/services.py
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

_EXTENSION_BY_MIME_TYPE = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/webp": "webp",
}


def save_generated_image(data: bytes, mime_type: str) -> str:
    """Persists generated image bytes to media storage and returns an
    absolute URL — built manually since this runs from contexts (the WS
    consumer) with no HTTP request object to derive one from."""
    extension = _EXTENSION_BY_MIME_TYPE.get(mime_type, "png")
    filename = f"generated_images/{uuid.uuid4()}.{extension}"
    saved_path = default_storage.save(filename, ContentFile(data))
    return f"{settings.BACKEND_URL.rstrip('/')}/{settings.MEDIA_URL.lstrip('/')}{saved_path}"
