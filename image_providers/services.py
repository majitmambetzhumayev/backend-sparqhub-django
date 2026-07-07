# image_providers/services.py
import uuid
from urllib.parse import urlparse

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

_EXTENSION_BY_MIME_TYPE = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/webp": "webp",
}


def save_generated_image(data: bytes, mime_type: str) -> str:
    """Persists generated image bytes to media storage (R2 in production,
    local disk in dev/CI — see STORAGES in settings.py) and returns an
    absolute URL. Remote storage backends already return a fully-qualified
    URL; local FileSystemStorage only returns a MEDIA_URL-relative one, which
    needs BACKEND_URL prepended manually since this runs from contexts (the
    WS consumer) with no HTTP request object to derive an absolute URL from."""
    extension = _EXTENSION_BY_MIME_TYPE.get(mime_type, "png")
    filename = f"generated_images/{uuid.uuid4()}.{extension}"
    saved_path = default_storage.save(filename, ContentFile(data))
    url = default_storage.url(saved_path)
    if not urlparse(url).netloc:
        url = f"{settings.BACKEND_URL.rstrip('/')}/{url.lstrip('/')}"
    return url
