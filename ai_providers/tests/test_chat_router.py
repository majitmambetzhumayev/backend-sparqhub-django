import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase

from ai_providers.anthropic.anthropic_provider import AnthropicProvider
from ai_providers.base import UsageAccumulator
from ai_providers.factory import PROVIDERS
from ai_providers.chat_router import (
    AgentTool,
    InsufficientCreditsError,
    _build_combined_executor,
    _build_delegate_tool,
    _build_file_search_tool,
    _build_image_tool,
    _compute_cost_credits,
    _get_mcp_context,
    deduct_credits,
    send_chat_message,
)
from assistants.models import Assistant
from projects.models import Project
from mcp_client.models import MCPServer
from image_providers.base import ImageResult
from image_providers.openai_image.provider import OpenAIImageProvider
from project_files.models import ProjectFile, ProjectFileChunk

User = get_user_model()


def run(coro):
    return asyncio.run(coro)


def _mock_provider(**kwargs) -> MagicMock:
    """A MagicMock provider with an awaitable aclose() — send_chat_message
    always calls it, and a plain MagicMock's aclose() isn't awaitable.
    label defaults to a real string since chat_router.py reads it (via
    .lower()) for the gen_ai.provider.name span attribute, which OTel
    validates as a str/int/float/bool -- override via kwargs if a test
    needs a specific provider name."""
    kwargs.setdefault('label', 'Anthropic')
    provider = MagicMock(**kwargs)
    provider.aclose = AsyncMock()
    return provider


class ComputeCostCreditsTest(TransactionTestCase):
    def test_zero_when_no_usage(self):
        self.assertEqual(_compute_cost_credits(AnthropicProvider, 'claude-sonnet-5', None), 0)

    def test_zero_when_model_has_no_pricing_entry(self):
        usage = UsageAccumulator(input_tokens=1000, output_tokens=1000)
        self.assertEqual(_compute_cost_credits(AnthropicProvider, 'not-a-real-model', usage), 0)

    def test_rounds_up_and_charges_at_least_one_credit(self):
        # A handful of tokens costs a fraction of a cent — still charged the minimum 1 credit.
        usage = UsageAccumulator(input_tokens=10, output_tokens=10)
        self.assertEqual(_compute_cost_credits(AnthropicProvider, 'claude-sonnet-5', usage), 1)

    def test_computes_real_cost_for_larger_usage(self):
        # claude-sonnet-5: $3/$15 per 1M tokens. 1M input + 1M output = $18 = 1800 credits at $0.01/credit.
        usage = UsageAccumulator(input_tokens=1_000_000, output_tokens=1_000_000)
        self.assertEqual(_compute_cost_credits(AnthropicProvider, 'claude-sonnet-5', usage), 1800)


class DeductCreditsTest(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='credituser', password='pass', credits_remaining=100)

    def test_decrements_balance_by_computed_cost(self):
        usage = UsageAccumulator(input_tokens=1_000_000, output_tokens=1_000_000)  # 1800 credits on Sonnet 5
        run(deduct_credits(self.user, 'anthropic', 'claude-sonnet-5', usage))
        self.user.refresh_from_db()
        self.assertEqual(self.user.credits_remaining, 100 - 1800)

    def test_noop_when_usage_is_none(self):
        run(deduct_credits(self.user, 'anthropic', 'claude-sonnet-5', None))
        self.user.refresh_from_db()
        self.assertEqual(self.user.credits_remaining, 100)

    def test_includes_extra_credits_from_usage(self):
        # extra_credits carries costs from tools priced outside the main
        # provider/model (e.g. image generation) so they're deducted in the
        # same atomic call as the turn's token usage.
        usage = UsageAccumulator(input_tokens=0, output_tokens=0, extra_credits=50)
        run(deduct_credits(self.user, 'anthropic', 'claude-sonnet-5', usage))
        self.user.refresh_from_db()
        self.assertEqual(self.user.credits_remaining, 100 - 50)


