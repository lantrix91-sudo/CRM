from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.customers.models import Client
from apps.services.models import Service

from .models import Lead


class LeadCardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.viewer = get_user_model().objects.create_user(username="viewer", role="operator")
        cls.viewer.user_permissions.add(
            Permission.objects.get(content_type__app_label="leads", codename="view_lead"),
            Permission.objects.get(content_type__app_label="customers", codename="view_client"),
        )
        cls.employee = get_user_model().objects.create_user(
            username="sergey", first_name="Сергей", role="worker",
        )
        cls.customer = Client.objects.create(name="Иван Петров", phone="+77001234567")
        cls.service = Service.objects.create(name="Ремонт холодильника")
        cls.lead = Lead.objects.create(
            title="Холодильник не охлаждает", client=cls.customer,
            service=cls.service, employee=cls.employee, source="OLX",
        )
        cls.url = reverse("leads:list")

    def setUp(self):
        self.client.force_login(self.viewer)

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("login") + "?next=" + self.url)

    def test_staff_without_permissions_cannot_read_clients(self):
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_view_lead_alone_does_not_grant_client_access(self):
        self.employee.user_permissions.add(
            Permission.objects.get(content_type__app_label="leads", codename="view_lead"),
        )
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_worker_with_permissions_cannot_read_cards(self):
        self.viewer.role = "worker"
        self.viewer.save(update_fields=["role"])
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_card_contains_all_requested_fields(self):
        response = self.client.get(self.url)
        for text in ("Иван Петров", "+77001234567", "Ремонт холодильника", "OLX", "Сергей", "Новый"):
            self.assertContains(response, text)
        self.assertNotContains(response, reverse("admin:leads_lead_add"))
        self.assertNotContains(response, reverse("admin:leads_lead_change", args=[self.lead.pk]))

    def test_missing_source_and_employee(self):
        self.lead.employee = None
        self.lead.source = ""
        self.lead.save()
        response = self.client.get(self.url)
        self.assertContains(response, "Не назначен")
        self.assertContains(response, "Не указан")

    def test_search_and_status_filter(self):
        other = Lead.objects.create(
            title="Другой лид", client=self.customer, service=self.service,
            status=Lead.Status.IN_PROGRESS, source="Сайт",
        )
        for query in ("Иван", "7700123", "холодильника", "OLX"):
            with self.subTest(query=query):
                response = self.client.get(self.url, {"q": query, "status": "new"})
                self.assertEqual(list(response.context["page_obj"]), [self.lead])
        response = self.client.get(self.url, {"status": "in_progress"})
        self.assertEqual(list(response.context["page_obj"]), [other])
        response = self.client.get(self.url, {"q": "нет совпадений"})
        self.assertContains(response, "Лиды не найдены")

    def test_pagination_preserves_filters(self):
        Lead.objects.bulk_create([
            Lead(title="Extra", client=self.customer, service=self.service, source="OLX")
            for _ in range(12)
        ])
        response = self.client.get(self.url, {"q": "OLX", "status": "new"})
        self.assertEqual(len(response.context["page_obj"]), 12)
        self.assertContains(response, "?q=OLX&amp;status=new&amp;page=2")
        response = self.client.get(self.url, {"q": "OLX", "status": "new", "page": 2})
        self.assertEqual(len(response.context["page_obj"]), 1)
        self.assertEqual(self.client.get(self.url, {"page": "invalid"}).status_code, 200)

    def test_customer_text_is_escaped(self):
        self.customer.name = "<script>alert(1)</script>"
        self.customer.save()
        response = self.client.get(self.url)
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_status_validation_and_default(self):
        self.assertEqual(self.lead.status, Lead.Status.NEW)
        self.lead.status = "unknown"
        with self.assertRaises(ValidationError):
            self.lead.full_clean()

    def test_related_records_do_not_add_per_card_queries(self):
        Lead.objects.bulk_create([
            Lead(title="Extra", client=self.customer, service=self.service, employee=self.employee)
            for _ in range(4)
        ])
        with self.assertNumQueries(4):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
