import asyncio

from django.urls import reverse
from rest_framework.test import APITestCase, APITransactionTestCase
from django.contrib.auth import get_user_model
from rest_framework import status
from .services import (
    get_user_api_key,
    create_or_update_user_api_key,
    delete_user_api_key,
)
from .models import APIKey

User = get_user_model()


def run(coro):
    return asyncio.run(coro)


class APIKeyServiceTests(APITransactionTestCase):
    # get_user_api_key() is async and hops to a different DB connection; a
    # plain TestCase's uncommitted transaction from setUp() isn't visible
    # there (same gotcha documented in chat_messages/tests.py).
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')

    def test_get_api_key_service(self):
        create_or_update_user_api_key(self.user, 'anthropic', "dummy_encrypted_key")
        api_key = run(get_user_api_key(self.user, 'anthropic'))
        self.assertIsNotNone(api_key)
        self.assertEqual(api_key.key_type, "anthropic")
        self.assertEqual(api_key.encrypted_key, "dummy_encrypted_key")

    def test_update_api_key_service(self):
        create_or_update_user_api_key(self.user, 'anthropic', "dummy_encrypted_key")
        first_key_id = APIKey.objects.get(user=self.user, key_type='anthropic').encryption_key_id
        updated_api_key = create_or_update_user_api_key(self.user, 'anthropic', "new_dummy_encrypted_key")
        self.assertEqual(updated_api_key.encrypted_key, "new_dummy_encrypted_key")
        self.assertNotEqual(updated_api_key.encryption_key_id, first_key_id)
        self.assertEqual(APIKey.objects.filter(user=self.user, key_type='anthropic').count(), 1)

    def test_delete_api_key_service(self):
        create_or_update_user_api_key(self.user, 'anthropic', "dummy_encrypted_key")
        deleted_count = delete_user_api_key(self.user, 'anthropic')
        self.assertEqual(deleted_count, 1)
        api_key = run(get_user_api_key(self.user, 'anthropic'))
        self.assertIsNone(api_key)


class APIKeyAPITest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='apiuser', password='testpass')
        self.client.force_authenticate(user=self.user)
        self.url = reverse('apikey-list')

    def test_create_key(self):
        response = self.client.post(self.url, {"key_type": "anthropic", "raw_key": "sk-test-123"}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["key_type"], "anthropic")
        self.assertNotIn("raw_key", response.data)
        self.assertNotIn("encrypted_key", response.data)

    def test_create_upserts_existing_key(self):
        self.client.post(self.url, {"key_type": "anthropic", "raw_key": "sk-first"}, format='json')
        self.client.post(self.url, {"key_type": "anthropic", "raw_key": "sk-second"}, format='json')
        self.assertEqual(APIKey.objects.filter(user=self.user, key_type='anthropic').count(), 1)
        self.assertEqual(APIKey.objects.get(user=self.user, key_type='anthropic').encrypted_key, "sk-second")

    def test_list_never_leaks_raw_key(self):
        create_or_update_user_api_key(self.user, 'anthropic', "sk-secret")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("encrypted_key", response.data[0])
        self.assertNotIn("raw_key", response.data[0])

    def test_delete_key(self):
        key = create_or_update_user_api_key(self.user, 'anthropic', "sk-secret")
        response = self.client.delete(reverse('apikey-detail', kwargs={'pk': key.id}))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(APIKey.objects.filter(pk=key.id).exists())

    def test_only_returns_own_keys(self):
        other_user = User.objects.create_user(username='otheruser', password='testpass')
        create_or_update_user_api_key(other_user, 'anthropic', "sk-other")

        response = self.client.get(self.url)
        self.assertEqual(response.data, [])
