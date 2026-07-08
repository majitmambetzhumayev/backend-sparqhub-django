import asyncio

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
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
