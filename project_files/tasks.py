import logging

from celery import shared_task
from django.core.files.storage import default_storage

from core.embeddings import embed_batch

from .models import ProjectFile, ProjectFileChunk
from .services import IMAGE_CONTENT_TYPES, chunk_text, extract_text, generate_thumbnail, save_uploaded_file_bytes

logger = logging.getLogger(__name__)


@shared_task
def process_project_file_task(file_id: int) -> None:
    """Unlike librarian's extract_memories_task, this must NOT swallow
    failures silently — ProjectFile has a user-facing status the frontend
    polls, so a failure has to land as status='failed' + error_message
    rather than just a logged exception with nothing else changing."""
    try:
        file_obj = ProjectFile.objects.get(pk=file_id)
    except ProjectFile.DoesNotExist:
        logger.warning("process_project_file_task: file %s no longer exists", file_id)
        return

    file_obj.status = 'processing'
    file_obj.save(update_fields=['status', 'updated_at'])

    try:
        data = default_storage.open(file_obj.storage_key).read()
        if file_obj.content_type in IMAGE_CONTENT_TYPES:
            thumb_key = save_uploaded_file_bytes(
                generate_thumbnail(data, file_obj.content_type), f"thumb_{file_obj.original_filename}",
            )
            file_obj.thumbnail_storage_key = thumb_key
        else:
            chunks = chunk_text(extract_text(data, file_obj.content_type))
            if chunks:
                embeddings = embed_batch(chunks)
                ProjectFileChunk.objects.bulk_create([
                    ProjectFileChunk(file=file_obj, project=file_obj.project, chunk_index=i, content=c, embedding=e)
                    for i, (c, e) in enumerate(zip(chunks, embeddings))
                ])
        file_obj.status = 'ready'
        file_obj.error_message = ''
        file_obj.save(update_fields=['status', 'thumbnail_storage_key', 'error_message', 'updated_at'])
    except Exception as exc:
        logger.exception("Processing failed for file %s", file_id)
        file_obj.status = 'failed'
        file_obj.error_message = str(exc)[:2000]
        file_obj.save(update_fields=['status', 'error_message', 'updated_at'])
