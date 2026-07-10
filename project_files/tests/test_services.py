from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from project_files.models import ProjectFile
from project_files.services import (
    build_storage_url,
    create_project_file,
    delete_project_file,
    resolve_canonical_content_type,
    save_uploaded_file_bytes,
)
from projects.models import Project

User = get_user_model()


class ResolveCanonicalContentTypeTest(TestCase):
    def test_recognizes_each_supported_extension(self):
        cases = {
            'notes.pdf': 'application/pdf',
            'notes.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'notes.txt': 'text/plain',
            'notes.md': 'text/markdown',
            'photo.png': 'image/png',
            'photo.jpg': 'image/jpeg',
            'photo.jpeg': 'image/jpeg',
            'photo.webp': 'image/webp',
            'photo.gif': 'image/gif',
        }
        for filename, expected in cases.items():
            self.assertEqual(resolve_canonical_content_type(filename), expected)

    def test_is_case_insensitive(self):
        self.assertEqual(resolve_canonical_content_type('NOTES.PDF'), 'application/pdf')

    def test_unsupported_extension_raises(self):
        with self.assertRaises(ValueError):
            resolve_canonical_content_type('archive.zip')

    def test_no_extension_raises(self):
        with self.assertRaises(ValueError):
            resolve_canonical_content_type('README')


class SaveUploadedFileBytesTest(TestCase):
    @patch('project_files.services.default_storage')
    def test_saves_under_project_files_prefix_with_matching_extension(self, mock_storage):
        mock_storage.save.return_value = 'project_files/some-uuid.pdf'
        key = save_uploaded_file_bytes(b'%PDF-1.4...', 'report.pdf')
        self.assertEqual(key, 'project_files/some-uuid.pdf')
        args, _ = mock_storage.save.call_args
        self.assertTrue(args[0].startswith('project_files/'))
        self.assertTrue(args[0].endswith('.pdf'))


class BuildStorageUrlTest(TestCase):
    def test_empty_key_returns_empty_string(self):
        self.assertEqual(build_storage_url(''), '')

    @patch('project_files.services.default_storage')
    def test_prepends_backend_url_for_relative_storage_url(self, mock_storage):
        mock_storage.url.return_value = '/media/project_files/x.pdf'
        url = build_storage_url('project_files/x.pdf')
        self.assertTrue(url.startswith('http'))
        self.assertTrue(url.endswith('/media/project_files/x.pdf'))

    @patch('project_files.services.default_storage')
    def test_leaves_absolute_storage_url_untouched(self, mock_storage):
        mock_storage.url.return_value = 'https://pub-x.r2.dev/project_files/x.pdf'
        url = build_storage_url('project_files/x.pdf')
        self.assertEqual(url, 'https://pub-x.r2.dev/project_files/x.pdf')


class CreateProjectFileTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='fileowner', password='pass')
        self.project = Project.objects.create(user=self.user, name='Research')

    @patch('project_files.services.default_storage')
    def test_creates_row_with_pending_status_and_no_task_dispatched_yet(self, mock_storage):
        # Stage 2 scope: no Celery task exists yet (added in Stage 3), so
        # create_project_file must not try to dispatch one — the file just
        # stays 'pending' until that stage lands.
        mock_storage.save.return_value = 'project_files/x.txt'
        uploaded = SimpleUploadedFile('notes.txt', b'hello world', content_type='text/plain')
        file_obj = create_project_file(self.project, uploaded)
        self.assertEqual(file_obj.status, 'pending')
        self.assertEqual(file_obj.original_filename, 'notes.txt')
        self.assertEqual(file_obj.content_type, 'text/plain')
        self.assertEqual(file_obj.size_bytes, len(b'hello world'))
        self.assertTrue(ProjectFile.objects.filter(pk=file_obj.pk).exists())


class DeleteProjectFileTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='fileowner', password='pass')
        self.project = Project.objects.create(user=self.user, name='Research')

    @patch('project_files.services.default_storage')
    def test_deletes_storage_object_and_db_row(self, mock_storage):
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='a.txt', content_type='text/plain', size_bytes=1,
            storage_key='project_files/a.txt',
        )
        delete_project_file(file_obj)
        mock_storage.delete.assert_called_once_with('project_files/a.txt')
        self.assertFalse(ProjectFile.objects.filter(pk=file_obj.pk).exists())

    @patch('project_files.services.default_storage')
    def test_deletes_thumbnail_too_when_present(self, mock_storage):
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='a.png', content_type='image/png', size_bytes=1,
            storage_key='project_files/a.png', thumbnail_storage_key='project_files/thumb_a.png',
        )
        delete_project_file(file_obj)
        self.assertEqual(mock_storage.delete.call_count, 2)

    @patch('project_files.services.default_storage')
    def test_no_thumbnail_delete_call_when_none_exists(self, mock_storage):
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='a.txt', content_type='text/plain', size_bytes=1,
            storage_key='project_files/a.txt',
        )
        delete_project_file(file_obj)
        mock_storage.delete.assert_called_once_with('project_files/a.txt')
