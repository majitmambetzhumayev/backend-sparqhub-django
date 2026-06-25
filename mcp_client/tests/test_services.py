from unittest.mock import MagicMock
from django.test import SimpleTestCase

from mcp_client.services import _extract_result_text, _to_anthropic_tool


class ToAnthropicToolTest(SimpleTestCase):
    def test_converts_mcp_tool_fields(self):
        mock_tool = MagicMock()
        mock_tool.name = "list_assistants"
        mock_tool.description = "List active assistants"
        mock_tool.inputSchema = {
            "type": "object",
            "properties": {"user_id": {"type": "integer"}},
            "required": ["user_id"],
        }
        result = _to_anthropic_tool(mock_tool)
        self.assertEqual(result["name"], "list_assistants")
        self.assertEqual(result["description"], "List active assistants")
        self.assertEqual(result["input_schema"], mock_tool.inputSchema)

    def test_handles_none_description(self):
        mock_tool = MagicMock()
        mock_tool.name = "tool"
        mock_tool.description = None
        mock_tool.inputSchema = {"type": "object"}
        result = _to_anthropic_tool(mock_tool)
        self.assertEqual(result["description"], "")


class ExtractResultTextTest(SimpleTestCase):
    def test_extracts_text_from_single_content_item(self):
        item = MagicMock()
        item.text = "tool output"
        result = MagicMock()
        result.content = [item]
        self.assertEqual(_extract_result_text(result), "tool output")

    def test_joins_multiple_text_items(self):
        item1 = MagicMock()
        item1.text = "line one"
        item2 = MagicMock()
        item2.text = "line two"
        result = MagicMock()
        result.content = [item1, item2]
        self.assertEqual(_extract_result_text(result), "line one\nline two")

    def test_returns_empty_string_when_no_content(self):
        result = MagicMock()
        result.content = []
        self.assertEqual(_extract_result_text(result), "")

    def test_skips_items_without_text_attribute(self):
        image_item = MagicMock(spec=[])
        text_item = MagicMock()
        text_item.text = "text only"
        result = MagicMock()
        result.content = [image_item, text_item]
        self.assertEqual(_extract_result_text(result), "text only")