class SendChatMessageCreditsGateTest(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='gateuser', password='pass', credits_remaining=0)
        self.assistant = Assistant.objects.create(
            user=self.user, name='A', instructions='Be helpful.', ai_provider='anthropic',
        )

    @patch('keys.services.get_user_api_key', new_callable=AsyncMock, return_value=None)
    def test_blocks_when_credits_exhausted_and_no_personal_key(self, mock_get_key):
        with self.assertRaises(InsufficientCreditsError):
            run(send_chat_message(
                self.assistant, 'Hello', ai_provider='anthropic', model='claude-sonnet-5', user=self.user,
            ))

    @patch('ai_providers.chat_router.get_provider')
    @patch('ai_providers.chat_router.run_agent_loop', new_callable=AsyncMock, return_value='Hi there!')
    @patch('keys.services.get_user_api_key', new_callable=AsyncMock)
    def test_does_not_block_when_personal_key_present(self, mock_get_key, mock_run_loop, mock_get_provider):
        mock_get_key.return_value = MagicMock(encrypted_key='sk-personal')
        mock_get_provider.return_value = _mock_provider()

        result, usage, used_global_key = run(send_chat_message(
            self.assistant, 'Hello', ai_provider='anthropic', model='claude-sonnet-5', user=self.user,
        ))

        self.assertEqual(result, 'Hi there!')
        self.assertFalse(used_global_key)

    @patch('ai_providers.chat_router.get_provider')
    @patch('ai_providers.chat_router.run_agent_loop', new_callable=AsyncMock, return_value='Hi there!')
    @patch('keys.services.get_user_api_key', new_callable=AsyncMock, return_value=None)
    def test_blocks_once_credits_run_out_mid_connection(self, mock_get_key, mock_run_loop, mock_get_provider):
        # Regression test: on a long-lived WS connection, `user` is the same
        # in-memory object across every message (resolved once at connect
        # time). The gate must re-check the DB, not a stale in-memory
        # attribute, or a user who starts with credits never gets blocked
        # after they're exhausted mid-connection.
        user = User.objects.create_user(username='longlived', password='pass', credits_remaining=1)
        assistant = Assistant.objects.create(
            user=user, name='A', instructions='Be helpful.', ai_provider='anthropic',
        )
        mock_get_provider.return_value = _mock_provider()

        run(send_chat_message(assistant, 'Hello', ai_provider='anthropic', model='claude-sonnet-5', user=user))

        # Credits are exhausted directly in the DB (simulating a deduction
        # from an earlier message on this same connection) without touching
        # the in-memory `user` object's cached attribute.
        User.objects.filter(pk=user.pk).update(credits_remaining=0)

        with self.assertRaises(InsufficientCreditsError):
            run(send_chat_message(assistant, 'Hello again', ai_provider='anthropic', model='claude-sonnet-5', user=user))


