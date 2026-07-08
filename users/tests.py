import asyncio
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.tokens import default_token_generator
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APITestCase
from rest_framework import status
from rest_framework_simplejwt.tokens import AccessToken

from users.ws_auth import JWTAuthMiddleware

User = get_user_model()


def run(coro):
    return asyncio.run(coro)


def make_scope(cookie_header: str):
    return {"headers": [(b"cookie", cookie_header.encode())]}


class JWTAuthMiddlewareTest(TransactionTestCase):
    # database_sync_to_async runs on a different thread/DB connection than
    # setUp(); a plain TestCase's uncommitted transaction wouldn't be visible
    # to it, so this needs a TransactionTestCase (same reasoning as the
    # Channels consumer tests in chat_messages/tests.py).
    def setUp(self):
        self.user = User.objects.create_user(username="wsauth", password="pass")

    def _resolved_user(self, cookie_header: str):
        received = {}

        async def inner_app(scope, receive, send):
            received["user"] = scope["user"]

        run(JWTAuthMiddleware(inner_app)(make_scope(cookie_header), None, None))
        return received["user"]

    def test_valid_token_resolves_user(self):
        token = str(AccessToken.for_user(self.user))
        self.assertEqual(self._resolved_user(f"access_token={token}"), self.user)

    def test_missing_cookie_resolves_anonymous(self):
        self.assertIsInstance(self._resolved_user(""), AnonymousUser)

    def test_invalid_token_resolves_anonymous(self):
        self.assertIsInstance(self._resolved_user("access_token=garbage"), AnonymousUser)


