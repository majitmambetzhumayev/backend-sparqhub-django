import shutil
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from image_providers.services import save_generated_image

_TEST_MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT, MEDIA_URL='media/', BACKEND_URL='http://localhost:8000')
class SaveGeneratedImageTest(SimpleTestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TEST_MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def test_returns_absolute_url_under_media(self):
        url = save_generated_image(b'fake-bytes', 'image/png')
        self.assertTrue(url.startswith('http://localhost:8000/media/generated_images/'))
        self.assertTrue(url.endswith('.png'))

    def test_picks_extension_from_mime_type(self):
        url = save_generated_image(b'fake-bytes', 'image/jpeg')
        self.assertTrue(url.endswith('.jpeg'))

    def test_defaults_to_png_for_unknown_mime_type(self):
        url = save_generated_image(b'fake-bytes', 'application/octet-stream')
        self.assertTrue(url.endswith('.png'))

    def test_file_is_actually_written_with_given_bytes(self):
        from django.core.files.storage import default_storage

        url = save_generated_image(b'exact-content', 'image/png')
        relative_path = url.split('/media/', 1)[1]
        with default_storage.open(relative_path) as f:
            self.assertEqual(f.read(), b'exact-content')

    def test_sets_content_type_explicitly_instead_of_leaving_it_to_be_guessed(self):
        # Regression test: mimetypes.guess_type(filename) — django-storages'
        # fallback when a ContentFile has no .content_type of its own — can
        # resolve to None for some extensions, which S3Storage then serves
        # as "application/octet-stream": a type browsers are willing to
        # content-sniff (including as text/html) on an origin with no
        # nosniff header. content_type must always be set explicitly.
        with patch('image_providers.services.default_storage.save') as mock_save:
            mock_save.return_value = 'generated_images/x.webp'
            save_generated_image(b'fake-bytes', 'image/webp')
        saved_content = mock_save.call_args.args[1]
        self.assertEqual(saved_content.content_type, 'image/webp')

    def test_returns_remote_storage_url_unprefixed(self):
        # Regression test: a remote backend (R2/S3, once R2_* env vars are
        # set) already returns a fully-qualified URL via .url() — it must be
        # returned as-is, not prefixed with BACKEND_URL a second time.
        remote_url = 'https://pub-example.r2.dev/generated_images/x.png'
        with patch('image_providers.services.default_storage.url', return_value=remote_url):
            url = save_generated_image(b'fake-bytes', 'image/png')
        self.assertEqual(url, remote_url)
