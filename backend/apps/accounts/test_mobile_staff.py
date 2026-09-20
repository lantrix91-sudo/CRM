from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient
from .models import User
from apps.customers.models import City, Client
from apps.services.models import Service
from apps.orders.models import Order
from apps.leads.models import Lead, TelegramNotice


class MobileStaffTests(TestCase):
    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.operator = User.objects.create_user(username="staff-operator", password="test-pass", role="operator")
        self.manager = User.objects.create_user(username="staff-manager", password="test-pass", role="manager")
        self.worker = User.objects.create_user(username="staff-worker", password="test-pass", role="worker", is_available=True)
        self.city = City.objects.create(name="City")
        self.service = Service.objects.create(name="Repair")
        self.worker.services.add(self.service)
        self.worker.service_cities.add(self.city)
        self.customer = Client.objects.create(name="Client", phone="+77001234567")
        self.order = Order.objects.create(title="Repair", client=self.customer, service=self.service, city=self.city)

    def login(self, user):
        self.api.credentials()
        result = self.api.post("/api/mobile/login/", {"username": user.username, "password": "test-pass"})
        self.assertEqual(result.status_code, 200)
        self.api.credentials(HTTP_AUTHORIZATION="Bearer " + result.data["token"])
        return result

    def post(self, path, data):
        return self.api.post("/api/mobile/staff/" + path, data, format="json")

    def test_all_roles_and_superuser_login(self):
        for user in (self.worker, self.operator, self.manager):
            self.assertEqual(self.login(user).data["role"], user.role)
            self.assertEqual(self.api.get("/api/mobile/me/").data["role"], user.role)
        admin = User.objects.create_superuser(username="staff-admin", password="test-pass")
        self.assertEqual(self.login(admin).data["role"], "manager")
        self.assertEqual(self.api.get("/api/mobile/staff/dashboard/").status_code, 200)

    def test_staff_and_worker_isolation(self):
        self.login(self.worker)
        for path in ("leads/", "orders/", "clients/", "options/", "dashboard/", "team/"):
            self.assertEqual(self.api.get("/api/mobile/staff/" + path).status_code, 403)
        self.assertEqual(self.post(f"orders/{self.order.pk}/", {"action": "assign"}).status_code, 403)
        self.login(self.operator)
        self.assertEqual(self.api.get("/api/mobile/orders/").status_code, 403)
        self.assertEqual(self.api.post(f"/api/mobile/orders/{self.order.pk}/action/", {"action": "complete"}, format="json").status_code, 403)
        for path in ("dashboard/", "team/"):
            self.assertEqual(self.api.get("/api/mobile/staff/" + path).status_code, 403)
        self.assertEqual(self.post(f"team/{self.worker.pk}/availability/", {"available": False}).status_code, 403)

    def test_role_change_and_inactive_session(self):
        self.login(self.manager)
        self.manager.role = "worker"
        self.manager.save()
        self.assertEqual(self.api.get("/api/mobile/staff/dashboard/").status_code, 403)
        self.assertEqual(self.api.get("/api/mobile/me/").data["role"], "worker")
        self.manager.is_active = False
        self.manager.save()
        self.assertEqual(self.api.get("/api/mobile/me/").status_code, 401)

    def test_create_convert_assign_return_cancel(self):
        self.login(self.operator)
        payload = {"client_name": "New client", "client_phone": "+77001234568", "city": self.city.pk,
                   "service": self.service.pk, "scheduled_at": "2030-01-01 14:00"}
        created = self.post("leads/", payload)
        self.assertEqual(created.status_code, 201, created.data)
        lead = Lead.objects.get(pk=created.data["id"])
        self.assertEqual(lead.events.first().actor, self.operator)
        payload.update(action="save", comment="Updated")
        self.assertEqual(self.post(f"leads/{lead.pk}/", payload).status_code, 200)
        converted = self.post(f"leads/{lead.pk}/", {"action": "convert"})
        self.assertEqual(converted.status_code, 200, converted.data)
        pk = converted.data["id"]
        self.assertEqual(self.post(f"leads/{lead.pk}/", payload).status_code, 409)
        assigned = self.post(f"orders/{pk}/", {"action": "assign", "employee_id": self.worker.pk})
        self.assertEqual(assigned.status_code, 200, assigned.data)
        self.assertEqual(assigned.data["employee_id"], self.worker.pk)
        self.assertTrue(TelegramNotice.objects.filter(order_id=pk, active=True).exists())
        self.assertEqual(self.post(f"orders/{pk}/", {"action": "assign"}).status_code, 409)
        self.assertEqual(self.post(f"orders/{pk}/", {"action": "return", "reason": "Busy", "expected_notice": "stale"}).status_code, 409)
        returned = self.post(f"orders/{pk}/", {"action": "return", "reason": "Busy", "expected_notice": assigned.data["assignment_notice"]})
        self.assertEqual(returned.status_code, 200)
        self.assertIsNone(returned.data["employee_id"])
        self.assertEqual(self.post(f"orders/{pk}/", {"action": "cancel", "reason": "Client declined"}).status_code, 200)
        self.assertTrue(Order.objects.get(pk=pk).events.filter(description__contains="Client declined").exists())

    def test_invalid_inputs_and_inactive_city(self):
        self.login(self.operator)
        self.assertEqual(self.post("leads/", {}).status_code, 400)
        self.assertEqual(self.post("leads/", []).status_code, 400)
        self.city.is_active = False
        self.city.save()
        self.assertEqual(self.post("leads/", {"client_name": "Client", "client_phone": "+77001234567", "city": self.city.pk}).status_code, 400)
        self.assertEqual(self.post(f"orders/{self.order.pk}/", {"action": "assign", "employee_id": "bad"}).status_code, 400)
        self.assertEqual(self.post(f"orders/{self.order.pk}/", {"action": "complete"}).status_code, 400)
        self.assertEqual(self.post(f"orders/{self.order.pk}/", {"action": "cancel"}).status_code, 409)

    def test_search_history_and_contacts(self):
        self.login(self.operator)
        response = self.api.get("/api/mobile/staff/orders/")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["phone"], self.customer.phone)
        self.assertEqual(self.api.get("/api/mobile/staff/orders/?q=nothing").data["count"], 0)
        self.order.status = "paid"
        self.order.save()
        self.assertEqual(self.api.get("/api/mobile/staff/orders/").data["count"], 0)
        self.assertEqual(self.api.get("/api/mobile/staff/orders/?scope=history").data["count"], 1)
        self.assertEqual(self.api.get("/api/mobile/staff/clients/?q=Client").data["count"], 1)

    def test_manager_dashboard_team_availability(self):
        self.login(self.manager)
        self.assertEqual(self.api.get("/api/mobile/staff/dashboard/").data["unassigned"], 1)
        self.assertEqual(self.api.get("/api/mobile/staff/team/").data["count"], 1)
        response = self.post(f"team/{self.worker.pk}/availability/", {"available": False})
        self.assertEqual(response.status_code, 200)
        self.worker.refresh_from_db()
        self.assertFalse(self.worker.is_available)
        self.assertEqual(self.post(f"team/{self.worker.pk}/availability/", {"available": "invalid"}).status_code, 400)

    def test_unknown_role_and_anonymous_denied(self):
        self.assertEqual(self.api.get("/api/mobile/staff/orders/").status_code, 401)
        curator = User.objects.create_user(username="staff-curator", password="test-pass", role="curator")
        self.assertEqual(self.api.post("/api/mobile/login/", {"username": curator.username, "password": "test-pass"}).status_code, 403)
