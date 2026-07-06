import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase

from ai_providers.anthropic.anthropic_provider import AnthropicProvider
from ai_providers.base import UsageAccumulator
from ai_providers.factory import PROVIDERS
from ai_providers.chat_router import (
    InsufficientCreditsError,
    _build_delegate_tool,
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

User = get_user_model()


def run(coro):
    return asyncio.run(coro)


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
        mock_get_provider.return_value = MagicMock()

        result, usage, used_global_key = run(send_chat_message(
            self.assistant, 'Hello', ai_provider='anthropic', model='claude-sonnet-5', user=self.user,
        ))

        self.assertEqual(result, 'Hi there!')
        self.assertFalse(used_global_key)


class GetMcpContextTest(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='mcpctxuser', password='pass')
        self.project = Project.objects.create(user=self.user, name='Research')

    def test_returns_empty_when_no_project(self):
        tools, tool_executor = run(_get_mcp_context(None))
        self.assertEqual(tools, [])
        self.assertIsNone(tool_executor)

    def test_returns_empty_when_project_has_no_servers(self):
        tools, tool_executor = run(_get_mcp_context(self.project.id))
        self.assertEqual(tools, [])
        self.assertIsNone(tool_executor)

    def test_ignores_disabled_servers(self):
        MCPServer.objects.create(
            project=self.project, name='Disabled', transport='stdio', command='python', enabled=False,
        )
        tools, tool_executor = run(_get_mcp_context(self.project.id))
        self.assertEqual(tools, [])
        self.assertIsNone(tool_executor)

    def test_ignores_servers_from_other_projects(self):
        other_project = Project.objects.create(user=self.user, name='Other')
        MCPServer.objects.create(project=other_project, name='NotMine', transport='stdio', command='python')
        tools, tool_executor = run(_get_mcp_context(self.project.id))
        self.assertEqual(tools, [])
        self.assertIsNone(tool_executor)

    @patch('mcp_client.services.get_tools_from_server', new_callable=AsyncMock)
    @patch('mcp_client.services.call_tool', new_callable=AsyncMock, return_value='tool output')
    def test_collects_tools_from_enabled_servers_and_executes(self, mock_call_tool, mock_get_tools):
        MCPServer.objects.create(project=self.project, name='Mine', transport='stdio', command='python')
        mock_get_tools.return_value = [{'name': 'search', 'description': '', 'input_schema': {}}]

        tools, tool_executor = run(_get_mcp_context(self.project.id))

        self.assertEqual(tools, [{'name': 'search', 'description': '', 'input_schema': {}}])
        self.assertEqual(run(tool_executor('search', {})), 'tool output')

    @patch('mcp_client.services.get_tools_from_server', new_callable=AsyncMock, side_effect=ValueError('unreachable'))
    def test_survives_unreachable_server(self, mock_get_tools):
        MCPServer.objects.create(project=self.project, name='Down', transport='sse', url='https://example.com/mcp')
        tools, tool_executor = run(_get_mcp_context(self.project.id))
        self.assertEqual(tools, [])
        self.assertIsNone(tool_executor)


class BuildImageToolTest(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='imageuser', password='pass', credits_remaining=100)

    def test_returns_none_for_provider_without_image_support(self):
        tool, executor = _build_image_tool('anthropic', None, self.user, True)
        self.assertIsNone(tool)
        self.assertIsNone(executor)

    @patch('image_providers.services.save_generated_image', return_value='http://localhost:8000/media/generated_images/x.png')
    @patch('image_providers.factory.get_image_provider')
    def test_returns_tool_schema_and_markdown_result(self, mock_get_provider, mock_save):
        provider = OpenAIImageProvider(api_key='test')
        provider.generate = AsyncMock(return_value=ImageResult(
            data=b'x', mime_type='image/png', usage={'input_tokens': 0, 'output_tokens': 0},
        ))
        mock_get_provider.return_value = provider

        tool, executor = _build_image_tool('openai', None, self.user, False)

        self.assertEqual(tool['name'], 'generate_image')
        result = run(executor({'prompt': 'a cat'}))
        self.assertEqual(result, '![Generated image](http://localhost:8000/media/generated_images/x.png)')
        provider.generate.assert_called_once_with('a cat')

    @patch('image_providers.services.save_generated_image', return_value='http://localhost:8000/media/generated_images/x.png')
    @patch('image_providers.factory.get_image_provider')
    def test_deducts_credits_when_global_key_used(self, mock_get_provider, mock_save):
        provider = OpenAIImageProvider(api_key='test')
        provider.generate = AsyncMock(return_value=ImageResult(
            data=b'x', mime_type='image/png', usage={'input_tokens': 1_000_000, 'output_tokens': 1_000_000},
        ))
        mock_get_provider.return_value = provider

        _, executor = _build_image_tool('openai', None, self.user, True)
        run(executor({'prompt': 'a cat'}))

        self.user.refresh_from_db()
        # gpt-image-2: $8/$30 per 1M tokens. 1M input + 1M output = $38 = 3800 credits at $0.01/credit.
        self.assertEqual(self.user.credits_remaining, 100 - 3800)

    @patch('image_providers.services.save_generated_image', return_value='http://localhost:8000/media/generated_images/x.png')
    @patch('image_providers.factory.get_image_provider')
    def test_does_not_deduct_credits_when_personal_key_used(self, mock_get_provider, mock_save):
        provider = OpenAIImageProvider(api_key='test')
        provider.generate = AsyncMock(return_value=ImageResult(
            data=b'x', mime_type='image/png', usage={'input_tokens': 1_000_000, 'output_tokens': 1_000_000},
        ))
        mock_get_provider.return_value = provider

        _, executor = _build_image_tool('openai', 'sk-personal', self.user, False)
        run(executor({'prompt': 'a cat'}))

        self.user.refresh_from_db()
        self.assertEqual(self.user.credits_remaining, 100)


class SendChatMessageImageToolTest(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='imgtooluser', password='pass', credits_remaining=100)

    @patch('ai_providers.chat_router.get_provider')
    @patch('ai_providers.chat_router.run_agent_loop', new_callable=AsyncMock, return_value='Hi there!')
    @patch('keys.services.get_user_api_key', new_callable=AsyncMock, return_value=None)
    def test_includes_image_tool_for_supported_provider(self, mock_get_key, mock_run_loop, mock_get_provider):
        assistant = Assistant.objects.create(
            user=self.user, name='A', instructions='Be helpful.', ai_provider='openai',
        )
        mock_get_provider.return_value = MagicMock()

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
        mock_get_provider.return_value = MagicMock()

        run(send_chat_message(assistant, 'Hello', ai_provider='anthropic', model='claude-sonnet-5', user=self.user))

        # No image tool for a provider without image support, but delegate_to_model
        # is always offered regardless of provider.
        tool_names = [t['name'] for t in mock_run_loop.call_args.args[4]]
        self.assertNotIn('generate_image', tool_names)
        self.assertIn('delegate_to_model', tool_names)


class BuildDelegateToolTest(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='delegateuser', password='pass', credits_remaining=100)

    def test_declines_without_confirmation(self):
        confirm_tool_call = AsyncMock(return_value=False)
        _, executor = _build_delegate_tool(self.user, confirm_tool_call)

        result = run(executor({
            'provider': 'gemini', 'model': 'gemini-2.5-flash-image', 'prompt': 'a cat', 'reason': 'no image support',
        }))

        self.assertIn('declined', result)
        confirm_tool_call.assert_awaited_once_with('delegate_to_model', {
            'provider': 'gemini', 'model': 'gemini-2.5-flash-image', 'prompt': 'a cat', 'reason': 'no image support',
        })

    @patch('ai_providers.chat_router.send_chat_message', new_callable=AsyncMock)
    def test_proceeds_and_dispatches_when_confirmed(self, mock_send):
        mock_send.return_value = ('Here is your image: ![x](http://x)', UsageAccumulator(), False)
        confirm_tool_call = AsyncMock(return_value=True)
        _, executor = _build_delegate_tool(self.user, confirm_tool_call)

        result = run(executor({
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
        _, executor = _build_delegate_tool(self.user, AsyncMock(return_value=True))

        run(executor({'provider': 'openai', 'model': 'gpt-image-1', 'prompt': 'a cat', 'reason': 'x'}))

        call_kwargs = mock_send.call_args.kwargs
        self.assertNotEqual(call_kwargs['model'], 'gpt-image-1')
        self.assertIn(call_kwargs['model'], [m['id'] for m in PROVIDERS['openai'].AVAILABLE_MODELS])

    @patch('ai_providers.chat_router.send_chat_message', new_callable=AsyncMock)
    def test_uses_default_model_when_none_requested(self, mock_send):
        mock_send.return_value = ('OK', UsageAccumulator(), False)
        _, executor = _build_delegate_tool(self.user, AsyncMock(return_value=True))

        run(executor({'provider': 'openai', 'prompt': 'a cat', 'reason': 'x'}))

        call_kwargs = mock_send.call_args.kwargs
        self.assertEqual(call_kwargs['model'], PROVIDERS['openai'].AVAILABLE_MODELS[0]['id'])

    def test_proceeds_without_confirmation_hook(self):
        with patch('ai_providers.chat_router.send_chat_message', new_callable=AsyncMock) as mock_send:
            mock_send.return_value = ('OK', None, False)
            _, executor = _build_delegate_tool(self.user, None)

            result = run(executor({
                'provider': 'gemini', 'model': 'gemini-2.5-flash', 'prompt': 'a cat', 'reason': 'x',
            }))

            self.assertIn('OK', result)
            mock_send.assert_awaited_once()

    def test_unknown_provider_returns_error_text_without_dispatching(self):
        _, executor = _build_delegate_tool(self.user, AsyncMock(return_value=True))

        result = run(executor({'provider': 'not-a-provider', 'model': 'x', 'prompt': 'a cat', 'reason': 'x'}))

        self.assertIn('Unknown provider', result)

    @patch('ai_providers.chat_router.send_chat_message', new_callable=AsyncMock)
    def test_deducts_credits_when_delegated_call_used_global_key(self, mock_send):
        usage = UsageAccumulator(input_tokens=1_000_000, output_tokens=1_000_000)
        mock_send.return_value = ('OK', usage, True)
        _, executor = _build_delegate_tool(self.user, AsyncMock(return_value=True))

        run(executor({'provider': 'anthropic', 'model': 'claude-sonnet-5', 'prompt': 'hi', 'reason': 'x'}))

        self.user.refresh_from_db()
        # claude-sonnet-5: $3/$15 per 1M tokens = $18 = 1800 credits.
        self.assertEqual(self.user.credits_remaining, 100 - 1800)

    @patch('ai_providers.chat_router.send_chat_message', new_callable=AsyncMock)
    def test_does_not_deduct_credits_when_delegated_call_used_personal_key(self, mock_send):
        usage = UsageAccumulator(input_tokens=1_000_000, output_tokens=1_000_000)
        mock_send.return_value = ('OK', usage, False)
        _, executor = _build_delegate_tool(self.user, AsyncMock(return_value=True))

        run(executor({'provider': 'anthropic', 'model': 'claude-sonnet-5', 'prompt': 'hi', 'reason': 'x'}))

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
        mock_get_provider.return_value = MagicMock()

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
        mock_get_provider.return_value = MagicMock()

        run(send_chat_message(
            assistant, 'Hello', ai_provider='anthropic', model='claude-sonnet-5', user=self.user,
            allow_delegation=False,
        ))

        tool_names = [t['name'] for t in mock_run_loop.call_args.args[4]]
        self.assertNotIn('delegate_to_model', tool_names)
