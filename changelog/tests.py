# changelog/tests.py
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status

from .models import ChangelogEntry


class ChangelogEntryModelTest(APITestCase):
    def test_orders_most_recent_first(self):
        # The changelog.0002 data migration seeds real entries into every
        # test database -- cleared here so this test's ordering assertion
        # isn't dependent on (or broken by) that unrelated seed data.
        ChangelogEntry.objects.all().delete()
        older = ChangelogEntry.objects.create(
            title_fr="Ancien", title_en="Older", published_at=timezone.now() - timezone.timedelta(days=1),
        )
        newer = ChangelogEntry.objects.create(title_fr="Récent", title_en="Newer", published_at=timezone.now())

        self.assertEqual(list(ChangelogEntry.objects.all()), [newer, older])


class ChangelogListAPITest(APITestCase):
    def setUp(self):
        # Same reasoning as above -- isolate from the 0002 seed data.
        ChangelogEntry.objects.all().delete()
        ChangelogEntry.objects.create(
            title_fr="Titre FR", title_en="Title EN",
            description_fr="Description FR", description_en="Description EN",
            commit_refs="backend#47, frontend#41",
        )

    def test_is_publicly_readable_without_authentication(self):
        response = self.client.get(reverse('changelog-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_returns_both_locales_and_excludes_internal_commit_refs(self):
        response = self.client.get(reverse('changelog-list'))

        entry = response.data[0]
        self.assertEqual(entry['title_fr'], "Titre FR")
        self.assertEqual(entry['title_en'], "Title EN")
        self.assertEqual(entry['description_fr'], "Description FR")
        self.assertEqual(entry['description_en'], "Description EN")
        self.assertNotIn('commit_refs', entry)
