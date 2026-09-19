from decimal import Decimal
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from .models import User, MobileSession, WorkerServiceRate
from apps.customers.models import Client
from apps.services.models import Service
from apps.orders.models import Order
from apps.leads.models import TelegramNotice


class MobileAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.worker = User.objects.create_user(username="mobile", password="Test-mobile-pass-123", role="worker")
        self.other = User.objects.create_user(username="other-mobile", password="Test-mobile-pass-123", role="worker")
        self.client = APIClient()
        self.order = Order.objects.create(title="Repair", client=Client.objects.create(name="Client", phone="+77001234567"),
            service=Service.objects.create(name="Repair"), employee=self.worker, status="assigned")
        self.rate = WorkerServiceRate.objects.create(worker=self.worker, service=self.order.service, worker_percentage=40)
        self.login()

    def login(self):
        response = self.client.post("/api/mobile/login/", {"username": "mobile", "password": "Test-mobile-pass-123"})
        self.assertEqual(response.status_code, 200)
        self.token = response.data["token"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token)

    def action(self, action, order=None, **data):
        return self.client.post(f"/api/mobile/orders/{(order or self.order).pk}/action/",
                                {"action": action, **data}, format="json")

    def test_login_and_revocation(self):
        self.assertEqual(len(MobileSession.objects.get().token_hash), 64)
        self.assertNotEqual(MobileSession.objects.get().token_hash, self.token)
        self.assertEqual(self.client.get("/api/mobile/profile/").status_code, 200)
        self.assertEqual(self.client.post("/api/mobile/logout/").status_code, 204)
        self.assertEqual(self.client.get("/api/mobile/orders/").status_code, 401)

    def test_password_change_and_expiry_revoke_session(self):
        self.worker.set_password("Changed-password-123")
        self.worker.save()
        self.assertEqual(self.client.get("/api/mobile/orders/").status_code, 401)
        self.worker.set_password("Test-mobile-pass-123")
        self.worker.save()
        self.login()
        MobileSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.get("/api/mobile/orders/").status_code, 401)

    def test_worker_only_and_ownership(self):
        self.order.employee = self.other
        self.order.save()
        self.assertEqual(self.client.get("/api/mobile/orders/").data["count"], 0)
        self.assertEqual(self.client.get(f"/api/mobile/orders/{self.order.pk}/").status_code, 404)
        self.assertEqual(self.action("start").status_code, 404)
        self.worker.role = "operator"
        self.worker.save()
        self.assertEqual(self.client.get("/api/mobile/orders/").status_code, 403)
        self.client.credentials()
        self.assertNotEqual(self.client.post("/api/mobile/login/", {"username": "mobile", "password": "Test-mobile-pass-123"}).status_code, 200)

    def test_contacts_hidden_until_accepted(self):
        url = f"/api/mobile/orders/{self.order.pk}/"
        self.assertIsNone(self.client.get(url).data["phone"])
        self.assertEqual(self.action("start").status_code, 200)
        self.assertEqual(self.client.get(url).data["phone"], "+77001234567")
        self.assertEqual(self.action("start").status_code, 409)

    def test_paid_completion_preview_and_replay(self):
        self.action("start")
        invalid = self.action("preview", amount="100", expenses="200", comment="Repair")
        self.assertEqual(invalid.status_code, 400)
        preview = self.action("preview", amount="1000", expenses="100", comment="Repair")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.data["worker_share"], "360.00")
        self.assertEqual(self.action("complete", confirmation=preview.data["confirmation"] + "x").status_code, 409)
        response = self.action("complete", confirmation=preview.data["confirmation"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "paid")
        self.assertEqual(self.action("complete", confirmation=preview.data["confirmation"]).status_code, 409)
        self.assertEqual(self.client.get("/api/mobile/orders/?scope=history").data["count"], 1)
        self.assertEqual(self.client.get("/api/mobile/orders/").data["count"], 0)

    def test_percentage_change_invalidates_preview(self):
        self.action("start")
        preview = self.action("preview", amount="100", expenses="0", comment="Done")
        self.rate.worker_percentage = 50
        self.rate.save()
        self.assertEqual(self.action("complete", confirmation=preview.data["confirmation"]).status_code, 409)

    def test_free_and_repeat_completion_cannot_be_paid(self):
        for repeat in (False, True):
            order = Order.objects.create(title="Free", client=self.order.client, service=self.order.service,
                employee=self.worker, status="in_progress", repeat_of=self.order if repeat else None)
            preview = self.action("preview", order=order, amount="0", expenses="0", comment="Free repair")
            self.assertEqual(self.action("complete", order=order, confirmation=preview.data["confirmation"]).status_code, 200)
            order.refresh_from_db()
            self.assertTrue(order.completed_free)
            self.assertEqual(self.action("payment", order=order, received_amount="100", received_method="cash").status_code, 409)

    def test_reject_returns_order_and_cancels_notice(self):
        notice = TelegramNotice.objects.create(order=self.order, employee=self.worker)
        self.assertEqual(self.action("reject").status_code, 200)
        self.order.refresh_from_db()
        notice.refresh_from_db()
        self.assertEqual(self.order.status, "new")
        self.assertIsNone(self.order.employee_id)
        self.assertFalse(notice.active)

    def test_legacy_payment(self):
        self.order.status = "completed"
        self.order.save()
        self.assertEqual(self.action("payment", received_amount="500", received_method="cash").status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.amount, Decimal("500"))
        self.assertEqual(self.order.status, "paid")

    def test_defer_keeps_assignment_and_active_list(self):
        self.action("start")
        before = timezone.now()
        response = self.action("defer_1")
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.employee_id, self.worker.pk)
        self.assertEqual(self.order.status, "in_progress")
        notice = TelegramNotice.objects.get(order=self.order, active=True)
        self.assertGreaterEqual(notice.reminder_at, before + timedelta(days=1))
        self.assertLessEqual(notice.reminder_at, timezone.now() + timedelta(days=1))
        self.assertIsNotNone(response.data["deferred_until"])
        active = self.client.get("/api/mobile/orders/").data
        self.assertEqual(active["count"], 1)
        self.assertIsNotNone(active["results"][0]["deferred_until"])
        self.assertEqual(self.client.get("/api/mobile/orders/?scope=history").data["count"], 0)
        self.assertEqual(self.action("defer_1").status_code, 200)
        self.assertEqual(TelegramNotice.objects.filter(order=self.order, active=True).count(), 1)
        from unittest.mock import Mock
        from apps.leads.telegram import deliver_pending
        deliver_pending(Mock())
        notice.refresh_from_db()
        self.assertTrue(notice.active)
        preview = self.action("preview", amount="1000", expenses="100", comment="Done")
        completed = self.action("complete", confirmation=preview.data["confirmation"])
        self.assertEqual(completed.status_code, 200)
        self.assertIsNone(completed.data["deferred_until"])
        self.assertEqual(self.client.get("/api/mobile/orders/?scope=history").data["count"], 1)

    def test_defer_rejects_wrong_status_and_other_worker(self):
        for status in ("assigned", "new", "completed", "paid", "cancelled"):
            self.order.status = status
            self.order.save()
            self.assertEqual(self.action("defer_1").status_code, 409)
        self.order.status = "in_progress"
        self.order.employee = self.other
        self.order.save()
        self.assertEqual(self.action("defer_1").status_code, 404)
        self.assertFalse(TelegramNotice.objects.filter(order=self.order).exists())

    def test_repeat_defer_and_telegram_share_reminder(self):
        from apps.leads.telegram import apply_callback
        self.worker.telegram_chat_id = 9988
        self.worker.save()
        repeat = Order.objects.create(title="Repeat", client=self.order.client, service=self.order.service,
            employee=self.worker, status="in_progress", repeat_of=self.order)
        notice = TelegramNotice.objects.create(order=repeat, employee=self.worker, state="sent",
            chat_id=9988, message_id=44, payment_step="amount", amount_prompt_id=45)
        response = self.action("defer_1", order=repeat)
        self.assertEqual(response.status_code, 200)
        notice.refresh_from_db()
        self.assertEqual(notice.payment_step, "")
        self.assertIsNone(notice.amount_prompt_id)
        callback = {"data": f"{notice.pk.hex}:defer_1", "from": {"id": 9988},
                    "message": {"message_id": 44, "chat": {"id": 9988, "type": "private"}}}
        self.assertIsNotNone(apply_callback(callback)[1])
        detail = self.client.get(f"/api/mobile/orders/{repeat.pk}/")
        self.assertIsNotNone(detail.data["deferred_until"])
        self.assertEqual(detail.data["actions"], ["complete", "defer_1"])
