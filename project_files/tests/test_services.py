import io
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from project_files.models import ProjectFile, ProjectFileChunk
from project_files.services import (
    build_storage_url,
    chunk_text,
    create_project_file,
    delete_project_file,
    extract_text,
    generate_thumbnail,
    project_has_searchable_files,
    resolve_canonical_content_type,
    save_uploaded_file_bytes,
    search_project_files,
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

    @patch('project_files.tasks.process_project_file_task.delay')
    @patch('project_files.services.default_storage')
    def test_creates_row_and_dispatches_processing_task(self, mock_storage, mock_delay):
        mock_storage.save.return_value = 'project_files/x.txt'
        uploaded = SimpleUploadedFile('notes.txt', b'hello world', content_type='text/plain')
        file_obj = create_project_file(self.project, uploaded)
        self.assertEqual(file_obj.status, 'pending')
        self.assertEqual(file_obj.original_filename, 'notes.txt')
        self.assertEqual(file_obj.content_type, 'text/plain')
        self.assertEqual(file_obj.size_bytes, len(b'hello world'))
        self.assertTrue(ProjectFile.objects.filter(pk=file_obj.pk).exists())
        mock_delay.assert_called_once_with(file_obj.id)


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


class ExtractTextTest(TestCase):
    @patch('project_files.services.pypdf.PdfReader')
    def test_extracts_and_joins_pdf_pages(self, mock_reader_cls):
        page1, page2 = MagicMock(), MagicMock()
        page1.extract_text.return_value = 'Page one.'
        page2.extract_text.return_value = 'Page two.'
        mock_reader_cls.return_value = MagicMock(pages=[page1, page2])

        result = extract_text(b'%PDF-1.4...', 'application/pdf')

        self.assertEqual(result, 'Page one.\nPage two.')

    @patch('project_files.services.pypdf.PdfReader')
    def test_pdf_page_with_no_extractable_text_becomes_empty_line(self, mock_reader_cls):
        page = MagicMock()
        page.extract_text.return_value = None
        mock_reader_cls.return_value = MagicMock(pages=[page])

        result = extract_text(b'%PDF-1.4...', 'application/pdf')

        self.assertEqual(result, '')

    @patch('project_files.services.docx.Document')
    def test_extracts_and_joins_docx_paragraphs(self, mock_document_cls):
        p1, p2 = MagicMock(text='First paragraph.'), MagicMock(text='Second paragraph.')
        mock_document_cls.return_value = MagicMock(paragraphs=[p1, p2])

        result = extract_text(b'PK...', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')

        self.assertEqual(result, 'First paragraph.\nSecond paragraph.')

    def test_decodes_plain_text(self):
        self.assertEqual(extract_text(b'hello world', 'text/plain'), 'hello world')

    def test_decodes_markdown(self):
        self.assertEqual(extract_text(b'# Heading', 'text/markdown'), '# Heading')

    def test_non_utf8_bytes_do_not_raise(self):
        result = extract_text(b'\xff\xfe not valid utf-8', 'text/plain')
        self.assertIsInstance(result, str)

    def test_unsupported_content_type_raises(self):
        with self.assertRaises(ValueError):
            extract_text(b'...', 'application/zip')


class ChunkTextTest(TestCase):
    def test_empty_string_produces_no_chunks(self):
        self.assertEqual(chunk_text(''), [])

    def test_text_shorter_than_chunk_size_is_a_single_chunk(self):
        self.assertEqual(chunk_text('short text', chunk_size=1000, overlap=150), ['short text'])

    def test_long_text_produces_overlapping_chunks(self):
        text = 'a' * 2500
        chunks = chunk_text(text, chunk_size=1000, overlap=150)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(len(chunks[0]), 1000)

    def test_whitespace_only_text_produces_no_chunks(self):
        self.assertEqual(chunk_text('   \n\t  '), [])


class GenerateThumbnailTest(TestCase):
    @staticmethod
    def _make_png_bytes(size=(800, 600)):
        buf = io.BytesIO()
        Image.new('RGB', size, color='red').save(buf, format='PNG')
        return buf.getvalue()

    def test_output_is_within_thumbnail_bounds(self):
        thumb_bytes = generate_thumbnail(self._make_png_bytes(), 'image/png')
        thumb = Image.open(io.BytesIO(thumb_bytes))
        self.assertLessEqual(thumb.width, 320)
        self.assertLessEqual(thumb.height, 320)

    def test_jpeg_output_is_decodable(self):
        buf = io.BytesIO()
        Image.new('RGB', (800, 600), color='blue').save(buf, format='JPEG')
        thumb_bytes = generate_thumbnail(buf.getvalue(), 'image/jpeg')
        thumb = Image.open(io.BytesIO(thumb_bytes))
        self.assertEqual(thumb.format, 'JPEG')


class ProjectHasSearchableFilesTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='fileowner', password='pass')
        self.project = Project.objects.create(user=self.user, name='Research')

    def test_false_when_no_chunks_exist(self):
        self.assertFalse(project_has_searchable_files(self.project.id))

    def test_true_when_at_least_one_chunk_exists(self):
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='a.txt', content_type='text/plain', size_bytes=1,
            storage_key='project_files/a.txt', status='ready',
        )
        ProjectFileChunk.objects.create(file=file_obj, project=self.project, chunk_index=0, content='x', embedding=[0.0] * 1024)
        self.assertTrue(project_has_searchable_files(self.project.id))


class SearchProjectFilesTest(TestCase):
    # Mirrors librarian's test_retrieve_relevant_memories_orders_by_similarity.
    def setUp(self):
        self.user = User.objects.create_user(username='fileowner', password='pass')
        self.project = Project.objects.create(user=self.user, name='Research')
        self.file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='doc.txt', content_type='text/plain', size_bytes=1,
            storage_key='project_files/doc.txt', status='ready',
        )

    @staticmethod
    def _mock_response(vector):
        return MagicMock(data=[MagicMock(embedding=vector)])

    @patch('core.embeddings.Mistral')
    def test_orders_by_similarity_closest_first(self, mock_mistral_cls):
        from core.embeddings import get_embed_client
        get_embed_client.cache_clear()
        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = [
            self._mock_response([1.0] + [0.0] * 1023),   # "close match"
            self._mock_response([0.0] * 1023 + [1.0]),   # "far match" (orthogonal)
            self._mock_response([1.0] + [0.0] * 1023),   # the query itself
        ]
        mock_mistral_cls.return_value = mock_client

        ProjectFileChunk.objects.create(
            file=self.file_obj, project=self.project, chunk_index=0, content='close match',
            embedding=[1.0] + [0.0] * 1023,
        )
        ProjectFileChunk.objects.create(
            file=self.file_obj, project=self.project, chunk_index=1, content='far match',
            embedding=[0.0] * 1023 + [1.0],
        )

        results = search_project_files(self.project.id, 'query', top_k=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].content, 'close match')
        self.assertEqual(results[0].filename, 'doc.txt')

    def test_ignores_chunks_from_non_ready_files(self):
        other_file = ProjectFile.objects.create(
            project=self.project, original_filename='pending.txt', content_type='text/plain', size_bytes=1,
            storage_key='project_files/pending.txt', status='pending',
        )
        ProjectFileChunk.objects.create(
            file=other_file, project=self.project, chunk_index=0, content='not ready yet', embedding=[0.0] * 1024,
        )
        with patch('project_files.services.embed', return_value=[0.0] * 1024):
            results = search_project_files(self.project.id, 'query')
        self.assertEqual(results, [])
