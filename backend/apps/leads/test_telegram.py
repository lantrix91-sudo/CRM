from unittest.mock import Mock
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.db import transaction
from apps.customers.models import Client
from apps.services.models import Service
from .models import Lead, TelegramNotice
from .assignment import assign_lead
from .telegram import apply_callback, deliver_pending, process_update, TelegramError


class TelegramTests(TestCase):
    def setUp(self):
        self.employee = get_user_model().objects.create_user(
            username="telegram_worker", is_staff=False, role="worker", is_available=True, telegram_chat_id=12345,
        )
        self.service = Service.objects.create(name="Repair")
        self.employee.services.add(self.service)
        self.customer = Client.objects.create(name="Ivan", phone="+77000000000", district="Centre")
        self.lead = Lead.objects.create(title="Repair", client=self.customer, service=self.service)
        assign_lead(self.lead.pk)
        self.notice = TelegramNotice.objects.get(lead=self.lead)
        self.api = Mock()
        self.api.call.return_value = {"message_id": 42}

    def callback(self, action, sender=12345):
        return {"id": "callback", "data": f"{self.notice.pk.hex}:{action}",
                "from": {"id": sender},
                "message": {"message_id": 42, "chat": {"id": sender, "type": "private"}}}

    def deliver(self):
        deliver_pending(self.api)
        self.notice.refresh_from_db()

    def test_assignment_queues_and_sends_details(self):
        self.deliver()
        self.assertEqual(self.notice.state, "sent")
        kwargs = self.api.call.call_args.kwargs
        self.assertEqual(kwargs["chat_id"], 12345)
        for text in ("Repair",):
            self.assertIn(text, kwargs["text"])
        self.assertIn("Ivan", kwargs["text"])
        self.assertNotIn("+77000000000", kwargs["text"])
        self.assertEqual(len(kwargs["reply_markup"]["inline_keyboard"][0]), 2)

    def test_accept_then_finish(self):
        self.deliver()
        text, notice, markup = apply_callback(self.callback("accept"))
        self.assertIsNotNone(notice)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.IN_PROGRESS)
        self.assertIn("finish", markup["inline_keyboard"][0][0]["callback_data"])
        apply_callback(self.callback("finish"))
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.WON)
        self.assertIsNone(apply_callback(self.callback("finish"))[1])

    def test_cannot_finish_before_accept(self):
        self.deliver()
        self.assertIsNone(apply_callback(self.callback("finish"))[1])
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.ASSIGNED)

    def test_reject_clears_employee(self):
        self.deliver()
        apply_callback(self.callback("reject"))
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.employee_id)
        self.assertEqual(self.lead.status, Lead.Status.NEW)
        self.assertIsNone(apply_callback(self.callback("accept"))[1])

    def test_foreign_chat_and_stale_assignment_denied(self):
        self.deliver()
        self.assertIsNone(apply_callback(self.callback("accept", sender=999))[1])
        apply_callback(self.callback("reject"))
        assign_lead(self.lead.pk)
        self.assertIsNone(apply_callback(self.callback("accept"))[1])

    def test_deactivated_employee_denied(self):
        self.deliver()
        self.employee.is_active = False
        self.employee.save()
        self.assertIsNone(apply_callback(self.callback("accept"))[1])

    def test_retry_and_no_duplicate_after_success(self):
        self.api.call.side_effect = TelegramError("Offline")
        self.deliver()
        self.assertEqual(self.notice.state, "pending")
        self.assertEqual(self.notice.attempts, 1)

    def test_sent_notice_is_not_resent(self):
        self.deliver()
        self.api.reset_mock()
        deliver_pending(self.api)
        self.api.call.assert_not_called()

    def test_missing_chat_waits(self):
        self.employee.telegram_chat_id = None
        self.employee.save()
        self.deliver()
        self.api.call.assert_not_called()
        self.assertEqual(self.notice.state, "pending")

    def test_superseded_pending_notice_cancelled(self):
        self.lead.status = Lead.Status.WON
        self.lead.save()
        self.deliver()
        self.api.call.assert_not_called()
        self.assertEqual(self.notice.state, "cancelled")

    def test_start_returns_only_own_id(self):
        process_update(self.api, {"message": {"text": "/start", "chat": {"id": 99, "type": "private"}}})
        self.assertIn("99", self.api.call.call_args.kwargs["text"])
        self.assertNotIn("Ivan", self.api.call.call_args.kwargs["text"])

    def test_assignment_queue_rolls_back(self):
        another = Lead.objects.create(title="Another", client=self.customer, service=self.service)
        with transaction.atomic():
            assign_lead(another.pk)
            transaction.set_rollback(True)
        self.assertFalse(TelegramNotice.objects.filter(lead=another).exists())


class TelegramLoggingTests(TestCase):
    def test_api_error_logs_category_without_secret(self):
        import io
        from urllib.error import HTTPError
        from unittest.mock import patch
        from .telegram import TelegramAPI
        token = "private-test-token"
        error = HTTPError("https://api.telegram.org/bot" + token, 400, "Bad Request", {}, io.BytesIO(b'{"description":"Bad Request: chat not found private-test-token"}'))
        with patch("apps.leads.telegram.urlopen", side_effect=error):
            with self.assertLogs("crm.telegram", level="WARNING") as captured:
                with self.assertRaises(TelegramError):
                    TelegramAPI(token).call("getChat", chat_id=123)
        output = " ".join(captured.output)
        self.assertIn("chat_not_found", output)
        self.assertNotIn(token, output)
        self.assertNotIn("https://", output)
