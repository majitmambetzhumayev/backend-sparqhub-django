from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from project_files.models import ProjectFile
from projects.models import Project

User = get_user_model()


class ProjectFileCRUDAPITest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='fileowner', password='pass')
        self.client.force_authenticate(user=self.user)
        self.project = Project.objects.create(user=self.user, name='Research')
        self.url = reverse('projectfile-list')

    @patch('project_files.tasks.process_project_file_task.delay')
    @patch('project_files.services.default_storage')
    def test_upload_success(self, mock_storage, mock_delay):
        mock_storage.save.return_value = 'project_files/x.txt'
        mock_storage.url.return_value = 'https://pub-x.r2.dev/project_files/x.txt'
        uploaded = SimpleUploadedFile('notes.txt', b'hello world', content_type='text/plain')

        response = self.client.post(self.url, data={'project': self.project.id, 'file': uploaded}, format='multipart')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['original_filename'], 'notes.txt')
        self.assertEqual(response.data['status'], 'pending')
        self.assertTrue(ProjectFile.objects.filter(project=self.project, original_filename='notes.txt').exists())
        mock_delay.assert_called_once()

    @override_settings(PROJECT_FILE_MAX_SIZE_BYTES=10)
    def test_oversized_file_rejected(self):
        uploaded = SimpleUploadedFile('notes.txt', b'this is definitely more than ten bytes', content_type='text/plain')
        response = self.client.post(self.url, data={'project': self.project.id, 'file': uploaded}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('file', response.data)

    def test_disallowed_extension_rejected(self):
        uploaded = SimpleUploadedFile('archive.zip', b'PK...', content_type='application/zip')
        response = self.client.post(self.url, data={'project': self.project.id, 'file': uploaded}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('file', response.data)

    def test_cannot_attach_file_to_another_users_project(self):
        other_user = User.objects.create_user(username='otheruser', password='pass')
        other_project = Project.objects.create(user=other_user, name='Not yours')
        uploaded = SimpleUploadedFile('notes.txt', b'hello', content_type='text/plain')

        response = self.client.post(self.url, data={'project': other_project.id, 'file': uploaded}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_filters_by_project_id_and_ownership(self):
        other_user = User.objects.create_user(username='otheruser', password='pass')
        other_project = Project.objects.create(user=other_user, name='Not yours')
        second_project = Project.objects.create(user=self.user, name='Second')

        ProjectFile.objects.create(
            project=self.project, original_filename='a.txt', content_type='text/plain', size_bytes=1,
            storage_key='project_files/a.txt',
        )
        ProjectFile.objects.create(
            project=second_project, original_filename='b.txt', content_type='text/plain', size_bytes=1,
            storage_key='project_files/b.txt',
        )
        ProjectFile.objects.create(
            project=other_project, original_filename='c.txt', content_type='text/plain', size_bytes=1,
            storage_key='project_files/c.txt',
        )

        response = self.client.get(self.url, {'project_id': self.project.id})
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['original_filename'], 'a.txt')

    @patch('project_files.services.default_storage')
    def test_delete_removes_row_and_calls_storage_delete(self, mock_storage):
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='a.txt', content_type='text/plain', size_bytes=1,
            storage_key='project_files/a.txt',
        )
        detail_url = reverse('projectfile-detail', kwargs={'pk': file_obj.id})

        response = self.client.delete(detail_url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        mock_storage.delete.assert_called_once_with('project_files/a.txt')
        self.assertFalse(ProjectFile.objects.filter(pk=file_obj.id).exists())

    def test_cannot_access_another_users_file(self):
        other_user = User.objects.create_user(username='otheruser', password='pass')
        other_project = Project.objects.create(user=other_user, name='Not yours')
        file_obj = ProjectFile.objects.create(
            project=other_project, original_filename='a.txt', content_type='text/plain', size_bytes=1,
            storage_key='project_files/a.txt',
        )
        detail_url = reverse('projectfile-detail', kwargs={'pk': file_obj.id})

        self.assertEqual(self.client.get(detail_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.delete(detail_url).status_code, status.HTTP_404_NOT_FOUND)

    def test_no_update_capability(self):
        # Files are immutable once uploaded — ProjectFileViewSet deliberately
        # doesn't include UpdateModelMixin.
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='a.txt', content_type='text/plain', size_bytes=1,
            storage_key='project_files/a.txt',
        )
        detail_url = reverse('projectfile-detail', kwargs={'pk': file_obj.id})
        response = self.client.patch(detail_url, data={'original_filename': 'renamed.txt'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class ProjectFileUploadRateLimitTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='uploadratelimituser', password='pass')
        self.client.force_authenticate(user=self.user)
        self.project = Project.objects.create(user=self.user, name='Research')
        self.url = reverse('projectfile-list')

    @patch('project_files.tasks.process_project_file_task.delay')
    @patch('project_files.services.default_storage')
    def test_create_action_is_rate_limited_but_list_is_not(self, mock_storage, mock_delay):
        mock_storage.save.return_value = 'project_files/x.txt'
        mock_storage.url.return_value = 'https://pub-x.r2.dev/project_files/x.txt'

        for i in range(20):
            uploaded = SimpleUploadedFile(f'notes{i}.txt', b'hello world', content_type='text/plain')
            self.client.post(self.url, data={'project': self.project.id, 'file': uploaded}, format='multipart')

        uploaded = SimpleUploadedFile('one-more.txt', b'hello world', content_type='text/plain')
        response = self.client.post(self.url, data={'project': self.project.id, 'file': uploaded}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        # list/retrieve/delete are deliberately not scoped — only the
        # expensive create action is budget-limited.
        list_response = self.client.get(self.url)
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
