# prompts/tests.py
from django.test import TestCase

from .models import PromptTemplate
from .services import get_active_prompt


class PromptTemplateModelTest(TestCase):
    def setUp(self):
        # Isolate from the 0002 seed migration's row -- same reasoning as
        # changelog/tests.py's own ChangelogEntry.objects.all().delete().
        PromptTemplate.objects.all().delete()

    def test_activating_a_new_version_deactivates_the_previous_one(self):
        v1 = PromptTemplate.objects.create(name="greeting", version=1, content="Hello v1", is_active=True)

        v2 = PromptTemplate.objects.create(name="greeting", version=2, content="Hello v2", is_active=True)

        v1.refresh_from_db()
        self.assertFalse(v1.is_active)
        self.assertTrue(v2.is_active)

    def test_activating_a_new_version_does_not_affect_a_different_prompt_name(self):
        other = PromptTemplate.objects.create(name="farewell", version=1, content="Bye", is_active=True)

        PromptTemplate.objects.create(name="greeting", version=1, content="Hello", is_active=True)

        other.refresh_from_db()
        self.assertTrue(other.is_active)

    def test_creating_an_inactive_version_does_not_deactivate_the_active_one(self):
        active = PromptTemplate.objects.create(name="greeting", version=1, content="Hello v1", is_active=True)

        PromptTemplate.objects.create(name="greeting", version=2, content="Hello v2", is_active=False)

        active.refresh_from_db()
        self.assertTrue(active.is_active)


class GetActivePromptTest(TestCase):
    def setUp(self):
        PromptTemplate.objects.all().delete()

    def test_returns_the_active_version_content(self):
        PromptTemplate.objects.create(name="greeting", version=1, content="Hello v1", is_active=False)
        PromptTemplate.objects.create(name="greeting", version=2, content="Hello v2", is_active=True)

        self.assertEqual(get_active_prompt("greeting"), "Hello v2")

    def test_raises_does_not_exist_when_no_active_version(self):
        PromptTemplate.objects.create(name="greeting", version=1, content="Hello v1", is_active=False)

        with self.assertRaises(PromptTemplate.DoesNotExist):
            get_active_prompt("greeting")

    def test_raises_does_not_exist_for_an_unknown_name(self):
        with self.assertRaises(PromptTemplate.DoesNotExist):
            get_active_prompt("does_not_exist")
