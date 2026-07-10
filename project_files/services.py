import uuid
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from .models import ProjectFile

# Resolved from the filename extension, not the browser-supplied
# Content-Type header — that header is unreliable/often empty for .md
# across browsers, so trusting it for validation would be fragile.
_DOCUMENT_EXTENSIONS = {
    '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.txt': 'text/plain',
    '.md': 'text/markdown',
}
_IMAGE_EXTENSIONS = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
}


def resolve_canonical_content_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    canonical = _DOCUMENT_EXTENSIONS.get(ext) or _IMAGE_EXTENSIONS.get(ext)
    if canonical is None:
        raise ValueError(ext or 'unknown')
    return canonical


def save_uploaded_file_bytes(data: bytes, filename: str) -> str:
    """Mirrors image_providers/services.py::save_generated_image, but
    returns only the storage key (path) rather than a pre-built URL — the
    URL is derived on read (see build_storage_url) so it never goes stale
    if R2's public domain ever changes."""
    extension = Path(filename).suffix.lstrip('.').lower() or 'bin'
    key = f"project_files/{uuid.uuid4()}.{extension}"
    return default_storage.save(key, ContentFile(data))


def build_storage_url(storage_key: str) -> str:
    """Same BACKEND_URL-prepending logic as image_providers/services.py —
    remote storage backends already return a fully-qualified URL; local
    FileSystemStorage only returns a MEDIA_URL-relative one."""
    if not storage_key:
        return ''
    url = default_storage.url(storage_key)
    if not urlparse(url).netloc:
        url = f"{settings.BACKEND_URL.rstrip('/')}/{url.lstrip('/')}"
    return url


def create_project_file(project, uploaded_file) -> ProjectFile:
    data = uploaded_file.read()
    storage_key = save_uploaded_file_bytes(data, uploaded_file.name)
    return ProjectFile.objects.create(
        project=project,
        original_filename=uploaded_file.name,
        content_type=resolve_canonical_content_type(uploaded_file.name),
        size_bytes=len(data),
        storage_key=storage_key,
    )


def delete_project_file(file_obj: ProjectFile) -> None:
    default_storage.delete(file_obj.storage_key)
    if file_obj.thumbnail_storage_key:
        default_storage.delete(file_obj.thumbnail_storage_key)
    file_obj.delete()   # cascades to ProjectFileChunk rows
