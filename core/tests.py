from unittest.mock import patch

from django.test import SimpleTestCase

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
