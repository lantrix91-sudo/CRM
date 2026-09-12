from django.test import TestCase
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.customers.models import Client
from apps.services.models import Service
from apps.orders.models import Order
from apps.leads.models import Lead


class WorkflowBoardTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(username="operator", role="operator")
        self.worker = User.objects.create_user(username="worker", role="worker", is_available=True)
        self.manager = User.objects.create_user(username="manager", role="manager")
        self.service = Service.objects.create(name="Repair")
        self.worker.services.add(self.service)
        self.customer = Client.objects.create(name="Client", phone="123")
        self.lead = Lead.objects.create(title="Repair", service=self.service, client=self.customer)
        self.api = APIClient(enforce_csrf_checks=True)
        self.api.force_login(self.operator)
        self.api.get("/kanban/")
        self.csrf = self.api.cookies["csrftoken"].value

    def post(self, url, data):
        return self.api.post(url, data, HTTP_X_CSRFTOKEN=self.csrf)

    def card(self):
        return self.api.get("/api/leads/board/").data["leads"][0]

    def test_full_lifecycle_and_history(self):
        self.assertEqual(self.card()["status"], "new")
        url = f"/workspace/lead/{self.lead.pk}/"
        self.assertEqual(self.post(url, {"action": "convert", "return_to": "kanban"}).url, "/kanban/")
        order = Order.objects.get(lead=self.lead)
        self.post(url, {"action": "convert"})
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(self.card()["status"], "in_progress")
        response = self.api.post(f"/api/orders/{order.pk}/assign/", {"employee_id": self.worker.pk}, format="json", HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.card()["status"], "assigned")
        order_url = f"/orders/{order.pk}/"
        self.assertEqual(self.post(order_url, {"action": "complete"}).status_code, 403)
        self.api.force_login(self.worker)
        self.assertContains(self.api.get("/my-orders/"), order_url)
        self.post(order_url, {"action": "start"})
        self.post(order_url, {"action": "complete"})
        self.api.force_login(self.operator)
        self.assertEqual(self.card()["status"], "completed")
        self.assertEqual(self.post(order_url, {"action": "pay"}).status_code, 403)
        order.refresh_from_db()
        order.amount = 100
        order.save()
        self.api.force_login(self.manager)
        self.post(order_url, {"action": "pay"})
        self.assertEqual(self.card()["status"], "paid")
        self.assertEqual(list(order.events.values_list("actor_id", flat=True)), [self.operator.pk, self.operator.pk, self.worker.pk, self.worker.pk, self.manager.pk])

    def test_old_status_does_not_fabricate_consent(self):
        self.lead.status = "won"
        self.lead.save()
        self.assertEqual(self.card()["status"], "new")
        self.assertEqual(Order.objects.count(), 0)

    def test_cannot_change_stage_arbitrarily(self):
        response = self.api.patch(f"/api/leads/{self.lead.pk}/status/", {"status": "won", "expected_status": "new"}, format="json", HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 409)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "new")

    def test_permissions_and_csrf(self):
        self.assertEqual(self.api.post(f"/workspace/lead/{self.lead.pk}/", {"action": "convert"}).status_code, 403)
        self.api.force_login(self.worker)
        self.assertEqual(self.api.get("/api/leads/board/").status_code, 403)
        self.assertEqual(self.post(f"/workspace/lead/{self.lead.pk}/", {"action": "convert"}).status_code, 403)

    def test_assignment_explains_missing_skill(self):
        from apps.orders.services import assign_order
        from django.core.exceptions import ValidationError
        order = Order.objects.create(title="Cleaning", service=self.service, client=self.customer)
        self.worker.services.clear()
        with self.assertRaisesMessage(ValidationError, "Нет активного мастера с услугой"):
            assign_order(order.pk, None, self.operator)
        order.refresh_from_db()
        self.assertIsNone(order.employee_id)
        self.worker.services.add(self.service)
        self.assertEqual(assign_order(order.pk, None, self.operator).employee_id, self.worker.pk)
