from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from .models import APIKey
from .services import (
    get_user_api_key,
    create_or_update_user_api_key,
    delete_user_api_key,
)

User = get_user_model()

class APIKeyAPITests(APITestCase):
    def setUp(self):
        # Create a test user and log in
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.client.login(username='testuser', password='testpass')
        
        # Define sample data for an API key
        self.api_key_data = {
            "key_type": "openai",
            "encrypted_key": "dummy_encrypted_key",
            "encryption_key_id": "key_v1",
        }
        # URL for the APIKey viewset (registered with basename 'apikey')
        self.url = reverse('apikey-list')

    def test_create_api_key_via_api(self):
        # Create API key via POST request to the viewset endpoint
        response = self.client.post(self.url, data=self.api_key_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['key_type'], self.api_key_data['key_type'])
        self.assertEqual(response.data['encryption_key_id'], self.api_key_data['encryption_key_id'])
        # Check that the API key is associated with the logged-in user
        self.assertEqual(response.data['user'], self.user.id)

    def test_get_api_key_service(self):
        # Create an API key using the service function
        create_or_update_user_api_key(self.user, 'openai', "dummy_encrypted_key", "key_v1")
        api_key = get_user_api_key(self.user, 'openai')
        self.assertIsNotNone(api_key)
        self.assertEqual(api_key.encryption_key_id, "key_v1")
        self.assertEqual(api_key.key_type, "openai")

    def test_update_api_key_service(self):
        # Create an API key then update it using the service layer
        create_or_update_user_api_key(self.user, 'openai', "dummy_encrypted_key", "key_v1")
        updated_api_key = create_or_update_user_api_key(self.user, 'openai', "new_dummy_encrypted_key", "key_v2")
        self.assertEqual(updated_api_key.encryption_key_id, "key_v2")
        self.assertEqual(updated_api_key.encrypted_key, "new_dummy_encrypted_key")

    def test_delete_api_key_service(self):
        # Create an API key and then delete it
        create_or_update_user_api_key(self.user, 'openai', "dummy_encrypted_key", "key_v1")
        deleted_count = delete_user_api_key(self.user, 'openai')
        self.assertEqual(deleted_count, 1)
        api_key = get_user_api_key(self.user, 'openai')
        self.assertIsNone(api_key)
