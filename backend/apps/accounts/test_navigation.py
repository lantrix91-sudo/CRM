from django.test import TestCase
from .models import User
from apps.customers.models import Client


class NavigationTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_superuser(username="nav-manager", password="test-password")
        self.client.force_login(self.manager)

    def test_main_sections_render(self):
        for path in ("/profile/", "/manager/", "/operator/", "/clients/", "/calls/", "/analytics/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'href="/clients/"')
                self.assertNotContains(response, "Клиенты и заказы")
        self.assertRedirects(self.client.get("/"), "/profile/")

    def test_client_search_and_pagination(self):
        Client.objects.bulk_create([Client(name=f"Customer {i:02}", phone=f"+7700000{i:04}") for i in range(26)])
        first = self.client.get("/clients/")
        self.assertEqual(len(first.context["page_obj"]), 25)
        self.assertEqual(len(self.client.get("/clients/?page=2").context["page_obj"]), 1)
        result = self.client.get("/clients/?q=Customer+07")
        self.assertEqual(result.context["page_obj"].paginator.count, 1)
        self.assertContains(result, "Customer 07")

    def test_client_directory_restricted(self):
        self.client.logout()
        self.assertEqual(self.client.get("/clients/").status_code, 302)
        worker = User.objects.create_user(username="nav-worker", role="worker")
        self.client.force_login(worker)
        self.assertEqual(self.client.get("/clients/").status_code, 403)
        operator = User.objects.create_user(username="nav-operator", role="operator")
        self.client.force_login(operator)
        self.assertEqual(self.client.get("/clients/").status_code, 200)
