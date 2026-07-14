from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db.utils import OperationalError
from django.test import TestCase

from project_files.models import ProjectFile, ProjectFileChunk
from project_files.tasks import process_project_file_task
from projects.models import Project

User = get_user_model()


class ProcessProjectFileTaskTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='fileowner', password='pass')
        self.project = Project.objects.create(user=self.user, name='Research')

    @patch('project_files.tasks.embed_batch')
    @patch('project_files.tasks.default_storage')
    def test_document_happy_path_creates_chunks_and_marks_ready(self, mock_storage, mock_embed_batch):
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='notes.txt', content_type='text/plain', size_bytes=11,
            storage_key='project_files/notes.txt',
        )
        mock_storage.open.return_value = MagicMock(read=MagicMock(return_value=b'hello world'))
        mock_embed_batch.return_value = [[0.0] * 1024]

        process_project_file_task(file_obj.id)

        file_obj.refresh_from_db()
        self.assertEqual(file_obj.status, 'ready')
        self.assertEqual(file_obj.error_message, '')
        self.assertEqual(file_obj.thumbnail_storage_key, '')
        self.assertEqual(ProjectFileChunk.objects.filter(file=file_obj).count(), 1)
        self.assertEqual(ProjectFileChunk.objects.get(file=file_obj).content, 'hello world')

    @patch('project_files.tasks.generate_thumbnail')
    @patch('project_files.tasks.save_uploaded_file_bytes')
    @patch('project_files.tasks.default_storage')
    def test_image_happy_path_creates_thumbnail_and_marks_ready_no_chunks(
        self, mock_storage, mock_save_bytes, mock_generate_thumbnail,
    ):
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='photo.png', content_type='image/png', size_bytes=100,
            storage_key='project_files/photo.png',
        )
        mock_storage.open.return_value = MagicMock(read=MagicMock(return_value=b'\x89PNG...'))
        mock_generate_thumbnail.return_value = b'thumb-bytes'
        mock_save_bytes.return_value = 'project_files/thumb_photo.png'

        process_project_file_task(file_obj.id)

        file_obj.refresh_from_db()
        self.assertEqual(file_obj.status, 'ready')
        self.assertEqual(file_obj.thumbnail_storage_key, 'project_files/thumb_photo.png')
        self.assertEqual(ProjectFileChunk.objects.filter(file=file_obj).count(), 0)

    @patch('project_files.tasks.default_storage')
    def test_extraction_failure_marks_failed_with_error_message_and_does_not_raise(self, mock_storage):
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='broken.pdf', content_type='application/pdf', size_bytes=10,
            storage_key='project_files/broken.pdf',
        )
        mock_storage.open.return_value = MagicMock(read=MagicMock(return_value=b'not a real pdf'))

        process_project_file_task(file_obj.id)  # must not raise

        file_obj.refresh_from_db()
        self.assertEqual(file_obj.status, 'failed')
        self.assertNotEqual(file_obj.error_message, '')
        self.assertEqual(ProjectFileChunk.objects.filter(file=file_obj).count(), 0)

    def test_missing_file_row_logs_and_returns_without_raising(self):
        process_project_file_task(999999)  # must not raise

    def test_failure_flipping_status_to_processing_still_marks_file_failed(self):
        # Regression test: the status='processing' save() used to sit outside
        # the try/except — if THAT specific save failed (a transient DB
        # blip), the file was left stuck at 'pending' forever with no
        # error_message, defeating the whole point of this task not
        # swallowing failures silently (unlike librarian's task).
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='notes.txt', content_type='text/plain', size_bytes=11,
            storage_key='project_files/notes.txt',
        )
        real_save = ProjectFile.save
        calls = {'n': 0}

        def flaky_save(self, *args, **kwargs):
            calls['n'] += 1
            if calls['n'] == 1:
                raise OperationalError('db connection blip')
            return real_save(self, *args, **kwargs)

        with patch.object(ProjectFile, 'save', flaky_save):
            process_project_file_task(file_obj.id)  # must not raise

        file_obj.refresh_from_db()
        self.assertEqual(file_obj.status, 'failed')
        self.assertIn('db connection blip', file_obj.error_message)

    @patch('project_files.tasks.embed_batch')
    @patch('project_files.tasks.default_storage')
    def test_empty_extracted_text_marks_ready_with_no_chunks(self, mock_storage, mock_embed_batch):
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='empty.txt', content_type='text/plain', size_bytes=0,
            storage_key='project_files/empty.txt',
        )
        mock_storage.open.return_value = MagicMock(read=MagicMock(return_value=b''))

        process_project_file_task(file_obj.id)

        file_obj.refresh_from_db()
        self.assertEqual(file_obj.status, 'ready')
        self.assertEqual(ProjectFileChunk.objects.filter(file=file_obj).count(), 0)
        mock_embed_batch.assert_not_called()
