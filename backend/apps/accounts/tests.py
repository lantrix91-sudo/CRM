from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, Client as Browser
from apps.customers.models import Client
from apps.services.models import Service
from apps.orders.models import Order

class RoleTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user(username="manager", role="manager")
        self.operator = User.objects.create_user(username="operator", role="operator")
        self.worker = User.objects.create_user(username="worker", role="worker")
        self.other = User.objects.create_user(username="other", role="worker")
        customer = Client.objects.create(name="Private client", phone="+77000000000")
        service = Service.objects.create(name="Repair")
        self.order = Order.objects.create(title="Private work", client=customer, service=service, employee=self.worker, status="assigned", amount=Decimal("1000"))
        self.url = f"/orders/{self.order.pk}/"

    def login(self, user):
        self.client.force_login(user)

    def test_role_redirects_without_staff(self):
        for user, url in [(self.manager, "/manager/"), (self.operator, "/operator/"), (self.worker, "/my-orders/")]:
            self.login(user)
            self.assertRedirects(self.client.get("/"), url)

    def test_worker_cannot_read_or_change_others(self):
        self.login(self.other)
        self.assertNotContains(self.client.get("/my-orders/"), "Private client")
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {"action": "complete"}).status_code, 404)

    def test_worker_own_lifecycle_and_no_payment(self):
        self.login(self.worker)
        self.assertContains(self.client.get(self.url), "Private client")
        self.assertEqual(self.client.post(self.url, {"action": "start"}).status_code, 302)
        self.assertEqual(self.client.post(self.url, {"action": "complete"}).status_code, 302)
        self.assertEqual(self.client.post(self.url, {"action": "pay"}).status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "completed")

    def test_operator_cannot_pay_manager_can(self):
        self.order.status = "completed"
        self.order.save()
        self.login(self.operator)
        self.assertEqual(self.client.post(self.url, {"action": "pay"}).status_code, 403)
        self.login(self.manager)
        self.assertEqual(self.client.post(self.url, {"action": "pay"}).status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "paid")

    def test_worker_legacy_permissions_do_not_bypass_role(self):
        self.worker.is_staff = True
        self.worker.save()
        self.worker.user_permissions.set(Permission.objects.all())
        self.login(self.worker)
        for url in ("/operator/", "/manager/", "/analytics/", "/calls/", "/kanban/", "/api/leads/board/", "/employees/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_operator_denied_management_and_admin(self):
        self.login(self.operator)
        for url in ("/manager/", "/analytics/", "/employees/"):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_csrf_required_for_worker_actions(self):
        browser = Browser(enforce_csrf_checks=True)
        browser.force_login(self.worker)
        self.assertEqual(browser.post(self.url, {"action": "start"}).status_code, 403)

    def test_unassigned_role_cannot_enter(self):
        self.worker.role = ""
        self.worker.save()
        self.login(self.worker)
        self.assertContains(self.client.get("/"), "Роль ещё не назначена", status_code=403)

    def test_worker_cannot_edit_fields(self):
        self.login(self.worker)
        self.assertEqual(self.client.post(self.url, {"action": "save", "employee": self.other.pk, "amount": "1"}).status_code, 403)

    def test_manager_creates_employee_without_elevating_admin(self):
        self.login(self.manager)
        response = self.client.post("/employees/", {
            "username": "new_worker", "role": "worker", "password": "Long-test-pass-8742",
            "is_active": "on", "max_active_leads": 5, "is_superuser": "on", "is_staff": "on",
        })
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username="new_worker")
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
        self.assertTrue(user.check_password("Long-test-pass-8742"))

    def test_operator_creates_lead_without_admin_redirect(self):
        from apps.leads.models import Lead
        self.login(self.operator)
        response = self.client.post("/workspace/lead/new/", {
            "title": "Created by operator", "client": self.order.client_id,
            "service": self.order.service_id, "source": "Телефон",
        })
        self.assertRedirects(response, "/operator/")
        self.assertTrue(Lead.objects.filter(title="Created by operator").exists())

    def test_assigned_lead_visible_only_to_its_worker(self):
        from apps.leads.models import Lead
        lead = Lead.objects.create(title="Assigned request", client=self.order.client, service=self.order.service, employee=self.worker, status="assigned")
        self.login(self.worker)
        self.assertContains(self.client.get("/my-orders/"), "Assigned request")
        self.assertContains(self.client.get(f"/my-assignments/{lead.pk}/"), "Private client")
        self.login(self.other)
        self.assertNotContains(self.client.get("/my-orders/"), "Assigned request")
        self.assertEqual(self.client.get(f"/my-assignments/{lead.pk}/").status_code, 404)

    def test_converted_lead_is_not_duplicated_in_worker_assignments(self):
        from apps.leads.models import Lead
        from apps.orders.services import convert_lead
        lead = Lead.objects.create(title="Converted request", client=self.order.client, service=self.order.service, employee=self.worker, status="assigned")
        order, _ = convert_lead(lead.pk)
        self.login(self.worker)
        response = self.client.get("/my-orders/")
        self.assertContains(response, f'/orders/{order.pk}/')
        self.assertNotContains(response, f'/my-assignments/{lead.pk}/')
