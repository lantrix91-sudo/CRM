import os
from datetime import datetime, time, timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.customers.models import Client
from .models import Lead, IncomingCall


class TelephonyTests(TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"TELEPHONY_WEBHOOK_TOKEN": "test-secret"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.api = APIClient()
        self.url = "/api/telephony/incoming/"

    def send(self, event="call-1", phone="+7 (700) 123-45-67"):
        return self.api.post(self.url, {"event_id": event, "phone": phone}, format="json", HTTP_AUTHORIZATION="Bearer test-secret")

    def test_new_client_and_idempotent_call(self):
        response = self.send()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Lead.objects.get().source, "Телефон")
        self.assertIsNone(Lead.objects.get().service_id)
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(IncomingCall.objects.count(), 1)
        self.assertEqual(Client.objects.count(), 1)

    def test_existing_phone_formats_match_and_history(self):
        customer = Client.objects.create(name="Ivan", phone="8 (700) 123-45-67")
        response = self.send()
        self.assertEqual(response.data["client_id"], customer.pk)
        self.assertEqual(Client.objects.count(), 1)
        self.assertEqual(self.send(event="call-2").status_code, 201)
        self.assertEqual(Lead.objects.count(), 2)

    def test_ambiguous_phone_needs_operator(self):
        for name in ("Ivan", "Anna"):
            Client.objects.create(name=name, phone="+77001234567")
        response = self.send()
        self.assertTrue(response.data["needs_review"])
        self.assertIsNone(response.data["lead_id"])
        self.assertEqual(Lead.objects.count(), 0)

    def test_wrong_token_and_invalid_phone_rejected(self):
        self.assertEqual(self.api.post(self.url, {}, format="json").status_code, 403)
        self.assertEqual(self.send(phone="hidden").status_code, 400)
        self.assertEqual(IncomingCall.objects.count(), 0)

    def test_same_event_cannot_change_phone(self):
        self.send()
        self.assertEqual(self.send(phone="+77009999999").status_code, 400)
        self.assertEqual(Client.objects.count(), 1)

    def test_phone_index_updates(self):
        customer = Client.objects.create(name="Ivan", phone="+77001234567")
        customer.phone = "+77009999999"
        customer.save(update_fields=["phone"])
        customer.refresh_from_db()
        self.assertEqual(customer.normalized_phone, "+77009999999")

    def test_null_service_board(self):
        self.send()
        user = get_user_model().objects.create_superuser(username="manager", password=None)
        self.api.force_login(user)
        response = self.api.get("/api/leads/board/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["leads"][0]["service_name"], "Услуга не выбрана")

    def test_report_local_day_and_legacy_sources(self):
        user = get_user_model().objects.create_superuser(username="boss", password=None)
        self.api.force_login(user)
        customer = Client.objects.create(name="Ivan", phone="+77001111111")
        for source in ("OLX", "olx ", "Google", "Instagram", "Telegram", "Телефон", "Старый", ""):
            Lead.objects.create(title="Test", client=customer, source=source)
        old = Lead.objects.create(title="Yesterday", client=customer, source="OLX")
        start = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
        Lead.objects.filter(pk=old.pk).update(created_at=start - timedelta(seconds=1))
        response = self.api.get("/analytics/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total"], 8)
        self.assertEqual(dict(response.context["rows"])["OLX"], 2)

    def test_history_and_permissions(self):
        data = self.send().data
        user = get_user_model().objects.create_superuser(username="boss", password=None)
        self.api.force_login(user)
        self.assertEqual(self.api.get(data["history_url"]).status_code, 200)
        self.assertEqual(self.api.get("/calls/").status_code, 200)
        user.is_superuser = False
        user.save()
        self.assertEqual(self.api.get("/analytics/").status_code, 403)
        self.assertEqual(self.api.get(data["history_url"]).status_code, 403)
