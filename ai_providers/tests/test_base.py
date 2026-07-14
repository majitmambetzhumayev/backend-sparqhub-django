import logging

from django.test import SimpleTestCase

from ai_providers.base import ProviderResponse, warn_if_finish_reason_suspicious


class WarnIfFinishReasonSuspiciousTest(SimpleTestCase):
    def test_no_log_for_benign_reasons(self):
        for reason in (None, 'stop', 'end_turn', 'tool_use', 'tool_calls', 'STOP'):
            with self.assertNoLogs('ai_providers.base', level='WARNING'):
                warn_if_finish_reason_suspicious(ProviderResponse(text='Hi', finish_reason=reason))

    def test_logs_warning_for_suspicious_reason(self):
        with self.assertLogs('ai_providers.base', level='WARNING') as cm:
            warn_if_finish_reason_suspicious(ProviderResponse(text='Cut off', finish_reason='length'))
        self.assertIn('length', cm.output[0])