class GetMcpContextTest(TransactionTestCase):
    """Confirmation is no longer tested here — _get_mcp_context only builds
    AgentTool entries with the right requires_confirmation flag; the gate
    itself is applied uniformly by _build_combined_executor (see
    BuildCombinedExecutorTest)."""

    def setUp(self):
        self.user = User.objects.create_user(username='mcpctxuser', password='pass')
        self.project = Project.objects.create(user=self.user, name='Research')

    def test_returns_empty_when_no_project(self):
        self.assertEqual(run(_get_mcp_context(None)), {})

    def test_returns_empty_when_project_has_no_servers(self):
        self.assertEqual(run(_get_mcp_context(self.project.id)), {})

    def test_ignores_disabled_servers(self):
        MCPServer.objects.create(
            project=self.project, name='Disabled', transport='stdio', command='python', enabled=False,
        )
        self.assertEqual(run(_get_mcp_context(self.project.id)), {})

    def test_ignores_servers_from_other_projects(self):
        other_project = Project.objects.create(user=self.user, name='Other')
        MCPServer.objects.create(project=other_project, name='NotMine', transport='stdio', command='python')
        self.assertEqual(run(_get_mcp_context(self.project.id)), {})

    @patch('mcp_client.services.get_tools_from_server', new_callable=AsyncMock)
    @patch('mcp_client.services.call_tool', new_callable=AsyncMock, return_value='tool output')
    def test_collects_tools_from_enabled_servers_and_executes(self, mock_call_tool, mock_get_tools):
        MCPServer.objects.create(
            project=self.project, name='Mine', transport='stdio', command='python', requires_confirmation=False,
        )
        mock_get_tools.return_value = [{'name': 'search', 'description': '', 'input_schema': {}}]

        registry = run(_get_mcp_context(self.project.id))

        self.assertEqual(list(registry), ['search'])
        self.assertEqual(registry['search'].schema, {'name': 'search', 'description': '', 'input_schema': {}})
        self.assertFalse(registry['search'].requires_confirmation)
        self.assertEqual(run(registry['search'].executor({})), 'tool output')

    @patch('mcp_client.services.get_tools_from_server', new_callable=AsyncMock)
    @patch('mcp_client.services.call_tool', new_callable=AsyncMock, return_value='tool output')
    def test_defaults_to_requiring_confirmation(self, mock_call_tool, mock_get_tools):
        MCPServer.objects.create(project=self.project, name='Mine', transport='stdio', command='python')
        mock_get_tools.return_value = [{'name': 'search', 'description': '', 'input_schema': {}}]

        registry = run(_get_mcp_context(self.project.id))

        self.assertTrue(registry['search'].requires_confirmation)

    @patch('mcp_client.services.get_tools_from_server', new_callable=AsyncMock, side_effect=ValueError('unreachable'))
    def test_survives_unreachable_server(self, mock_get_tools):
        MCPServer.objects.create(project=self.project, name='Down', transport='sse', url='https://example.com/mcp')
        self.assertEqual(run(_get_mcp_context(self.project.id)), {})

    @patch('mcp_client.services.get_tools_from_server')
    @patch('mcp_client.services.call_tool', new_callable=AsyncMock, return_value='from first server')
    def test_tool_name_collision_across_servers_keeps_first_consistently(self, mock_call_tool, mock_get_tools):
        # Regression test: previously the tools list kept BOTH duplicate
        # entries while the dispatch map only kept the last server, so the
        # list and the actual routing disagreed. Now both consistently
        # resolve to the first server that exposed the name.
        first = MCPServer.objects.create(
            project=self.project, name='First', transport='stdio', command='python', requires_confirmation=False,
        )
        second = MCPServer.objects.create(
            project=self.project, name='Second', transport='stdio', command='python', requires_confirmation=False,
        )

        async def fake_get_tools(server):
            return [{'name': 'search', 'description': '', 'input_schema': {}}]

        mock_get_tools.side_effect = fake_get_tools

        registry = run(_get_mcp_context(self.project.id))

        self.assertEqual(list(registry), ['search'])
        self.assertEqual(run(registry['search'].executor({})), 'from first server')
        mock_call_tool.assert_awaited_once_with(first, 'search', {})