class AuthCookieDomainTest(APITestCase):
    # Regression tests: a cookie set with no `domain` is host-only (scoped
    # exactly to the host that set it) and is never sent to a sibling
    # subdomain, even one that's "same-site" for SameSite-cookie purposes.
    # COOKIE_DOMAIN must actually reach the Set-Cookie header in prod, or the
    # frontend's own server-side auth check (reading this cookie on its own
    # domain) never sees it and the user gets bounced back to login forever.
    def setUp(self):
        User.objects.create_user(username="cookieuser", password="pass")

    def test_cookies_are_host_only_by_default(self):
        response = self.client.post(reverse('auth-login'), {"username": "cookieuser", "password": "pass"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.cookies['access_token']['domain'], '')
        self.assertEqual(response.cookies['refresh_token']['domain'], '')

    @override_settings(COOKIE_DOMAIN='.sparqup.fr')
    def test_cookies_get_shared_parent_domain_when_configured(self):
        response = self.client.post(reverse('auth-login'), {"username": "cookieuser", "password": "pass"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.cookies['access_token']['domain'], '.sparqup.fr')
        self.assertEqual(response.cookies['refresh_token']['domain'], '.sparqup.fr')

    @override_settings(COOKIE_DOMAIN='.sparqup.fr')
    def test_logout_clears_cookies_on_the_same_domain_they_were_set_with(self):
        response = self.client.post(reverse('auth-logout'))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(response.cookies['access_token']['domain'], '.sparqup.fr')
        self.assertEqual(response.cookies['refresh_token']['domain'], '.sparqup.fr')
        self.assertEqual(response.cookies['sessionid']['domain'], '.sparqup.fr')


class RegistrationEmailTest(APITestCase):
    def test_registration_requires_email(self):
        response = self.client.post(reverse('auth-register'), {"username": "newuser", "password": "pass12345"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', response.data)

    def test_registration_rejects_duplicate_email(self):
        User.objects.create_user(username="existing", password="pass", email="taken@example.com")
        response = self.client.post(reverse('auth-register'), {
            "username": "newuser", "password": "pass12345", "email": "taken@example.com",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', response.data)

    def test_registration_succeeds_with_unique_email(self):
        response = self.client.post(reverse('auth-register'), {
            "username": "newuser", "password": "pass12345", "email": "new@example.com",
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(User.objects.get(username="newuser").email, "new@example.com")

    def test_users_without_email_do_not_collide_with_each_other(self):
        # Regression test for the CustomUserManager.normalize_email override:
        # the base Django manager coerces a falsy email to '' — but multiple
        # '' values collide under email's unique constraint, while multiple
        # NULLs (what this override produces instead) don't.
        User.objects.create_user(username="noemail1", password="pass")
        User.objects.create_user(username="noemail2", password="pass")  # must not raise
        self.assertIsNone(User.objects.get(username="noemail1").email)
        self.assertIsNone(User.objects.get(username="noemail2").email)


class PasswordResetTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="resetuser", password="oldpass123", email="reset@example.com")

    @patch('users.services.send_email_task')
    def test_request_sends_email_for_registered_address(self, mock_task):
        response = self.client.post(reverse('password-reset-request'), {"email": "reset@example.com"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_task.delay.assert_called_once()
        self.assertEqual(mock_task.delay.call_args.args[0], "reset@example.com")

    @patch('users.services.send_email_task')
    def test_request_silently_no_ops_for_unknown_address(self, mock_task):
        response = self.client.post(reverse('password-reset-request'), {"email": "nobody@example.com"})
        # Same response either way — must not leak whether the email is registered.
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_task.delay.assert_not_called()

    def _valid_uid_token(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        return uid, token

    def test_confirm_with_valid_token_changes_password(self):
        uid, token = self._valid_uid_token()
        response = self.client.post(reverse('password-reset-confirm'), {
            "uid": uid, "token": token, "new_password": "brandnewpass456",
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("brandnewpass456"))

    def test_confirm_rejects_invalid_token(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        response = self.client.post(reverse('password-reset-confirm'), {
            "uid": uid, "token": "not-a-real-token", "new_password": "brandnewpass456",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpass123"))

    def test_confirm_rejects_unknown_uid(self):
        bogus_uid = urlsafe_base64_encode(force_bytes(999999))
        response = self.client.post(reverse('password-reset-confirm'), {
            "uid": bogus_uid, "token": "irrelevant", "new_password": "brandnewpass456",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_confirm_rejects_too_short_password(self):
        uid, token = self._valid_uid_token()
        response = self.client.post(reverse('password-reset-confirm'), {
            "uid": uid, "token": token, "new_password": "short",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_token_cannot_be_reused_after_password_already_changed(self):
        uid, token = self._valid_uid_token()
        self.client.post(reverse('password-reset-confirm'), {
            "uid": uid, "token": token, "new_password": "brandnewpass456",
        })
        # Django's token generator hashes in the password, so a token becomes
        # invalid as soon as it's been used once.
        response = self.client.post(reverse('password-reset-confirm'), {
            "uid": uid, "token": token, "new_password": "anotherpass789",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class AdminUserViewSetTest(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_user", password="pass", is_staff=True)
        self.other_user = User.objects.create_user(username="plain_user", password="pass", credits_remaining=50)

    def test_non_admin_cannot_list_users(self):
        self.client.force_authenticate(user=self.other_user)
        response = self.client.get(reverse('admin-user-list'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_cannot_list_users(self):
        response = self.client.get(reverse('admin-user-list'))
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_admin_can_list_users(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(reverse('admin-user-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        usernames = [u['username'] for u in response.data]
        self.assertIn('plain_user', usernames)
        self.assertIn('admin_user', usernames)

    def test_admin_can_update_another_users_credits(self):
        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-user-detail', kwargs={'pk': self.other_user.pk})
        response = self.client.patch(url, {'credits_remaining': 500}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.other_user.refresh_from_db()
        self.assertEqual(self.other_user.credits_remaining, 500)

    def test_admin_can_update_their_own_credits(self):
        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-user-detail', kwargs={'pk': self.admin.pk})
        response = self.client.patch(url, {'credits_remaining': 9999}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.credits_remaining, 9999)

    def test_admin_can_deactivate_a_user(self):
        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-user-detail', kwargs={'pk': self.other_user.pk})
        response = self.client.patch(url, {'is_active': False}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.other_user.refresh_from_db()
        self.assertFalse(self.other_user.is_active)

    def test_negative_credits_rejected(self):
        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-user-detail', kwargs={'pk': self.other_user.pk})
        response = self.client.patch(url, {'credits_remaining': -1}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_admin_cannot_update_own_credits(self):
        self.client.force_authenticate(user=self.other_user)
        url = reverse('admin-user-detail', kwargs={'pk': self.other_user.pk})
        response = self.client.patch(url, {'credits_remaining': 999999}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.other_user.refresh_from_db()
        self.assertEqual(self.other_user.credits_remaining, 50)

    def test_is_staff_not_writable_through_this_endpoint(self):
        self.client.force_authenticate(user=self.admin)
        url = reverse('admin-user-detail', kwargs={'pk': self.other_user.pk})
        response = self.client.patch(url, {'is_staff': True}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.other_user.refresh_from_db()
        self.assertFalse(self.other_user.is_staff)
