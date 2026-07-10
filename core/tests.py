from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from core.embeddings import embed, embed_batch, get_embed_client
from core.services import send_email


class SendEmailTest(SimpleTestCase):
    @patch('core.services.resend.Emails.send')
    def test_sends_via_resend_with_configured_from_address(self, mock_send):
        send_email("someone@example.com", "Subject line", "<p>Body</p>")

        mock_send.assert_called_once_with({
            "from": "noreply@sparqup.fr",
            "to": "someone@example.com",
            "subject": "Subject line",
            "html": "<p>Body</p>",
        })


class EmbeddingsTest(SimpleTestCase):
    # Mocks the Mistral SDK client, not our own code — per this repo's
    # provider-testing convention.
    def setUp(self):
        get_embed_client.cache_clear()

    @staticmethod
    def _mock_response(vectors):
        return MagicMock(data=[MagicMock(embedding=v) for v in vectors])

    @patch('core.embeddings.Mistral')
    def test_embed_calls_mistral_embeddings_api_with_a_single_input(self, mock_mistral_cls):
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = self._mock_response([[0.1] * 1024])
        mock_mistral_cls.return_value = mock_client

        result = embed("hello")

        mock_client.embeddings.create.assert_called_once_with(model='mistral-embed', inputs=['hello'])
        self.assertEqual(len(result), 1024)

    @patch('core.embeddings.Mistral')
    def test_embed_batch_makes_one_call_for_many_texts(self, mock_mistral_cls):
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = self._mock_response([[0.1] * 1024, [0.2] * 1024])
        mock_mistral_cls.return_value = mock_client

        result = embed_batch(["chunk one", "chunk two"])

        mock_client.embeddings.create.assert_called_once_with(model='mistral-embed', inputs=["chunk one", "chunk two"])
        self.assertEqual(len(result), 2)

    @patch('core.embeddings.Mistral')
    def test_embed_batch_of_empty_list_makes_no_api_call(self, mock_mistral_cls):
        mock_client = MagicMock()
        mock_mistral_cls.return_value = mock_client

        result = embed_batch([])

        mock_client.embeddings.create.assert_not_called()
        self.assertEqual(result, [])


class PermissionsPolicyMiddlewareTest(TestCase):
    def test_adds_permissions_policy_header(self):
        response = self.client.get('/api/healthcheck/')
        self.assertIn('camera=()', response['Permissions-Policy'])
        self.assertIn('microphone=()', response['Permissions-Policy'])