class BuildCombinedExecutorTest(TransactionTestCase):
    """_build_combined_executor is the one place any tool call — built-in or
    MCP — gets dispatched and, if flagged, gated on human confirmation. This
    exercises that gate generically, independent of any specific tool."""

    def _tool(self, requires_confirmation=False, label=None, result='ok'):
        executor = AsyncMock(return_value=result)
        return executor, AgentTool(
            schema={'name': 'thing'}, executor=executor,
            requires_confirmation=requires_confirmation, confirmation_label=label,
        )

    def test_dispatches_to_the_matching_tool(self):
        executor, tool = self._tool()
        combined = _build_combined_executor({'thing': tool}, confirm_tool_call=None)

        result = run(combined('thing', {'a': 1}))

        self.assertEqual(result, 'ok')
        executor.assert_awaited_once_with({'a': 1})

    def test_unknown_tool_raises(self):
        combined = _build_combined_executor({}, confirm_tool_call=None)
        with self.assertRaises(ValueError):
            run(combined('thing', {}))

    def test_tool_not_requiring_confirmation_skips_the_gate_entirely(self):
        executor, tool = self._tool(requires_confirmation=False)
        combined = _build_combined_executor({'thing': tool}, confirm_tool_call=None)

        # No confirm_tool_call at all, yet it still runs — the gate only
        # applies when the tool itself opts in.
        result = run(combined('thing', {}))
        self.assertEqual(result, 'ok')

    def test_sensitive_tool_fails_closed_with_no_confirm_callback(self):
        executor, tool = self._tool(requires_confirmation=True)
        combined = _build_combined_executor({'thing': tool}, confirm_tool_call=None)

        result = run(combined('thing', {}))

        self.assertIn('requires interactive user confirmation', result)
        executor.assert_not_awaited()

    def test_sensitive_tool_declined(self):
        executor, tool = self._tool(requires_confirmation=True)
        confirm_tool_call = AsyncMock(return_value=False)
        combined = _build_combined_executor({'thing': tool}, confirm_tool_call)

        result = run(combined('thing', {'q': 'x'}))

        self.assertIn('declined', result)
        confirm_tool_call.assert_awaited_once_with('thing', {'q': 'x'})
        executor.assert_not_awaited()

    def test_sensitive_tool_confirmed(self):
        executor, tool = self._tool(requires_confirmation=True)
        confirm_tool_call = AsyncMock(return_value=True)
        combined = _build_combined_executor({'thing': tool}, confirm_tool_call)

        result = run(combined('thing', {}))

        self.assertEqual(result, 'ok')
        executor.assert_awaited_once()

    def test_uses_custom_confirmation_label(self):
        executor, tool = self._tool(requires_confirmation=True, label='this special thing')
        combined = _build_combined_executor({'thing': tool}, confirm_tool_call=None)

        result = run(combined('thing', {}))

        self.assertIn('this special thing', result)


class BuildFileSearchToolTest(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='filesearchuser', password='pass')
        self.project = Project.objects.create(user=self.user, name='Research')

    def test_returns_none_when_no_project(self):
        self.assertIsNone(run(_build_file_search_tool(None)))

    def test_returns_none_when_project_has_no_searchable_chunks(self):
        self.assertIsNone(run(_build_file_search_tool(self.project.id)))

    def test_returns_tool_and_working_executor_when_chunks_exist(self):
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='notes.txt', content_type='text/plain', size_bytes=1,
            storage_key='project_files/notes.txt', status='ready',
        )
        ProjectFileChunk.objects.create(
            file=file_obj, project=self.project, chunk_index=0, content='relevant passage', embedding=[0.0] * 1024,
        )

        tool = run(_build_file_search_tool(self.project.id))

        self.assertEqual(tool.schema['name'], 'search_project_files')
        self.assertFalse(tool.requires_confirmation)
        with patch('project_files.services.embed', return_value=[0.0] * 1024):
            result = run(tool.executor({'query': 'anything'}))
        self.assertIn('relevant passage', result)
        self.assertIn('notes.txt', result)

    def test_executor_reports_no_results_found(self):
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='notes.txt', content_type='text/plain', size_bytes=1,
            storage_key='project_files/notes.txt', status='ready',
        )
        ProjectFileChunk.objects.create(
            file=file_obj, project=self.project, chunk_index=0, content='relevant passage', embedding=[0.0] * 1024,
        )
        with patch('project_files.services.search_project_files', return_value=[]):
            tool = run(_build_file_search_tool(self.project.id))
            result = run(tool.executor({'query': 'anything'}))
        self.assertIn('No relevant content', result)


