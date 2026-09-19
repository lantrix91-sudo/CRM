from datetime import timedelta
from unittest.mock import Mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.forms import LeadForm
from apps.accounts.models import User
from apps.customers.models import Client, City
from apps.orders.models import Order
from apps.orders.services import convert_lead
from apps.services.models import Service
from .appointments import deliver_appointment_reminders
from .models import Lead
from .telegram import TelegramError, notification_text


class AppointmentTests(TestCase):
    def setUp(self):
        self.worker = User.objects.create_user(username="appointment-worker", role="worker", telegram_chat_id=1234)
        self.manager = User.objects.create_superuser(username="appointment-manager", password="test-password")
        self.customer = Client.objects.create(name="Client", phone="+77001234567")
        self.city = City.objects.create(name="Test city")
        self.service = Service.objects.create(name="Repair")
        self.when = timezone.now() + timedelta(minutes=30)
        self.lead = Lead.objects.create(title="Repair", client=self.customer, service=self.service,
            city=self.city, employee=self.worker, status="assigned", scheduled_at=self.when)
        self.api = Mock()

    def test_optional_form_and_local_time(self):
        data = {"client_name": "Client", "client_phone": "+77001234567", "city": self.city.pk}
        form = LeadForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.save().scheduled_at)
        data["scheduled_at"] = "2030-10-01T15:30"
        form = LeadForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(timezone.localtime(form.save().scheduled_at).strftime("%Y-%m-%d %H:%M"), "2030-10-01 15:30")
        data["scheduled_at"] = "invalid"
        self.assertFalse(LeadForm(data).is_valid())

    def test_history_changes_and_clear(self):
        self.assertEqual(self.lead.events.count(), 1)
        self.lead._history_actor = self.manager
        self.lead.scheduled_at += timedelta(days=1)
        self.lead.save()
        self.assertEqual(self.lead.events.first().actor, self.manager)
        self.lead.save()
        self.assertEqual(self.lead.events.count(), 2)
        self.lead.scheduled_at = None
        self.lead.save(update_fields=("scheduled_at",))
        self.assertEqual(self.lead.events.count(), 3)

    def test_reminder_once_and_history(self):
        deliver_appointment_reminders(self.api)
        deliver_appointment_reminders(self.api)
        self.api.call.assert_called_once()
        self.assertEqual(self.api.call.call_args.kwargs["chat_id"], 1234)
        self.assertIn(timezone.localtime(self.when).strftime("%d.%m.%Y %H:%M"), self.api.call.call_args.kwargs["text"])
        self.lead.refresh_from_db()
        self.assertIsNotNone(self.lead.appointment_reminded_at)
        self.assertEqual(self.lead.events.count(), 2)

    def test_no_reminder_without_time_or_before_window_or_after_time(self):
        for value in (None, timezone.now() + timedelta(hours=2), timezone.now() - timedelta(hours=1)):
            self.lead.scheduled_at = value
            self.lead.save()
            deliver_appointment_reminders(self.api)
        self.api.call.assert_not_called()

    def test_retry_failure(self):
        self.api.call.side_effect = TelegramError("offline")
        deliver_appointment_reminders(self.api)
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.appointment_reminded_at)
        self.assertEqual(self.lead.events.count(), 1)
        self.api.call.side_effect = None
        deliver_appointment_reminders(self.api)
        self.assertEqual(self.api.call.call_count, 2)

    def test_conversion_preserves_schedule_and_notifies_current_worker(self):
        order, _ = convert_lead(self.lead.pk)
        self.assertEqual(order.scheduled_at, self.when)
        other = User.objects.create_user(username="other", role="worker", telegram_chat_id=5678)
        order.employee = other
        order.save()
        deliver_appointment_reminders(self.api)
        self.assertEqual(self.api.call.call_args.kwargs["chat_id"], 5678)
        self.assertIn("Запись:", notification_text(order))
        self.assertTrue(order.events.filter(description__contains="Напоминание").exists())
        order.status = "cancelled"
        order.save()
        self.lead.refresh_from_db()
        self.lead.scheduled_at += timedelta(minutes=1)
        self.lead.save()
        self.api.reset_mock()
        deliver_appointment_reminders(self.api)
        self.api.call.assert_not_called()

    def test_reschedule_and_reassignment_send_new_reminder(self):
        deliver_appointment_reminders(self.api)
        self.lead.refresh_from_db()
        self.lead.scheduled_at += timedelta(minutes=5)
        self.lead.save()
        deliver_appointment_reminders(self.api)
        self.assertEqual(self.api.call.call_count, 2)
        self.lead.refresh_from_db()
        self.lead.employee = User.objects.create_user(username="replacement", role="worker", telegram_chat_id=5678)
        self.lead.save(update_fields=("employee",))
        deliver_appointment_reminders(self.api)
        self.assertEqual(self.api.call.call_count, 3)
        self.assertEqual(self.api.call.call_args.kwargs["chat_id"], 5678)

    def test_no_reminder_closed_unassigned_or_inactive_worker(self):
        self.lead.status = "lost"
        self.lead.save()
        deliver_appointment_reminders(self.api)
        self.lead.status = "assigned"
        self.lead.employee = None
        self.lead.save()
        deliver_appointment_reminders(self.api)
        self.lead.employee = self.worker
        self.lead.save()
        self.worker.is_active = False
        self.worker.save()
        deliver_appointment_reminders(self.api)
        self.api.call.assert_not_called()

    def test_board_and_mobile_expose_time_and_permissions(self):
        api = APIClient()
        api.force_login(self.manager)
        board = api.get("/api/leads/board/")
        self.assertEqual(board.status_code, 200)
        self.assertIsNotNone(board.data["leads"][0]["scheduled_at"])
        order, _ = convert_lead(self.lead.pk)
        self.assertEqual(api.get("/api/leads/board/").data["leads"][0]["scheduled_at"], self.when)
        from apps.accounts.mobile_api import order_data
        self.assertEqual(order_data(order)["scheduled_at"], self.when)
        api.force_login(self.worker)
        self.assertEqual(api.post(f"/workspace/lead/{self.lead.pk}/", {"scheduled_at": "2030-10-01T15:30"}).status_code, 403)

    def test_edit_records_actor_and_renders_history(self):
        api = APIClient()
        api.force_login(self.manager)
        url = f"/workspace/lead/{self.lead.pk}/"
        response = api.post(url, {"client_name": "Client", "client_phone": "+77001234567",
            "city": self.city.pk, "service": self.service.pk, "scheduled_at": "2030-10-01T15:30"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.lead.events.first().actor, self.manager)
        self.assertContains(api.get(url), "01.10.2030 15:30")
