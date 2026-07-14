import io
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import docx
import pypdf
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageOps
from pgvector.django import CosineDistance

from core.embeddings import embed, embed_batch

from .models import ProjectFile, ProjectFileChunk

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


def save_uploaded_file_bytes(data: bytes, filename: str, content_type: str) -> str:
    """Mirrors image_providers/services.py::save_generated_image, but
    returns only the storage key (path) rather than a pre-built URL — the
    URL is derived on read (see build_storage_url) so it never goes stale
    if R2's public domain ever changes.

    content_type is set explicitly on the ContentFile (read by
    django-storages' S3Storage as the object's stored Content-Type) rather
    than left for mimetypes.guess_type(filename) to infer — that guess
    falls through to "application/octet-stream" for some accepted
    extensions (.webp confirmed), and a file served with that generic
    type, with no nosniff header on R2's origin, is one browsers are
    willing to content-sniff — including sniffing to text/html and
    executing an uploaded file's bytes as script."""
    extension = Path(filename).suffix.lstrip('.').lower() or 'bin'
    key = f"project_files/{uuid.uuid4()}.{extension}"
    content = ContentFile(data)
    content.content_type = content_type
    return default_storage.save(key, content)


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
    canonical_content_type = resolve_canonical_content_type(uploaded_file.name)
    storage_key = save_uploaded_file_bytes(data, uploaded_file.name, canonical_content_type)
    file_obj = ProjectFile.objects.create(
        project=project,
        original_filename=uploaded_file.name,
        content_type=canonical_content_type,
        size_bytes=len(data),
        storage_key=storage_key,
    )
    from project_files.tasks import process_project_file_task
    process_project_file_task.delay(file_obj.id)
    return file_obj


def delete_project_file(file_obj: ProjectFile) -> None:
    default_storage.delete(file_obj.storage_key)
    if file_obj.thumbnail_storage_key:
        default_storage.delete(file_obj.thumbnail_storage_key)
    file_obj.delete()   # cascades to ProjectFileChunk rows


IMAGE_CONTENT_TYPES = frozenset(_IMAGE_EXTENSIONS.values())


def extract_text(data: bytes, content_type: str) -> str:
    if content_type == 'application/pdf':
        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if content_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
        document = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in document.paragraphs)
    if content_type in ('text/plain', 'text/markdown'):
        return data.decode('utf-8', errors='replace')
    raise ValueError(f"No extractor for {content_type}")


# Characters, not tokens — a simple default, not empirically tuned against
# real documents yet. ~15% overlap so a fact split across a chunk boundary
# still has a decent chance of appearing whole in at least one chunk.
_CHUNK_SIZE = 1000
_CHUNK_OVERLAP = 150


def chunk_text(text: str, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    chunks: list[str] = []
    start, length = 0, len(text)
    while start < length:
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= length:
            break
        start = end - overlap
    return [c.strip() for c in chunks if c.strip()]


_THUMBNAIL_SIZE = (320, 320)


def generate_thumbnail(data: bytes, content_type: str) -> bytes:
    # exif_transpose: phone photos are frequently stored with an EXIF
    # rotation flag rather than pre-rotated pixels — without this, portrait
    # photos thumbnail sideways.
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    image.thumbnail(_THUMBNAIL_SIZE)
    buf = io.BytesIO()
    if content_type == 'image/jpeg':
        # JPEG has no alpha channel — flatten first or Pillow raises.
        image.convert('RGB').save(buf, format='JPEG', quality=85)
    else:
        # Covers png/webp/gif — GIF thumbnails lose animation (Pillow's
        # default Image.open takes the first frame only); accepted for v1.
        image.save(buf, format='PNG')
    return buf.getvalue()


def project_has_searchable_files(project_id) -> bool:
    return ProjectFileChunk.objects.filter(project_id=project_id).exists()


@dataclass
class SearchResult:
    filename: str
    chunk_index: int
    content: str


def search_project_files(project_id, query: str, top_k: int = 5) -> list[SearchResult]:
    embedding = embed(query)
    chunks = (
        ProjectFileChunk.objects
        .filter(project_id=project_id, file__status='ready')
        .select_related('file')
        .order_by(CosineDistance('embedding', embedding))[:top_k]
    )
    return [SearchResult(c.file.original_filename, c.chunk_index, c.content) for c in chunks]
