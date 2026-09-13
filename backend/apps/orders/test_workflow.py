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
            username="master", is_staff=False, role="worker", telegram_chat_id=9988, percentage=30,
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
        apply_amount_message({"from": {"id": 9988}, "chat": {"id": 9988, "type": "private"}, "reply_to_message": {"message_id": 55}, "text": "0\n0\nRepair"})
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
        notice.payment_step = ""  # Legacy three-line prompts remain supported.
        notice.save(update_fields=("payment_step",))
        message = {"from":{"id":9988},"chat":{"id":9988,"type":"private"},"reply_to_message":{"message_id":55},"text":"15000,50\n0\nRepair"}
        for invalid in ("-1", "NaN", "Infinity", "1.001", "text"):
            apply_amount_message({**message,"text":invalid})
            order.refresh_from_db()
            self.assertEqual(order.status, "in_progress")
        self.assertIsNone(apply_amount_message({**message,"from":{"id":999}}))
        self.assertIsNone(apply_amount_message({**message,"reply_to_message":{"message_id":99}}))
        api.reset_mock()
        process_update(api, {"message": message})
        edit = next(call for call in api.call.call_args_list if call.args[0] == "editMessageText")
        self.assertEqual(edit.kwargs["chat_id"], 9988)
        self.assertEqual(edit.kwargs["message_id"], 44)
        self.assertTrue(edit.kwargs["text"].startswith("✅ Выполнено\n"))
        self.assertIn(f"Заказ № {order.pk}", edit.kwargs["text"])
        self.assertEqual(edit.kwargs["reply_markup"], {"inline_keyboard": []})
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

    def test_completion_removes_keyboard_only_after_valid_amount(self):
        from apps.leads.telegram import process_update, TelegramError
        order, _ = convert_lead(self.lead.pk)
        transition_order(order.pk, "start", actor=self.employee)
        notice = TelegramNotice.objects.get(order=order)
        notice.state, notice.chat_id, notice.message_id, notice.amount_prompt_id = "sent", 9988, 44, 55
        notice.save()
        message = {"from": {"id": 9988}, "chat": {"id": 9988, "type": "private"},
                   "reply_to_message": {"message_id": 55}, "text": "invalid"}
        api = Mock()
        process_update(api, {"message": message})
        self.assertFalse(any(call.args[0] == "editMessageText" for call in api.call.call_args_list))
        api.reset_mock()
        def transport(method, **kwargs):
            if method == "editMessageText":
                raise TelegramError("Unavailable")
        api.call.side_effect = transport
        process_update(api, {"message": {**message, "text": "0\n0\nRepair"}})
        edit = next(call for call in api.call.call_args_list if call.args[0] == "editMessageText")
        self.assertEqual(edit.kwargs["chat_id"], 9988)
        self.assertEqual(edit.kwargs["message_id"], 44)
        self.assertTrue(edit.kwargs["text"].startswith("✅ Выполнено\n"))
        self.assertIn(f"Заказ № {order.pk}", edit.kwargs["text"])
        self.assertEqual(edit.kwargs["reply_markup"], {"inline_keyboard": []})
        self.assertEqual(api.call.call_args.args[0], "sendMessage")
        order.refresh_from_db()
        self.assertEqual(order.status, "completed")

    def accepted_notice(self):
        order, _ = convert_lead(self.lead.pk)
        transition_order(order.pk, "start", actor=self.employee)
        notice = TelegramNotice.objects.get(order=order)
        notice.state, notice.chat_id, notice.message_id = "sent", 9988, 44
        notice.save()
        return order, notice

    def callback_for(self, notice, action):
        return {"id": "cb", "data": f"{notice.pk.hex}:{action}", "from": {"id": 9988},
                "message": {"message_id": 44, "chat": {"id": 9988, "type": "private"}}}

    def test_accepted_order_refusals(self):
        for action, status in (("worker_reject", "new"), ("client_reject", "cancelled")):
            with self.subTest(action=action):
                order, notice = self.accepted_notice()
                text, updated, markup = apply_callback(self.callback_for(notice, action))
                self.assertIsNotNone(updated)
                self.assertEqual(markup, {"inline_keyboard": []})
                order.refresh_from_db()
                notice.refresh_from_db()
                self.assertEqual(order.status, status)
                self.assertFalse(notice.active)
                if action == "worker_reject":
                    self.assertIsNone(order.employee_id)
                else:
                    self.assertIsNotNone(order.cancelled_at)
                self.assertIsNone(apply_callback(self.callback_for(notice, action))[1])
                order.delete()

    def test_defer_and_due_reminder(self):
        from datetime import timedelta
        from django.utils import timezone
        from apps.leads.telegram import deliver_reminders, TelegramError
        order, notice = self.accepted_notice()
        for days in (1, 2):
            before = timezone.now()
            self.assertIsNotNone(apply_callback(self.callback_for(notice, f"defer_{days}"))[1])
            notice.refresh_from_db()
            self.assertGreaterEqual(notice.reminder_at, before + timedelta(days=days))
        api = Mock()
        deliver_reminders(api)
        api.call.assert_not_called()
        notice.reminder_at = timezone.now() - timedelta(seconds=1)
        notice.save()
        api.call.side_effect = TelegramError("offline")
        deliver_reminders(api)
        notice.refresh_from_db()
        self.assertIsNotNone(notice.reminder_at)
        notice.reminder_at = timezone.now() - timedelta(seconds=1)
        notice.save()
        api.reset_mock()
        api.call.side_effect = None
        deliver_reminders(api)
        api.call.assert_called_once()
        deliver_reminders(api)
        api.call.assert_called_once()
        notice.refresh_from_db()
        self.assertIsNone(notice.reminder_at)
        notice.reminder_at = timezone.now() - timedelta(seconds=1)
        notice.save()
        transition_order(order.pk, "complete", actor=self.employee)
        api.reset_mock()
        deliver_reminders(api)
        api.call.assert_not_called()

    def test_new_actions_reject_other_sender(self):
        order, notice = self.accepted_notice()
        for action in ("worker_reject", "client_reject", "defer_1", "defer_2"):
            callback = self.callback_for(notice, action)
            callback["from"] = {"id": 999}
            self.assertIsNone(apply_callback(callback)[1])
        order.refresh_from_db()
        self.assertEqual(order.status, "in_progress")

    def test_repeat_repair_preserves_payment_and_queues_assignment(self):
        from apps.orders.services import repeat_repair
        from apps.leads.telegram import notification_text
        from django.core.exceptions import PermissionDenied
        operator = get_user_model().objects.create_user(username="operator", role="operator")
        order, _ = convert_lead(self.lead.pk)
        order.status, order.amount, order.received_amount = "paid", 20000, 20000
        order.save()
        repeated = repeat_repair(order.pk, operator)
        self.assertEqual(repeated.status, "in_progress")
        self.assertEqual(repeated.employee_id, self.employee.pk)
        self.assertEqual(repeated.client_id, order.client_id)
        self.assertEqual(repeated.repeat_of_id, order.pk)
        self.assertIsNone(repeated.amount)
        self.assertIsNone(repeated.received_amount)
        self.assertEqual(TelegramNotice.objects.filter(order=repeated, state="pending").count(), 1)
        self.assertEqual(repeat_repair(order.pk, operator).pk, repeated.pk)
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.received_amount, 20000)
        self.assertIn(str(order.pk), notification_text(repeated))
        with self.assertRaises(PermissionDenied):
            repeat_repair(order.pk, self.employee)
        with self.assertRaises(ValidationError):
            repeat_repair(repeated.pk, operator)
        self.client.force_login(operator)
        response = self.client.post(f"/orders/{order.pk}/", {"action": "repeat_repair"})
        self.assertRedirects(response, "/kanban/")
        cards = self.client.get("/api/leads/board/").data["leads"]
        self.assertEqual(next(c for c in cards if c["key"] == f"order-{repeated.pk}")["status"], "assigned")

    def test_repeat_notification_has_contacts_and_work_actions(self):
        from apps.orders.services import repeat_repair
        operator = get_user_model().objects.create_user(username="repeat_operator", role="operator")
        order, _ = convert_lead(self.lead.pk)
        order.status = "paid"
        order.save()
        repeated = repeat_repair(order.pk, operator)
        api = Mock()
        api.call.return_value = {"message_id": 123}
        deliver_pending(api)
        notice = TelegramNotice.objects.get(order=repeated)
        notice.refresh_from_db()
        self.assertEqual(notice.state, "sent")
        sent = api.call.call_args.kwargs
        self.assertIn(self.customer.phone, sent["text"])
        actions = [button["callback_data"].split(":")[-1] for row in sent["reply_markup"]["inline_keyboard"] for button in row]
        self.assertIn("finish", actions)
        self.assertIn("defer_1", actions)
        self.assertNotIn("accept", actions)

    def test_repeat_finishes_without_payment_prompt(self):
        from apps.orders.services import repeat_repair
        from apps.leads.telegram import process_update, keyboard
        operator = get_user_model().objects.create_user(username="free_operator", role="operator")
        original, _ = convert_lead(self.lead.pk)
        original.status = "paid"
        original.save()
        repeated = repeat_repair(original.pk, operator)
        notice = TelegramNotice.objects.get(order=repeated)
        notice.state, notice.chat_id, notice.message_id = "sent", 9988, 44
        notice.save()
        actions = [b["callback_data"].split(":")[-1] for row in keyboard(notice, accepted=True)["inline_keyboard"] for b in row]
        self.assertEqual(actions, ["defer_1", "defer_2", "finish"])
        self.assertIsNone(apply_callback(self.callback_for(notice, "worker_reject"))[1])
        api = Mock()
        process_update(api, {"callback_query": self.callback_for(notice, "finish")})
        repeated.refresh_from_db()
        notice.refresh_from_db()
        self.assertEqual(repeated.status, "completed")
        self.assertIsNone(repeated.received_amount)
        self.assertIsNone(repeated.paid_at)
        self.assertFalse(notice.active)
        self.assertFalse(any(c.args[0] == "sendMessage" for c in api.call.call_args_list))
        self.assertEqual(api.call.call_args.kwargs["reply_markup"], {"inline_keyboard": []})

    def test_expenses_split_and_percentage_snapshot(self):
        from apps.leads.telegram import apply_amount_message
        from apps.orders.services import payment_split
        order, notice = self.accepted_notice()
        notice.amount_prompt_id = 55
        notice.save()
        message = {"from": {"id": 9988}, "chat": {"id": 9988, "type": "private"},
                   "reply_to_message": {"message_id": 55}, "text": "20000\n2000\nReplaced pump"}
        apply_amount_message({**message, "text": "20000\n20001\nRepair"})
        order.refresh_from_db()
        self.assertEqual(order.status, "in_progress")
        apply_amount_message(message)
        order.refresh_from_db()
        self.assertEqual(order.expenses, 2000)
        self.assertEqual(order.work_comment, "Replaced pump")
        self.assertEqual(payment_split(order), (Decimal("18000"), Decimal("5400"), Decimal("12600")))
        self.employee.percentage = 70
        self.employee.save()
        self.assertEqual(payment_split(order)[1], Decimal("5400"))

    def test_payment_asks_three_separate_questions(self):
        from apps.leads.telegram import process_update
        order, notice = self.accepted_notice()
        api = Mock()
        api.call.return_value = {"message_id": 55}
        process_update(api, {"callback_query": self.callback_for(notice, "finish")})
        def reply(prompt_id, text):
            process_update(api, {"message": {"from": {"id": 9988},
                "chat": {"id": 9988, "type": "private"},
                "reply_to_message": {"message_id": prompt_id}, "text": text}})
        api.call.return_value = {"message_id": 56}
        reply(55, "20000")
        notice.refresh_from_db()
        self.assertEqual(notice.payment_step, "expenses")
        self.assertEqual(notice.draft_amount, 20000)
        reply(55, "99999")
        notice.refresh_from_db()
        self.assertEqual(notice.draft_amount, 20000)
        reply(56, "30000")
        notice.refresh_from_db()
        self.assertEqual(notice.payment_step, "expenses")
        api.call.return_value = {"message_id": 57}
        reply(56, "2000")
        order.refresh_from_db()
        self.assertEqual(order.status, "in_progress")
        notice.refresh_from_db()
        self.assertEqual(notice.payment_step, "comment")
        reply(57, "Pump purchase")
        order.refresh_from_db()
        self.assertEqual(order.status, "in_progress")
        process_update(api, {"callback_query": self.callback_for(notice, "confirm_payment")})
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.amount, 20000)
        self.assertEqual(order.expenses, 2000)
        self.assertEqual(order.work_comment, "Pump purchase")

    def test_payment_without_percentage_cleans_up_questions(self):
        from apps.leads.telegram import process_update
        self.employee.percentage = None
        self.employee.save()
        order, notice = self.accepted_notice()
        api = Mock()
        api.call.return_value = {"message_id": 55}
        process_update(api, {"callback_query": self.callback_for(notice, "finish")})
        for prompt, reply_id, value, next_id in ((55, 101, "20000", 56), (56, 102, "2000", 57), (57, 103, "Pump", 58)):
            api.call.return_value = {"message_id": next_id}
            process_update(api, {"message": {"from": {"id": 9988}, "chat": {"id": 9988, "type": "private"},
                "reply_to_message": {"message_id": prompt}, "message_id": reply_id, "text": value}})
            api.call.assert_any_call("deleteMessage", chat_id=9988, message_id=prompt)
            api.call.assert_any_call("deleteMessage", chat_id=9988, message_id=reply_id)
        order.refresh_from_db()
        self.assertEqual(order.status, "in_progress")
        process_update(api, {"callback_query": self.callback_for(notice, "confirm_payment")})
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.expenses, 2000)
        self.assertIsNone(order.worker_percentage)

    def test_three_steps_plain_messages_and_no_restart(self):
        from apps.leads.telegram import process_update
        order, notice = self.accepted_notice()
        api = Mock()
        api.call.return_value = {"message_id": 55}
        callback = self.callback_for(notice, "finish")
        process_update(api, {"callback_query": callback})
        api.call.return_value = {"message_id": 56}
        process_update(api, {"message": {"from": {"id": 9988}, "chat": {"id": 9988, "type": "private"}, "text": "10000"}})
        notice.refresh_from_db()
        self.assertEqual(notice.payment_step, "expenses")
        process_update(api, {"callback_query": callback})
        notice.refresh_from_db()
        self.assertEqual(notice.payment_step, "expenses")
        self.assertEqual(notice.draft_amount, 10000)
        api.call.return_value = {"message_id": 57}
        process_update(api, {"message": {"from": {"id": 9988}, "chat": {"id": 9988, "type": "private"}, "text": "2000"}})
        process_update(api, {"message": {"from": {"id": 9988}, "chat": {"id": 9988, "type": "private"}, "text": "Changed pump"}})
        order.refresh_from_db()
        self.assertEqual(order.status, "in_progress")
        process_update(api, {"callback_query": self.callback_for(notice, "confirm_payment")})
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.work_comment, "Changed pump")

    def test_edit_confirmation_and_duplicate_confirm(self):
        from apps.leads.telegram import process_update
        order, notice = self.accepted_notice()
        notice.payment_step = "confirm"
        notice.amount_prompt_id = 55
        notice.draft_amount, notice.draft_expenses, notice.draft_comment = 20000, 2000, "Repair"
        notice.save()
        api = Mock()
        api.call.return_value = {"message_id": 56}
        process_update(api, {"callback_query": self.callback_for(notice, "edit_payment")})
        notice.refresh_from_db()
        self.assertEqual(notice.payment_step, "amount")
        self.assertIsNone(notice.draft_amount)
        self.assertIsNone(apply_callback(self.callback_for(notice, "confirm_payment"))[1])
        notice.payment_step = "confirm"
        notice.draft_amount, notice.draft_expenses, notice.draft_comment = 10000, 1000, "Corrected"
        notice.save()
        process_update(api, {"callback_query": self.callback_for(notice, "confirm_payment")})
        count = order.events.count()
        process_update(api, {"callback_query": self.callback_for(notice, "confirm_payment")})
        self.assertEqual(order.events.count(), count)
        order.refresh_from_db()
        self.assertEqual(order.amount, 10000)
        self.assertEqual(order.expenses, 1000)
