from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from mcp_server.server import get_thread_messages, list_assistants, list_threads


def _make_mock_queryset(chained_result):
    """Builds a mock queryset whose final chained call returns chained_result."""
    mock_qs = MagicMock()
    mock_qs.filter.return_value = mock_qs
    mock_qs.select_related.return_value = mock_qs
    return mock_qs, chained_result


class ListAssistantsToolTest(SimpleTestCase):
    @patch('assistants.models.Assistant')
    def test_returns_assistants_for_user(self, MockAssistant):
        row = {'id': 1, 'name': 'TestBot', 'ai_provider': 'anthropic', 'model': 'claude-sonnet-4-6'}
        MockAssistant.objects.filter.return_value.values.return_value = [row]

        result = list_assistants(user_id=42)

        MockAssistant.objects.filter.assert_called_once_with(user_id=42, deleted=False)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['name'], 'TestBot')

    @patch('assistants.models.Assistant')
    def test_returns_empty_list_when_no_assistants(self, MockAssistant):
        MockAssistant.objects.filter.return_value.values.return_value = []
        result = list_assistants(user_id=99)
        self.assertEqual(result, [])


class ListThreadsToolTest(SimpleTestCase):
    def _setup_mock_thread(self, name='TestBot'):
        mock_thread = MagicMock()
        mock_thread.id = 1
        mock_thread.assistant.name = name
        mock_thread.created_at.isoformat.return_value = '2024-01-01T00:00:00+00:00'
        return mock_thread

    @patch('threads.models.Thread')
    def test_returns_threads_for_user(self, MockThread):
        mock_thread = self._setup_mock_thread()
        mock_ordered = MagicMock()
        mock_ordered.__getitem__ = MagicMock(return_value=[mock_thread])
        mock_qs = MagicMock()
        mock_qs.filter.return_value = mock_qs
        mock_qs.select_related.return_value = mock_qs
        mock_qs.order_by.return_value = mock_ordered
        MockThread.objects = mock_qs

        result = list_threads(user_id=1)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['assistant'], 'TestBot')
        self.assertIn('created_at', result[0])

    @patch('threads.models.Thread')
    def test_filters_by_assistant_id_when_provided(self, MockThread):
        mock_ordered = MagicMock()
        mock_ordered.__getitem__ = MagicMock(return_value=[])
        mock_qs = MagicMock()
        mock_qs.filter.return_value = mock_qs
        mock_qs.select_related.return_value = mock_qs
        mock_qs.order_by.return_value = mock_ordered
        MockThread.objects = mock_qs

        list_threads(user_id=1, assistant_id=5)

        self.assertEqual(mock_qs.filter.call_count, 2)
        mock_qs.filter.assert_any_call(assistant_id=5)

    @patch('threads.models.Thread')
    def test_returns_empty_when_no_threads(self, MockThread):
        mock_ordered = MagicMock()
        mock_ordered.__getitem__ = MagicMock(return_value=[])
        mock_qs = MagicMock()
        mock_qs.filter.return_value = mock_qs
        mock_qs.select_related.return_value = mock_qs
        mock_qs.order_by.return_value = mock_ordered
        MockThread.objects = mock_qs

        result = list_threads(user_id=99)
        self.assertEqual(result, [])


class GetThreadMessagesToolTest(SimpleTestCase):
    def _setup_mock_message(self, sender='user', content='Hello'):
        mock_msg = MagicMock()
        mock_msg.sender = sender
        mock_msg.content = content
        mock_msg.timestamp.isoformat.return_value = '2024-01-01T00:00:00+00:00'
        return mock_msg

    @patch('chat_messages.models.Message')
    def test_returns_messages_in_order(self, MockMessage):
        msg1 = self._setup_mock_message('user', 'Hello')
        msg2 = self._setup_mock_message('assistant', 'Hi there!')
        mock_ordered = MagicMock()
        mock_ordered.__getitem__ = MagicMock(return_value=[msg1, msg2])
        MockMessage.objects.filter.return_value.order_by.return_value = mock_ordered

        result = get_thread_messages(thread_id=1)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['sender'], 'user')
        self.assertEqual(result[0]['content'], 'Hello')
        self.assertEqual(result[1]['sender'], 'assistant')

    @patch('chat_messages.models.Message')
    def test_passes_limit_as_slice(self, MockMessage):
        mock_ordered = MagicMock()
        mock_ordered.__getitem__ = MagicMock(return_value=[])
        MockMessage.objects.filter.return_value.order_by.return_value = mock_ordered

        get_thread_messages(thread_id=1, limit=5)

        mock_ordered.__getitem__.assert_called_once()
        call_arg = mock_ordered.__getitem__.call_args[0][0]
        self.assertIsInstance(call_arg, slice)
        self.assertEqual(call_arg.stop, 5)

    @patch('chat_messages.models.Message')
    def test_returns_empty_for_unknown_thread(self, MockMessage):
        mock_ordered = MagicMock()
        mock_ordered.__getitem__ = MagicMock(return_value=[])
        MockMessage.objects.filter.return_value.order_by.return_value = mock_ordered

        result = get_thread_messages(thread_id=99999)
        self.assertEqual(result, [])