class BuildImageToolTest(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='imageuser', password='pass', credits_remaining=100)

    def test_returns_none_for_provider_without_image_support(self):
        self.assertIsNone(_build_image_tool('anthropic', None, self.user, True, UsageAccumulator()))

    @patch('image_providers.services.save_generated_image', return_value='http://localhost:8000/media/generated_images/x.png')
    @patch('image_providers.factory.get_image_provider')
    def test_returns_tool_schema_and_markdown_result(self, mock_get_provider, mock_save):
        provider = OpenAIImageProvider(api_key='test')
        provider.generate = AsyncMock(return_value=ImageResult(
            data=b'x', mime_type='image/png', usage={'input_tokens': 0, 'output_tokens': 0},
        ))
        mock_get_provider.return_value = provider

        tool = _build_image_tool('openai', None, self.user, False, UsageAccumulator())

        self.assertEqual(tool.schema['name'], 'generate_image')
        self.assertFalse(tool.requires_confirmation)
        result = run(tool.executor({'prompt': 'a cat'}))
        self.assertEqual(result, '![Generated image](http://localhost:8000/media/generated_images/x.png)')
        provider.generate.assert_called_once_with('a cat')

    @patch('image_providers.services.save_generated_image', return_value='http://localhost:8000/media/generated_images/x.png')
    @patch('image_providers.factory.get_image_provider')
    def test_accumulates_extra_credits_when_global_key_used_without_deducting_immediately(self, mock_get_provider, mock_save):
        # Regression test: image cost must NOT be deducted eagerly inside the
        # tool — it should only accumulate onto usage.extra_credits, so the
        # caller can defer the actual deduction until the whole turn succeeds
        # (a subsequent failed model call must not have already charged the user).
        provider = OpenAIImageProvider(api_key='test')
        provider.generate = AsyncMock(return_value=ImageResult(
            data=b'x', mime_type='image/png', usage={'input_tokens': 1_000_000, 'output_tokens': 1_000_000},
        ))
        mock_get_provider.return_value = provider

        usage = UsageAccumulator()
        tool = _build_image_tool('openai', None, self.user, True, usage)
        run(tool.executor({'prompt': 'a cat'}))

        self.user.refresh_from_db()
        # gpt-image-2: $8/$30 per 1M tokens. 1M input + 1M output = $38 = 3800 credits at $0.01/credit.
        self.assertEqual(usage.extra_credits, 3800)
        self.assertEqual(self.user.credits_remaining, 100)

    @patch('image_providers.services.save_generated_image', return_value='http://localhost:8000/media/generated_images/x.png')
    @patch('image_providers.factory.get_image_provider')
    def test_does_not_accumulate_credits_when_personal_key_used(self, mock_get_provider, mock_save):
        provider = OpenAIImageProvider(api_key='test')
        provider.generate = AsyncMock(return_value=ImageResult(
            data=b'x', mime_type='image/png', usage={'input_tokens': 1_000_000, 'output_tokens': 1_000_000},
        ))
        mock_get_provider.return_value = provider

        usage = UsageAccumulator()
        tool = _build_image_tool('openai', 'sk-personal', self.user, False, usage)
        run(tool.executor({'prompt': 'a cat'}))

        self.assertEqual(usage.extra_credits, 0)
        self.user.refresh_from_db()
        self.assertEqual(self.user.credits_remaining, 100)


class SendChatMessageImageToolTest(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='imgtooluser', password='pass', credits_remaining=100)

    @patch('image_providers.factory.get_image_provider')
    @patch('ai_providers.chat_router.get_provider')
    @patch('ai_providers.chat_router.run_agent_loop', new_callable=AsyncMock, return_value='Hi there!')
    @patch('keys.services.get_user_api_key', new_callable=AsyncMock, return_value=None)
    def test_includes_image_tool_for_supported_provider(
        self, mock_get_key, mock_run_loop, mock_get_provider, mock_get_image_provider,
    ):
        # get_image_provider is mocked directly rather than relying on a real
        # OpenAIImageProvider construction — the real OpenAI SDK validates
        # credentials eagerly at construction time, so this would otherwise
        # only pass in environments that happen to have a real
        # OPENAI_API_KEY set (as this dev machine's .env does, unlike CI).
        assistant = Assistant.objects.create(
            user=self.user, name='A', instructions='Be helpful.', ai_provider='openai',
        )
        mock_get_provider.return_value = _mock_provider()
        mock_get_image_provider.return_value = MagicMock()

        run(send_chat_message(assistant, 'Hello', ai_provider='openai', model='gpt-5.4', user=self.user))

        tools_arg = mock_run_loop.call_args.args[4]
        self.assertIn('generate_image', [t['name'] for t in tools_arg])

    @patch('ai_providers.chat_router.get_provider')
    @patch('ai_providers.chat_router.run_agent_loop', new_callable=AsyncMock, return_value='Hi there!')
    @patch('keys.services.get_user_api_key', new_callable=AsyncMock, return_value=None)
    def test_omits_image_tool_for_unsupported_provider(self, mock_get_key, mock_run_loop, mock_get_provider):
        assistant = Assistant.objects.create(
            user=self.user, name='A', instructions='Be helpful.', ai_provider='anthropic',
        )
        mock_get_provider.return_value = _mock_provider()

        run(send_chat_message(assistant, 'Hello', ai_provider='anthropic', model='claude-sonnet-5', user=self.user))

        # No image tool for a provider without image support, but delegate_to_model
        # is always offered regardless of provider.
        tool_names = [t['name'] for t in mock_run_loop.call_args.args[4]]
        self.assertNotIn('generate_image', tool_names)
        self.assertIn('delegate_to_model', tool_names)


