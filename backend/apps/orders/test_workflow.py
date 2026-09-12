from decimal import Decimal
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.customers.models import Client
from apps.services.models import Service
from apps.leads.models import Lead, TelegramNotice
from apps.leads.telegram import deliver_pending, apply_callback
from .models import Order
from .services import convert_lead, transition_order


class OrderWorkflowTests(TestCase):
    def setUp(self):
        self.employee = get_user_model().objects.create_user(
            username="master", is_staff=False, role="worker", telegram_chat_id=9988,
        )
        self.customer = Client.objects.create(name="Client", phone="+77000000000")
        self.service = Service.objects.create(name="Repair")
        self.lead = Lead.objects.create(title="Repair", client=self.customer, service=self.service, employee=self.employee)

    def test_conversion_is_idempotent(self):
        order, created = convert_lead(self.lead.pk)
        self.assertTrue(created)
        again, created = convert_lead(self.lead.pk)
        self.assertFalse(created)
        self.assertEqual(order.pk, again.pk)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(TelegramNotice.objects.filter(order=order).count(), 1)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.CONVERTED)
        self.assertEqual(order.client, self.customer)
        self.assertEqual(order.status, Order.Status.ASSIGNED)

    def test_conversion_without_master_starts_new(self):
        self.lead.employee = None
        self.lead.save()
        order, _ = convert_lead(self.lead.pk)
        self.assertEqual(order.status, Order.Status.NEW)
        self.assertFalse(TelegramNotice.objects.filter(order=order).exists())

    def test_rejected_lead_cannot_convert(self):
        self.lead.status = Lead.Status.LOST
        self.lead.save()
        with self.assertRaises(ValidationError):
            convert_lead(self.lead.pk)

    def test_work_then_payment(self):
        order, _ = convert_lead(self.lead.pk)
        with self.assertRaises(ValidationError):
            transition_order(order.pk, "pay")
        transition_order(order.pk, "start")
        transition_order(order.pk, "complete")
        with self.assertRaises(ValidationError):
            transition_order(order.pk, "pay")
        order.amount = Decimal("15000.00")
        order.save(update_fields=["amount"])
        result = transition_order(order.pk, "pay")
        self.assertEqual(result.status, Order.Status.PAID)
        self.assertIsNotNone(result.completed_at)
        self.assertIsNotNone(result.paid_at)
        with self.assertRaises(ValidationError):
            transition_order(order.pk, "pay")

    def test_cannot_skip_work(self):
        order, _ = convert_lead(self.lead.pk)
        with self.assertRaises(ValidationError):
            transition_order(order.pk, "complete")

    def test_old_lead_notification_invalidated(self):
        old = TelegramNotice.objects.create(lead=self.lead, employee=self.employee)
        convert_lead(self.lead.pk)
        old.refresh_from_db()
        self.assertFalse(old.active)

    def test_order_telegram_accept_finish_does_not_change_lead(self):
        order, _ = convert_lead(self.lead.pk)
        api = Mock()
        api.call.return_value = {"message_id": 44}
        deliver_pending(api)
        notice = TelegramNotice.objects.get(order=order)
        def callback(action):
            return {"data": f"{notice.pk.hex}:{action}", "from": {"id": 9988},
                    "message": {"message_id": 44, "chat": {"id": 9988, "type": "private"}}}
        self.assertIsNotNone(apply_callback(callback("accept"))[1])
        self.assertIsNotNone(apply_callback(callback("finish"))[1])
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.IN_PROGRESS)
        notice.amount_prompt_id = 55
        notice.save()
        from apps.leads.telegram import apply_amount_message
        apply_amount_message({"from": {"id": 9988}, "chat": {"id": 9988, "type": "private"}, "reply_to_message": {"message_id": 55}, "text": "0"})
        order.refresh_from_db()
        self.lead.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertIsNotNone(order.completed_at)
        self.assertIsNone(order.paid_at)
        self.assertEqual(self.lead.status, Lead.Status.CONVERTED)

    def test_notice_requires_exactly_one_target(self):
        order, _ = convert_lead(self.lead.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TelegramNotice.objects.create(lead=self.lead, order=order, employee=self.employee)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TelegramNotice.objects.create(employee=self.employee)

    def test_telegram_amount_validation_payment_and_replay(self):
        from apps.leads.telegram import process_update, apply_amount_message
        order, _ = convert_lead(self.lead.pk)
        transition_order(order.pk, "start", actor=self.employee)
        notice = TelegramNotice.objects.get(order=order)
        notice.state = "sent"
        notice.chat_id = 9988
        notice.message_id = 44
        notice.save()
        api = Mock()
        api.call.return_value = {"message_id": 55}
        process_update(api, {"callback_query": {"id": "cb", "data": f"{notice.pk.hex}:finish", "from": {"id":9988}, "message":{"message_id":44,"chat":{"id":9988,"type":"private"}}}})
        notice.refresh_from_db()
        self.assertEqual(notice.amount_prompt_id, 55)
        message = {"from":{"id":9988},"chat":{"id":9988,"type":"private"},"reply_to_message":{"message_id":55},"text":"15000,50"}
        for invalid in ("-1", "NaN", "Infinity", "1.001", "text"):
            apply_amount_message({**message,"text":invalid})
            order.refresh_from_db()
            self.assertEqual(order.status, "in_progress")
        self.assertIsNone(apply_amount_message({**message,"from":{"id":999}}))
        self.assertIsNone(apply_amount_message({**message,"reply_to_message":{"message_id":99}}))
        apply_amount_message(message)
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.received_amount, Decimal("15000.50"))
        self.assertIsNotNone(order.completed_at)
        self.assertIsNotNone(order.paid_at)
        count = order.events.count()
        apply_amount_message({**message,"text":"999"})
        order.refresh_from_db()
        self.assertEqual(order.amount, Decimal("15000.50"))
        self.assertEqual(order.events.count(), count)
