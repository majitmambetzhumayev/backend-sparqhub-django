import asyncio
from unittest.mock import MagicMock, patch

from authlib.integrations.base_client import OAuthError
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.contrib.auth.tokens import default_token_generator
from django.http import HttpResponseRedirect

from users.serializers import CurrentUserSerializer
from users.services import email_confirmation_token_generator, get_or_create_oauth_user
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient, APITestCase
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

    def test_unexpected_error_resolves_anonymous_but_is_logged(self):
        # Regression test: this file used to have no logger at all — an
        # expired/malformed token (expected) and a genuine bug (e.g. a DB
        # error resolving the user) were completely indistinguishable, with
        # zero trace either way.
        token = str(AccessToken.for_user(self.user))
        with patch("users.ws_auth.CookieJWTAuthentication.get_user", side_effect=RuntimeError("db blip")):
            with self.assertLogs("users.ws_auth", level="ERROR"):
                user = self._resolved_user(f"access_token={token}")
        self.assertIsInstance(user, AnonymousUser)


class LoginCsrfProtectionTest(APITestCase):
    # Regression test: CookieTokenObtainPairView used to be @csrf_exempt in
    # addition to @ensure_csrf_cookie — combined with DRF's default parsers
    # accepting a plain form-encoded body, that made "login CSRF" possible
    # (a cross-site auto-submitting HTML form could force a victim's browser
    # to authenticate as an attacker-controlled account). The frontend
    # already fetches /api/csrf/ on app mount before any login attempt, so
    # requiring the token here doesn't break the real login flow.
    def setUp(self):
        cache.clear()  # avoid the 'auth' throttle scope leaking across tests
        User.objects.create_user(username='csrfuser', password='pass', email_verified=True)

    def test_login_without_csrf_token_is_rejected(self):
        client = APIClient(enforce_csrf_checks=True)
        response = client.post(reverse('auth-login'), {"username": "csrfuser", "password": "pass"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_login_with_valid_csrf_token_succeeds(self):
        client = APIClient(enforce_csrf_checks=True)
        client.get(reverse('csrf'))
        token = client.cookies['csrftoken'].value

        response = client.post(
            reverse('auth-login'), {"username": "csrfuser", "password": "pass"}, HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class AuthEndpointRateLimitTest(APITestCase):
    # Guards against credential stuffing / brute force on pre-auth
    # endpoints, where there's no user identity to key on yet — only IP.
    def setUp(self):
        cache.clear()
        User.objects.create_user(username='ratelimituser', password='pass', email_verified=True)

    def test_login_endpoint_is_rate_limited(self):
        url = reverse('auth-login')
        for _ in range(10):
            self.client.post(url, {"username": "ratelimituser", "password": "wrong"})
        response = self.client.post(url, {"username": "ratelimituser", "password": "wrong"})
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_register_endpoint_is_rate_limited(self):
        url = reverse('auth-register')
        for i in range(10):
            self.client.post(url, {"username": f"newuser{i}", "password": "pass12345", "email": f"n{i}@example.com"})
        response = self.client.post(
            url, {"username": "onemore", "password": "pass12345", "email": "onemore@example.com"},
        )
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class AuthCookieDomainTest(APITestCase):
    # Regression tests: a cookie set with no `domain` is host-only (scoped
    # exactly to the host that set it) and is never sent to a sibling
    # subdomain, even one that's "same-site" for SameSite-cookie purposes.
    # COOKIE_DOMAIN must actually reach the Set-Cookie header in prod, or the
    # frontend's own server-side auth check (reading this cookie on its own
    # domain) never sees it and the user gets bounced back to login forever.
    def setUp(self):
        cache.clear()  # avoid the shared 'auth' throttle scope leaking across tests
        User.objects.create_user(username="cookieuser", password="pass", email_verified=True)

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
    def setUp(self):
        cache.clear()  # avoid the shared 'auth' throttle scope leaking across tests

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

    @patch('users.services.send_email_task')
    def test_registration_succeeds_with_unique_email(self, mock_task):
        response = self.client.post(reverse('auth-register'), {
            "username": "newuser", "password": "pass12345", "email": "new@example.com",
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(User.objects.get(username="newuser").email, "new@example.com")

    @patch('users.services.send_email_task')
    def test_registration_does_not_set_auth_cookies(self, mock_task):
        response = self.client.post(reverse('auth-register'), {
            "username": "newuser", "password": "pass12345", "email": "new@example.com",
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn('access_token', response.cookies)
        self.assertNotIn('refresh_token', response.cookies)

    @patch('users.services.send_email_task')
    def test_registration_sends_confirmation_email(self, mock_task):
        self.client.post(reverse('auth-register'), {
            "username": "newuser", "password": "pass12345", "email": "new@example.com",
        })
        mock_task.delay.assert_called_once()
        self.assertEqual(mock_task.delay.call_args.args[0], "new@example.com")

    @patch('users.services.send_email_task')
    def test_new_user_is_unverified_by_default(self, mock_task):
        self.client.post(reverse('auth-register'), {
            "username": "newuser", "password": "pass12345", "email": "new@example.com",
        })
        self.assertFalse(User.objects.get(username="newuser").email_verified)

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
        cache.clear()  # avoid the shared 'auth' throttle scope leaking across tests
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


class EmailVerificationLoginGateTest(APITestCase):
    def setUp(self):
        cache.clear()  # avoid the shared 'auth' throttle scope leaking across tests
        self.verified_user = User.objects.create_user(
            username="verified", password="pass12345", email="verified@example.com", email_verified=True,
        )
        self.unverified_user = User.objects.create_user(
            username="unverified", password="pass12345", email="unverified@example.com", email_verified=False,
        )

    def test_verified_user_can_log_in(self):
        response = self.client.post(reverse('auth-login'), {"username": "verified", "password": "pass12345"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unverified_user_is_rejected(self):
        response = self.client.post(reverse('auth-login'), {"username": "unverified", "password": "pass12345"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotIn('access_token', response.cookies)
        self.assertNotIn('refresh_token', response.cookies)


class EmailConfirmTest(APITestCase):
    def setUp(self):
        cache.clear()  # avoid the shared 'auth' throttle scope leaking across tests
        self.user = User.objects.create_user(username="confirmuser", password="pass12345", email="confirm@example.com")

    def _valid_uid_token(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = email_confirmation_token_generator.make_token(self.user)
        return uid, token

    def test_confirm_with_valid_token_marks_verified(self):
        uid, token = self._valid_uid_token()
        response = self.client.post(reverse('email-confirm'), {"uid": uid, "token": token})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_confirm_rejects_invalid_token(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        response = self.client.post(reverse('email-confirm'), {"uid": uid, "token": "not-a-real-token"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)

    def test_confirm_rejects_unknown_uid(self):
        bogus_uid = urlsafe_base64_encode(force_bytes(999999))
        response = self.client.post(reverse('email-confirm'), {"uid": bogus_uid, "token": "irrelevant"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_token_cannot_be_reused_after_already_confirmed(self):
        uid, token = self._valid_uid_token()
        self.client.post(reverse('email-confirm'), {"uid": uid, "token": token})
        # email_verified is folded into the token's hash value, so it
        # becomes invalid as soon as it's been used once.
        response = self.client.post(reverse('email-confirm'), {"uid": uid, "token": token})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_password_reset_token_cannot_be_used_to_confirm_email(self):
        # Regression test for the distinct key_salt: a password-reset token
        # must not validate against the email-confirmation generator.
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        reset_token = default_token_generator.make_token(self.user)
        response = self.client.post(reverse('email-confirm'), {"uid": uid, "token": reset_token})
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


class GetOrCreateOAuthUserTest(TestCase):
    def test_creates_new_verified_user(self):
        user = get_or_create_oauth_user('google', 'g-1', 'newuser@example.com')
        self.assertEqual(user.google_id, 'g-1')
        self.assertTrue(user.email_verified)
        self.assertEqual(user.email, 'newuser@example.com')
        self.assertEqual(user.username, 'newuser')

    def test_reclaims_existing_unverified_user_by_email_and_invalidates_its_password(self):
        # Regression test for a pre-account-hijacking vulnerability: anyone
        # can register with any email address and leave it unconfirmed
        # (e.g. squatting the real owner's email with an attacker-chosen
        # password, hoping the owner later signs in via OAuth and inherits
        # that password-protected row). OAuth login must reclaim the
        # account AND invalidate whatever password was already set, so the
        # squatter's password stops working the moment the real owner
        # proves ownership via the provider.
        existing = User.objects.create_user(username='existing', password='attacker-chosen-pw', email='link@example.com')
        self.assertFalse(existing.email_verified)
        self.assertTrue(existing.check_password('attacker-chosen-pw'))

        user = get_or_create_oauth_user('github', 'gh-1', 'link@example.com')

        self.assertEqual(user.pk, existing.pk)
        user.refresh_from_db()
        self.assertEqual(user.github_id, 'gh-1')
        self.assertTrue(user.email_verified)
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.check_password('attacker-chosen-pw'))

    def test_linking_a_second_provider_to_an_already_verified_user_does_not_touch_password(self):
        existing = User.objects.create_user(
            username='alreadyverified', password='real-user-pw', email='verified@example.com', email_verified=True,
        )

        user = get_or_create_oauth_user('github', 'gh-2', 'verified@example.com')

        self.assertEqual(user.pk, existing.pk)
        user.refresh_from_db()
        self.assertEqual(user.github_id, 'gh-2')
        self.assertTrue(user.check_password('real-user-pw'))

    def test_returns_same_user_on_repeat_call(self):
        first = get_or_create_oauth_user('google', 'g-2', 'repeat@example.com')
        second = get_or_create_oauth_user('google', 'g-2', 'repeat@example.com')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(User.objects.filter(google_id='g-2').count(), 1)

    def test_generates_unique_username_on_collision(self):
        User.objects.create_user(username='taken', password='pass')
        user = get_or_create_oauth_user('google', 'g-3', 'taken@example.com')
        self.assertNotEqual(user.username, 'taken')
        self.assertTrue(user.username.startswith('taken'))


class OAuthLoginViewTest(APITestCase):
    def test_unknown_provider_returns_404(self):
        response = self.client.get(reverse('oauth-login', kwargs={'provider': 'facebook'}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch('users.views.oauth')
    def test_known_provider_delegates_to_client_redirect(self, mock_oauth):
        mock_client = MagicMock()
        mock_client.authorize_redirect.return_value = HttpResponseRedirect('https://accounts.google.com/o/oauth2/auth')
        mock_oauth.create_client.return_value = mock_client

        self.client.get(reverse('oauth-login', kwargs={'provider': 'google'}))

        mock_oauth.create_client.assert_called_once_with('google')
        mock_client.authorize_redirect.assert_called_once()
        redirect_uri = mock_client.authorize_redirect.call_args.args[1]
        self.assertTrue(redirect_uri.endswith('/api/auth/oauth/google/callback/'))


class OAuthCallbackViewTest(APITestCase):
    def test_unknown_provider_returns_404(self):
        response = self.client.get(reverse('oauth-callback', kwargs={'provider': 'facebook'}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch('users.views.oauth')
    def test_provider_error_redirects_to_login_with_error(self, mock_oauth):
        mock_client = MagicMock()
        mock_client.authorize_access_token.side_effect = OAuthError(error='access_denied')
        mock_oauth.create_client.return_value = mock_client

        # Regression test: this failure used to be caught with no logging at
        # all — a misconfigured client secret and a user simply cancelling
        # the consent screen were completely indistinguishable, with zero
        # trace either way.
        with self.assertLogs('users.views', level='WARNING'):
            response = self.client.get(reverse('oauth-callback', kwargs={'provider': 'google'}))

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertIn('/auth/login', response.url)

    @patch('users.views.oauth')
    def test_google_callback_creates_user_and_sets_auth_cookies(self, mock_oauth):
        mock_client = MagicMock()
        mock_client.authorize_access_token.return_value = {
            'userinfo': {'sub': 'google-123', 'email': 'newgoogle@example.com'},
        }
        mock_oauth.create_client.return_value = mock_client

        response = self.client.get(reverse('oauth-callback', kwargs={'provider': 'google'}))

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertIn('/dashboard', response.url)
        self.assertIn('access_token', response.cookies)
        self.assertIn('refresh_token', response.cookies)
        user = User.objects.get(google_id='google-123')
        self.assertEqual(user.email, 'newgoogle@example.com')
        self.assertTrue(user.email_verified)

    @patch('users.views.oauth')
    def test_github_callback_falls_back_to_emails_endpoint(self, mock_oauth):
        mock_client = MagicMock()
        mock_client.authorize_access_token.return_value = {'access_token': 'tok'}
        profile_response = MagicMock()
        profile_response.json.return_value = {'id': 999, 'email': None}
        emails_response = MagicMock()
        emails_response.json.return_value = [
            {'email': 'secondary@example.com', 'primary': False, 'verified': True},
            {'email': 'primary@example.com', 'primary': True, 'verified': True},
        ]
        mock_client.get.side_effect = [profile_response, emails_response]
        mock_oauth.create_client.return_value = mock_client

        response = self.client.get(reverse('oauth-callback', kwargs={'provider': 'github'}))

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        user = User.objects.get(github_id='999')
        self.assertEqual(user.email, 'primary@example.com')

    @patch('users.views.oauth')
    def test_missing_email_redirects_with_error(self, mock_oauth):
        mock_client = MagicMock()
        mock_client.authorize_access_token.return_value = {
            'userinfo': {'sub': 'g-no-email', 'email': None},
        }
        mock_oauth.create_client.return_value = mock_client

        response = self.client.get(reverse('oauth-callback', kwargs={'provider': 'google'}))

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertIn('oauth_no_email', response.url)
        self.assertFalse(User.objects.filter(google_id='g-no-email').exists())


class MarkOnboardingSeenTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='onboarduser', password='pass', email_verified=True)
        self.client.force_authenticate(user=self.user)

    def test_marks_onboarding_seen(self):
        self.assertFalse(self.user.has_seen_onboarding)

        response = self.client.post(reverse('onboarding-seen'))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.user.refresh_from_db()
        self.assertTrue(self.user.has_seen_onboarding)

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(reverse('onboarding-seen'))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_requires_csrf_token_when_enforced(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_authenticate(user=self.user)

        response = client.post(reverse('onboarding-seen'))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CurrentUserOnboardingFieldTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='meuser', password='pass', email_verified=True)
        self.client.force_authenticate(user=self.user)

    def test_me_endpoint_exposes_has_seen_onboarding(self):
        response = self.client.get(reverse('auth-me'))

        self.assertEqual(response.data['user']['has_seen_onboarding'], False)

    def test_field_is_read_only_via_me_endpoint(self):
        # CurrentUserAPIView is GET-only, but this documents the intent
        # explicitly: has_seen_onboarding must only ever flip via
        # MarkOnboardingSeenAPIView, never as a side effect of some other
        # user-update path being added later.
        self.assertIn('has_seen_onboarding', CurrentUserSerializer(self.user).data)
        serializer = CurrentUserSerializer(self.user, data={'has_seen_onboarding': True}, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_seen_onboarding)


class CookieTokenRefreshTest(APITestCase):
    # Regression tests: the refresh token lives in an httpOnly cookie, never
    # in the request body -- a real browser client has no way to read it and
    # can only ever rely on this endpoint pulling it from the cookie jar
    # (self.client carries cookies across requests here exactly like a
    # browser would).
    def setUp(self):
        cache.clear()
        User.objects.create_user(username="refreshuser", password="pass", email_verified=True)
        login_response = self.client.post(reverse('auth-login'), {"username": "refreshuser", "password": "pass"})
        self.original_refresh_value = login_response.cookies['refresh_token'].value

    def test_refresh_with_only_the_cookie_present_succeeds(self):
        # No body at all -- exactly what a browser client can actually send.
        response = self.client.post(reverse('auth-refresh'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access_token', response.cookies)

    def test_rotated_refresh_token_is_re_cookied(self):
        # ROTATE_REFRESH_TOKENS issues a new refresh token on every call --
        # if it isn't re-cookied here, every session breaks after exactly
        # one silent refresh.
        response = self.client.post(reverse('auth-refresh'))

        self.assertIn('refresh_token', response.cookies)
        self.assertNotEqual(response.cookies['refresh_token'].value, self.original_refresh_value)

    def test_old_refresh_token_is_blacklisted_after_rotation(self):
        self.client.post(reverse('auth-refresh'))  # rotates -- old cookie value is now blacklisted

        self.client.cookies['refresh_token'] = self.original_refresh_value
        response = self.client.post(reverse('auth-refresh'))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_with_no_cookie_at_all_is_rejected(self):
        self.client.cookies.pop('refresh_token', None)

        response = self.client.post(reverse('auth-refresh'))

        # A missing value fails standard DRF field validation (refresh is a
        # required CharField) before it ever reaches SimpleJWT's own
        # token-parsing/InvalidToken path -- 400, not 401, and correctly so
        # (malformed/absent input vs. a rejected credential).
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_refresh_with_invalid_token_is_rejected(self):
        self.client.cookies['refresh_token'] = 'not-a-real-token'

        response = self.client.post(reverse('auth-refresh'))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