class SendChatMessageFileSearchToolTest(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='filesearchtooluser', password='pass', credits_remaining=100)
        self.project = Project.objects.create(user=self.user, name='Research')

    @patch('ai_providers.chat_router.get_provider')
    @patch('ai_providers.chat_router.run_agent_loop', new_callable=AsyncMock, return_value='Hi there!')
    @patch('keys.services.get_user_api_key', new_callable=AsyncMock, return_value=None)
    def test_includes_file_search_tool_when_project_has_searchable_files(
        self, mock_get_key, mock_run_loop, mock_get_provider,
    ):
        assistant = Assistant.objects.create(
            user=self.user, name='A', instructions='Be helpful.', ai_provider='anthropic',
        )
        file_obj = ProjectFile.objects.create(
            project=self.project, original_filename='notes.txt', content_type='text/plain', size_bytes=1,
            storage_key='project_files/notes.txt', status='ready',
        )
        ProjectFileChunk.objects.create(
            file=file_obj, project=self.project, chunk_index=0, content='hello', embedding=[0.0] * 1024,
        )
        mock_get_provider.return_value = _mock_provider()

        run(send_chat_message(
            assistant, 'What does my file say?', ai_provider='anthropic', model='claude-sonnet-5',
            user=self.user, project_id=self.project.id,
        ))

        tool_names = [t['name'] for t in mock_run_loop.call_args.args[4]]
        self.assertIn('search_project_files', tool_names)

    @patch('ai_providers.chat_router.get_provider')
    @patch('ai_providers.chat_router.run_agent_loop', new_callable=AsyncMock, return_value='Hi there!')
    @patch('keys.services.get_user_api_key', new_callable=AsyncMock, return_value=None)
    def test_omits_file_search_tool_when_project_has_no_searchable_files(
        self, mock_get_key, mock_run_loop, mock_get_provider,
    ):
        assistant = Assistant.objects.create(
            user=self.user, name='A', instructions='Be helpful.', ai_provider='anthropic',
        )
        mock_get_provider.return_value = _mock_provider()

        run(send_chat_message(
            assistant, 'Hello', ai_provider='anthropic', model='claude-sonnet-5',
            user=self.user, project_id=self.project.id,
        ))

        tool_names = [t['name'] for t in mock_run_loop.call_args.args[4]]
        self.assertNotIn('search_project_files', tool_names)

    @patch('ai_providers.chat_router.get_provider')
    @patch('ai_providers.chat_router.run_agent_loop', new_callable=AsyncMock, return_value='Hi there!')
    @patch('keys.services.get_user_api_key', new_callable=AsyncMock, return_value=None)
    def test_omits_file_search_tool_when_no_project(self, mock_get_key, mock_run_loop, mock_get_provider):
        assistant = Assistant.objects.create(
            user=self.user, name='A', instructions='Be helpful.', ai_provider='anthropic',
        )
        mock_get_provider.return_value = _mock_provider()

        run(send_chat_message(assistant, 'Hello', ai_provider='anthropic', model='claude-sonnet-5', user=self.user))

        tool_names = [t['name'] for t in mock_run_loop.call_args.args[4]]
        self.assertNotIn('search_project_files', tool_names)


