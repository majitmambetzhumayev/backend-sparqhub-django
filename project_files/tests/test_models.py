from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from project_files.models import ProjectFile, ProjectFileChunk
from projects.models import Project

User = get_user_model()


class ProjectFileModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='fileowner', password='pass')
        self.project = Project.objects.create(user=self.user, name='Research')

    def test_default_status_is_pending(self):
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='notes.txt', content_type='text/plain', size_bytes=10,
            storage_key='project_files/x.txt',
        )
        self.assertEqual(file_obj.status, 'pending')

    def test_ordering_is_newest_first(self):
        older = ProjectFile.objects.create(
            project=self.project, original_filename='a.txt', content_type='text/plain', size_bytes=1,
            storage_key='project_files/a.txt',
        )
        newer = ProjectFile.objects.create(
            project=self.project, original_filename='b.txt', content_type='text/plain', size_bytes=1,
            storage_key='project_files/b.txt',
        )
        self.assertEqual(list(ProjectFile.objects.all()), [newer, older])

    def test_deleting_file_cascades_to_chunks(self):
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='doc.pdf', content_type='application/pdf', size_bytes=100,
            storage_key='project_files/doc.pdf', status='ready',
        )
        ProjectFileChunk.objects.create(
            file=file_obj, project=self.project, chunk_index=0, content='hello', embedding=[0.0] * 1024,
        )
        file_obj.delete()
        self.assertEqual(ProjectFileChunk.objects.count(), 0)

    def test_deleting_project_cascades_to_files_and_chunks(self):
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='doc.pdf', content_type='application/pdf', size_bytes=100,
            storage_key='project_files/doc.pdf', status='ready',
        )
        ProjectFileChunk.objects.create(
            file=file_obj, project=self.project, chunk_index=0, content='hello', embedding=[0.0] * 1024,
        )
        self.project.delete()
        self.assertEqual(ProjectFile.objects.count(), 0)
        self.assertEqual(ProjectFileChunk.objects.count(), 0)

    def test_unique_chunk_index_per_file(self):
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='doc.pdf', content_type='application/pdf', size_bytes=100,
            storage_key='project_files/doc.pdf', status='ready',
        )
        ProjectFileChunk.objects.create(file=file_obj, project=self.project, chunk_index=0, content='a', embedding=[0.0] * 1024)
        # Wrapped in its own atomic() so the expected IntegrityError only
        # rolls back this savepoint, not the whole test's wrapping
        # transaction — the documented Django pattern for asserting on a
        # DB-level constraint violation.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProjectFileChunk.objects.create(file=file_obj, project=self.project, chunk_index=0, content='b', embedding=[0.0] * 1024)
