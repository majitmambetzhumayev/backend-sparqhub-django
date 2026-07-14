import asyncio
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase

from mcp_client.services import _extract_result_text, _open_session, _to_tool_schema, is_safe_sse_url


def run(coro):
    return asyncio.run(coro)


class ToToolSchemaTest(SimpleTestCase):
    def test_converts_mcp_tool_fields(self):
        mock_tool = MagicMock()
        mock_tool.name = "list_assistants"
        mock_tool.description = "List active assistants"
        mock_tool.inputSchema = {
            "type": "object",
            "properties": {"user_id": {"type": "integer"}},
            "required": ["user_id"],
        }
        result = _to_tool_schema(mock_tool)
        self.assertEqual(result["name"], "list_assistants")
        self.assertEqual(result["description"], "List active assistants")
        self.assertEqual(result["input_schema"], mock_tool.inputSchema)

    def test_handles_none_description(self):
        mock_tool = MagicMock()
        mock_tool.name = "tool"
        mock_tool.description = None
        mock_tool.inputSchema = {"type": "object"}
        result = _to_tool_schema(mock_tool)
        self.assertEqual(result["description"], "")


class ExtractResultTextTest(SimpleTestCase):
    def test_extracts_text_from_single_content_item(self):
        item = MagicMock()
        item.text = "tool output"
        result = MagicMock()
        result.content = [item]
        result.isError = False
        self.assertEqual(_extract_result_text(result), "tool output")

    def test_joins_multiple_text_items(self):
        item1 = MagicMock()
        item1.text = "line one"
        item2 = MagicMock()
        item2.text = "line two"
        result = MagicMock()
        result.content = [item1, item2]
        result.isError = False
        self.assertEqual(_extract_result_text(result), "line one\nline two")

    def test_returns_empty_string_when_no_content(self):
        result = MagicMock()
        result.content = []
        result.isError = False
        self.assertEqual(_extract_result_text(result), "")

    def test_skips_items_without_text_attribute(self):
        image_item = MagicMock(spec=[])
        text_item = MagicMock()
        text_item.text = "text only"
        result = MagicMock()
        result.content = [image_item, text_item]
        result.isError = False
        self.assertEqual(_extract_result_text(result), "text only")

    def test_prefixes_error_when_is_error_true(self):
        # Regression test: CallToolResult.isError was never checked — a
        # failed remote tool call looked exactly like a normal result to
        # the model.
        item = MagicMock()
        item.text = "permission denied"
        result = MagicMock()
        result.content = [item]
        result.isError = True
        self.assertEqual(_extract_result_text(result), "Error: permission denied")

    def test_reports_error_even_with_no_content(self):
        result = MagicMock()
        result.content = []
        result.isError = True
        self.assertEqual(_extract_result_text(result), "Error: the tool call failed with no further detail.")


class IsSafeSseUrlTest(SimpleTestCase):
    @patch('mcp_client.services.socket.getaddrinfo', return_value=[(None, None, None, None, ('93.184.216.34', 0))])
    def test_public_ip_is_safe(self, mock_getaddrinfo):
        self.assertTrue(is_safe_sse_url('https://example.com/mcp'))

    @patch('mcp_client.services.socket.getaddrinfo', return_value=[(None, None, None, None, ('169.254.169.254', 0))])
    def test_cloud_metadata_ip_is_unsafe(self, mock_getaddrinfo):
        self.assertFalse(is_safe_sse_url('http://example.com/mcp'))

    def test_non_http_scheme_is_unsafe(self):
        self.assertFalse(is_safe_sse_url('file:///etc/passwd'))


class OpenSessionSseRebindingTest(SimpleTestCase):
    @patch('mcp_client.services.sse_client')
    @patch('mcp_client.services.is_safe_sse_url', return_value=False)
    def test_refuses_to_connect_when_url_no_longer_safe(self, mock_is_safe, mock_sse_client):
        # Regression test for a DNS-rebinding SSRF: is_safe_sse_url used to
        # be checked ONLY at server-registration time — a hostname that
        # resolved to a public IP when saved could later be repointed at an
        # internal address (near-zero-TTL DNS), and every chat turn
        # re-resolves the hostname fresh via a brand-new connection with no
        # re-check. This proves the connection itself is now gated on a
        # fresh check, not just the one at save time.
        server = MagicMock(transport='sse', url='https://rebound.example.com/mcp')

        async def scenario():
            async with _open_session(server):
                pass  # pragma: no cover - must never get here, is_safe_sse_url blocks first

        with self.assertRaises(ValueError):
            run(scenario())
        mock_sse_client.assert_not_called()