class BuildDelegateToolTest(TransactionTestCase):
    """Confirmation is no longer part of the delegate tool's own executor —
    it's applied uniformly by _build_combined_executor (see
    BuildCombinedExecutorTest), based on requires_confirmation/
    confirmation_label below. Every test here calls .executor directly,
    i.e. as if already confirmed."""

    def setUp(self):
        self.user = User.objects.create_user(username='delegateuser', password='pass', credits_remaining=100)

    def test_requires_confirmation_with_a_natural_label(self):
        tool = _build_delegate_tool(self.user)
        self.assertTrue(tool.requires_confirmation)
        self.assertEqual(tool.confirmation_label, 'this delegation')

    @patch('ai_providers.chat_router.send_chat_message', new_callable=AsyncMock)
    def test_dispatches_to_the_target_provider(self, mock_send):
        mock_send.return_value = ('Here is your image: ![x](http://x)', UsageAccumulator(), False)
        tool = _build_delegate_tool(self.user)

        result = run(tool.executor({
            'provider': 'gemini', 'model': 'gemini-2.5-flash', 'prompt': 'a cat', 'reason': 'no image support',
        }))

        self.assertIn('Here is your image', result)
        self.assertIn('gemini/gemini-2.5-flash', result)
        call_kwargs = mock_send.call_args.kwargs
        self.assertEqual(call_kwargs['ai_provider'], 'gemini')
        self.assertEqual(call_kwargs['model'], 'gemini-2.5-flash')
        self.assertFalse(call_kwargs['allow_delegation'])

    @patch('ai_providers.chat_router.send_chat_message', new_callable=AsyncMock)
    def test_falls_back_to_default_model_when_requested_model_is_invalid(self, mock_send):
        # The calling model has no visibility into which model ids are valid chat
        # models for the target provider — it may guess an image-generation model
        # id (as actually happened: "gpt-image-1", which chat completions rejects).
        # That must not be trusted as-is; it should fall back to a real default.
        mock_send.return_value = ('OK', UsageAccumulator(), False)
        tool = _build_delegate_tool(self.user)

        run(tool.executor({'provider': 'openai', 'model': 'gpt-image-1', 'prompt': 'a cat', 'reason': 'x'}))

        call_kwargs = mock_send.call_args.kwargs
        self.assertNotEqual(call_kwargs['model'], 'gpt-image-1')
        self.assertIn(call_kwargs['model'], [m['id'] for m in PROVIDERS['openai'].AVAILABLE_MODELS])

    @patch('ai_providers.chat_router.send_chat_message', new_callable=AsyncMock)
    def test_uses_default_model_when_none_requested(self, mock_send):
        mock_send.return_value = ('OK', UsageAccumulator(), False)
        tool = _build_delegate_tool(self.user)

        run(tool.executor({'provider': 'openai', 'prompt': 'a cat', 'reason': 'x'}))

        call_kwargs = mock_send.call_args.kwargs
        self.assertEqual(call_kwargs['model'], PROVIDERS['openai'].AVAILABLE_MODELS[0]['id'])

    def test_unknown_provider_returns_error_text_without_dispatching(self):
        tool = _build_delegate_tool(self.user)

        result = run(tool.executor({'provider': 'not-a-provider', 'model': 'x', 'prompt': 'a cat', 'reason': 'x'}))

        self.assertIn('Unknown provider', result)

    @patch('ai_providers.chat_router.send_chat_message', new_callable=AsyncMock)
    def test_deducts_credits_when_delegated_call_used_global_key(self, mock_send):
        usage = UsageAccumulator(input_tokens=1_000_000, output_tokens=1_000_000)
        mock_send.return_value = ('OK', usage, True)
        tool = _build_delegate_tool(self.user)

        run(tool.executor({'provider': 'anthropic', 'model': 'claude-sonnet-5', 'prompt': 'hi', 'reason': 'x'}))

        self.user.refresh_from_db()
        # claude-sonnet-5: $3/$15 per 1M tokens = $18 = 1800 credits.
        self.assertEqual(self.user.credits_remaining, 100 - 1800)

    @patch('ai_providers.chat_router.send_chat_message', new_callable=AsyncMock)
    def test_does_not_deduct_credits_when_delegated_call_used_personal_key(self, mock_send):
        usage = UsageAccumulator(input_tokens=1_000_000, output_tokens=1_000_000)
        mock_send.return_value = ('OK', usage, False)
        tool = _build_delegate_tool(self.user)

        run(tool.executor({'provider': 'anthropic', 'model': 'claude-sonnet-5', 'prompt': 'hi', 'reason': 'x'}))

        self.user.refresh_from_db()
        self.assertEqual(self.user.credits_remaining, 100)


