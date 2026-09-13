from decimal import Decimal
import uuid
from django.test import TestCase
from django.core.exceptions import ValidationError, PermissionDenied
from apps.accounts.models import User
from apps.customers.models import Client
from apps.services.models import Service
from .models import Order, WorkerTransfer, SettlementShift
from .settlements import settle, totals

class SettlementTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(username="manager", role="manager")
        self.worker = User.objects.create_user(username="worker", role="worker", percentage=40)
        self.order = Order.objects.create(title="Repair", client=Client.objects.create(name="Client", phone="123"),
            service=Service.objects.create(name="Repair"), employee=self.worker, status="paid", amount=20000, expenses=2000, worker_percentage=40)

    def test_partial_transfer_close_and_carry(self):
        summary = totals(self.worker)
        self.assertEqual(summary["current_shift_gross"], Decimal("20000"))
        self.assertEqual(summary["current_shift_worker_share"], Decimal("7200"))
        self.assertEqual(summary["current_shift_company_share"], Decimal("10800"))
        self.assertEqual(summary["carried_debt"], Decimal("0"))
        self.assertEqual(summary["current_shift_debt"], Decimal("10800"))
        self.assertEqual(summary["total_balance_due"], Decimal("10800"))
        token = uuid.uuid4()
        settle(self.worker.pk, self.manager, "transfer", Decimal("8000"), request_id=token)
        settle(self.worker.pk, self.manager, "transfer", Decimal("8000"), request_id=token)
        self.assertEqual(WorkerTransfer.objects.count(), 1)
        settle(self.worker.pk, self.manager, "close")
        summary = totals(self.worker)
        self.assertEqual(summary["current_shift_company_share"], Decimal("0"))
        self.assertEqual(summary["current_shift_worker_share"], Decimal("0"))
        self.assertEqual(summary["carried_debt"], Decimal("2800"))
        self.assertEqual(summary["current_shift_debt"], Decimal("0"))
        self.assertEqual(summary["total_balance_due"], Decimal("2800"))
        with self.assertRaises(ValidationError):
            settle(self.worker.pk, self.manager, "close")
        settle(self.worker.pk, self.manager, "transfer", Decimal("2800"), request_id=uuid.uuid4())
        summary = totals(self.worker)
        self.assertEqual(summary["carried_debt"], Decimal("0"))
        self.assertEqual(summary["total_balance_due"], Decimal("0"))
        self.assertEqual(SettlementShift.objects.get().balance, 2800)
        self.order.refresh_from_db()
        self.assertEqual(self.order.amount, 20000)

    def test_new_order_is_current_and_carried_debt_is_not_double_counted(self):
        settle(self.worker.pk, self.manager, "transfer", Decimal("4300"), request_id=uuid.uuid4())
        settle(self.worker.pk, self.manager, "close")

        new_order = Order.objects.create(
            title="New repair", client=self.order.client, service=self.order.service,
            employee=self.worker, status="paid", amount=15000, worker_percentage=40,
        )
        summary = totals(self.worker)
        self.assertEqual(summary["carried_debt"], Decimal("6500"))
        self.assertEqual(summary["current_shift_gross"], Decimal("15000"))
        self.assertEqual(summary["current_shift_company_share"], Decimal("9000"))
        self.assertEqual(summary["current_shift_debt"], Decimal("9000"))
        self.assertEqual(summary["total_balance_due"], Decimal("15500"))
        self.assertEqual(summary["total_transferred_all_time"], Decimal("4300"))

        settle(self.worker.pk, self.manager, "transfer", Decimal("6500"), request_id=uuid.uuid4())
        summary = totals(self.worker)
        self.assertEqual(summary["carried_debt"], Decimal("0"))
        self.assertEqual(summary["current_shift_debt"], Decimal("9000"))
        self.assertEqual(summary["total_balance_due"], Decimal("9000"))
        self.assertEqual(new_order.settlement_shift_id, None)

    def test_closing_fully_paid_shift_starts_without_carried_debt(self):
        settle(self.worker.pk, self.manager, "transfer", Decimal("10800"), request_id=uuid.uuid4())
        settle(self.worker.pk, self.manager, "close")
        summary = totals(self.worker)
        self.assertEqual(summary["carried_debt"], Decimal("0"))
        self.assertEqual(summary["total_balance_due"], Decimal("0"))
        self.assertEqual(SettlementShift.objects.get().balance, Decimal("0"))

    def test_validation_and_access(self):
        with self.assertRaises(PermissionDenied):
            settle(self.worker.pk, self.worker, "close")
        with self.assertRaises(ValidationError):
            settle(self.worker.pk, self.manager, "transfer", Decimal("20000"), request_id=uuid.uuid4())
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get("/settlements/").status_code, 200)
        self.assertEqual(self.client.post(f"/settlements/{self.worker.pk}/", {"action":"close"}).status_code, 403)
        other = User.objects.create_user(username="other", role="worker")
        self.assertEqual(self.client.get(f"/settlements/{other.pk}/").status_code, 403)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(f"/settlements/{self.worker.pk}/", {"action":"transfer", "amount":"8000", "request_id":str(uuid.uuid4())}).status_code, 302)
        self.assertEqual(totals(self.worker)["balance"], 2800)
        self.order.worker_percentage = None
        self.order.save()
        with self.assertRaises(ValidationError):
            settle(self.worker.pk, self.manager, "close")
