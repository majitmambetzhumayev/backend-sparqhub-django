from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from projects.models import Project

User = get_user_model()


class ProjectCRUDAPITest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='projectuser', password='pass')
        self.client.force_authenticate(user=self.user)
        self.url = reverse('project-list')

    def test_create_project(self):
        response = self.client.post(self.url, data={"name": "Website Redesign", "description": "Q3 project"}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "Website Redesign")
        self.assertEqual(response.data["thread_count"], 0)

    def test_list_includes_thread_count(self):
        project = Project.objects.create(user=self.user, name="A")
        from assistants.models import Assistant
        from threads.models import Thread
        assistant = Assistant.objects.create(user=self.user, name="Assistant")
        Thread.objects.create(user=self.user, assistant=assistant, project=project)
        Thread.objects.create(user=self.user, assistant=assistant, project=project)

        response = self.client.get(self.url)
        item = next(p for p in response.data if p["id"] == project.id)
        self.assertEqual(item["thread_count"], 2)

    def test_update_project(self):
        project = Project.objects.create(user=self.user, name="Old name")
        detail_url = reverse('project-detail', kwargs={'pk': project.id})

        response = self.client.patch(detail_url, data={"name": "New name"}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "New name")

    def test_delete_project(self):
        project = Project.objects.create(user=self.user, name="Throwaway")
        detail_url = reverse('project-detail', kwargs={'pk': project.id})

        response = self.client.delete(detail_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Project.objects.filter(pk=project.id).exists())

    def test_cannot_access_another_users_project(self):
        other_user = User.objects.create_user(username='otheruser', password='pass')
        project = Project.objects.create(user=other_user, name="Not yours")
        detail_url = reverse('project-detail', kwargs={'pk': project.id})

        self.assertEqual(self.client.get(detail_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.delete(detail_url).status_code, status.HTTP_404_NOT_FOUND)

        list_resp = self.client.get(self.url)
        self.assertNotIn(project.id, [p["id"] for p in list_resp.data])