class SendChatMessageDelegateToolTest(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='delegatetooluser', password='pass', credits_remaining=100)

    @patch('ai_providers.chat_router.get_provider')
    @patch('ai_providers.chat_router.run_agent_loop', new_callable=AsyncMock, return_value='Hi there!')
    @patch('keys.services.get_user_api_key', new_callable=AsyncMock, return_value=None)
    def test_delegate_tool_included_by_default(self, mock_get_key, mock_run_loop, mock_get_provider):
        assistant = Assistant.objects.create(
            user=self.user, name='A', instructions='Be helpful.', ai_provider='anthropic',
        )
        mock_get_provider.return_value = _mock_provider()

        run(send_chat_message(assistant, 'Hello', ai_provider='anthropic', model='claude-sonnet-5', user=self.user))

        tool_names = [t['name'] for t in mock_run_loop.call_args.args[4]]
        self.assertIn('delegate_to_model', tool_names)

    @patch('ai_providers.chat_router.get_provider')
    @patch('ai_providers.chat_router.run_agent_loop', new_callable=AsyncMock, return_value='Hi there!')
    @patch('keys.services.get_user_api_key', new_callable=AsyncMock, return_value=None)
    def test_delegate_tool_excluded_when_delegation_disallowed(self, mock_get_key, mock_run_loop, mock_get_provider):
        assistant = Assistant.objects.create(
            user=self.user, name='A', instructions='Be helpful.', ai_provider='anthropic',
        )
        mock_get_provider.return_value = _mock_provider()

        run(send_chat_message(
            assistant, 'Hello', ai_provider='anthropic', model='claude-sonnet-5', user=self.user,
            allow_delegation=False,
        ))

        tool_names = [t['name'] for t in mock_run_loop.call_args.args[4]]
        self.assertNotIn('delegate_to_model', tool_names)


class SendChatMessageProviderCleanupTest(TransactionTestCase):
    # Regression tests: a provider's aclose() must run once send_chat_message
    # is truly done with it — immediately for the non-streaming path, but not
    # until the caller has fully drained the stream for the streaming path
    # (closing it earlier would kill the connection mid-response).
    def setUp(self):
        self.user = User.objects.create_user(username='cleanupuser', password='pass', credits_remaining=100)
        self.assistant = Assistant.objects.create(
            user=self.user, name='A', instructions='Be helpful.', ai_provider='anthropic',
        )

    @patch('ai_providers.chat_router.get_provider')
    @patch('ai_providers.chat_router.run_agent_loop', new_callable=AsyncMock, return_value='Hi there!')
    @patch('keys.services.get_user_api_key', new_callable=AsyncMock, return_value=None)
    def test_closes_provider_after_non_streaming_turn(self, mock_get_key, mock_run_loop, mock_get_provider):
        provider = _mock_provider()
        mock_get_provider.return_value = provider

        run(send_chat_message(
            self.assistant, 'Hello', ai_provider='anthropic', model='claude-sonnet-5', user=self.user,
        ))

        provider.aclose.assert_awaited_once()

    @patch('ai_providers.chat_router.get_provider')
    @patch('keys.services.get_user_api_key', new_callable=AsyncMock, return_value=None)
    def test_defers_closing_provider_until_stream_is_drained(self, mock_get_key, mock_get_provider):
        provider = _mock_provider()

        async def fake_stream(*args, **kwargs):
            yield "Hel"
            yield "lo!"

        provider.stream = fake_stream
        mock_get_provider.return_value = provider

        async def scenario():
            result, _usage, _used_global_key = await send_chat_message(
                self.assistant, 'Hello', ai_provider='anthropic', model='claude-sonnet-5', user=self.user,
                stream=True,
            )
            provider.aclose.assert_not_awaited()

            chunks = [chunk async for chunk in result]

            self.assertEqual(chunks, ["Hel", "lo!"])
            provider.aclose.assert_awaited_once()

        run(scenario())
