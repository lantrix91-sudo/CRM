from django.test import TestCase, Client as Browser
from apps.accounts.models import User
from apps.customers.models import Client
from apps.services.models import Service
from apps.orders.models import Order


class ReceivedPaymentTests(TestCase):
    def setUp(self):
        self.worker = User.objects.create_user(username="worker", role="worker")
        self.manager = User.objects.create_user(username="manager", role="manager")
        self.order = Order.objects.create(title="Repair", client=Client.objects.create(name="Client", phone="123"), service=Service.objects.create(name="Repair"), employee=self.worker, status="completed", amount=100)
        self.url = f"/orders/{self.order.pk}/"
        self.client.force_login(self.worker)

    def report(self, **overrides):
        return self.client.post(self.url, {"action": "received_payment", "received_amount": "100.00", "received_method": "cash", **overrides})

    def test_worker_payment_closes_order(self):
        self.assertContains(self.client.get(self.url), "Получил оплату")
        self.assertEqual(self.report().status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "paid")
        self.assertEqual(self.order.amount, 100)
        self.assertIsNotNone(self.order.paid_at)
        self.assertEqual(self.order.received_amount, 100)
        self.assertEqual(self.order.received_method, "cash")
        self.assertEqual(set(self.order.events.values_list("actor_id", flat=True)), {self.worker.pk})
        self.report(received_amount="200")
        self.order.refresh_from_db()
        self.assertEqual(self.order.received_amount, 100)
        self.assertEqual(self.order.events.count(), 2)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(self.url), "Наличные")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "paid")

    def test_invalid_data_and_stage(self):
        for data in ({"received_amount":"0"}, {"received_amount":"-1"}, {"received_amount":"NaN"}, {"received_amount":"1.001"}, {"received_method":"invalid"}):
            self.report(**data)
            self.order.refresh_from_db()
            self.assertIsNone(self.order.received_at)
        self.order.status = "assigned"
        self.order.save()
        self.report()
        self.order.refresh_from_db()
        self.assertIsNone(self.order.received_at)

    def test_permissions_and_csrf(self):
        other = User.objects.create_user(username="other", role="worker")
        self.client.force_login(other)
        self.assertEqual(self.report().status_code, 404)
        self.client.force_login(self.manager)
        self.assertEqual(self.report().status_code, 403)
        browser = Browser(enforce_csrf_checks=True)
        browser.force_login(self.worker)
        self.assertEqual(browser.post(self.url, {"action":"received_payment"}).status_code, 403)

    def test_paid_orders_move_to_private_history(self):
        self.assertContains(self.client.get("/my-orders/"), self.url)
        self.report()
        self.assertNotContains(self.client.get("/my-orders/"), self.url)
        self.assertContains(self.client.get("/my-orders/history/"), self.url)
        other = User.objects.create_user(username="history_other", role="worker")
        self.client.force_login(other)
        self.assertNotContains(self.client.get("/my-orders/history/"), self.url)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get("/my-orders/history/").status_code, 403)
