import uuid
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from apps.accounts.models import User
from apps.leads.telegram import link_telegram_account


class TelegramLinkTests(TestCase):
    def setUp(self):
        self.worker = User.objects.create_user(username="worker", role="worker", telegram_link_token=uuid.uuid4(), telegram_link_expires=timezone.now()+timedelta(hours=24))
        self.token = str(self.worker.telegram_link_token)
        self.message = {"chat":{"id":123,"type":"private"},"from":{"id":123}}

    def test_link_is_single_use(self):
        link_telegram_account(self.message,self.token)
        self.worker.refresh_from_db()
        self.assertEqual(self.worker.telegram_chat_id,123)
        self.assertIsNone(self.worker.telegram_link_token)
        link_telegram_account({"chat":{"id":456,"type":"private"},"from":{"id":456}},self.token)
        self.worker.refresh_from_db()
        self.assertEqual(self.worker.telegram_chat_id,123)

    def test_expired_duplicate_and_group_are_rejected(self):
        self.assertIsNone(link_telegram_account({**self.message,"chat":{"id":123,"type":"group"}},self.token))
        self.worker.telegram_link_expires=timezone.now()-timedelta(seconds=1)
        self.worker.save()
        link_telegram_account(self.message,self.token)
        self.worker.refresh_from_db()
        self.assertIsNone(self.worker.telegram_chat_id)
        self.worker.telegram_link_expires=timezone.now()+timedelta(hours=1)
        self.worker.save()
        User.objects.create_user(username="other",role="worker",telegram_chat_id=123)
        link_telegram_account(self.message,self.token)
        self.worker.refresh_from_db()
        self.assertIsNone(self.worker.telegram_chat_id)

    @patch("apps.leads.telegram.TelegramAPI.call", return_value={"username":"example_bot"})
    def test_manager_issues_link_and_worker_cannot(self, api):
        manager=User.objects.create_user(username="manager",role="manager")
        self.client.force_login(manager)
        response=self.client.post(f"/employees/{self.worker.pk}/",{"action":"telegram_link"})
        self.assertContains(response,"https://t.me/example_bot?start=link_")
        self.worker.refresh_from_db()
        self.assertNotEqual(str(self.worker.telegram_link_token),self.token)
        self.client.force_login(self.worker)
        self.assertEqual(self.client.post(f"/employees/{self.worker.pk}/",{"action":"telegram_link"}).status_code,403)
