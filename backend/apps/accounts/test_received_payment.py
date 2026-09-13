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

    def test_web_completion_uses_payment_and_expense_sequence(self):
        self.order.status = "in_progress"
        self.order.amount = None
        self.worker.percentage = 50
        self.worker.save(update_fields=("percentage",))
        self.order.save(update_fields=("status", "amount"))
        preview = self.client.post(self.url, {
            "action": "preview_completion",
            "amount": "150.00",
            "expenses": "30.00",
            "comment": "Заменил насос",
        })
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Подтверждение завершения заказа")
        self.assertContains(preview, "После расходов: 120")
        self.assertContains(preview, "Доля мастера: 60")
        self.assertContains(preview, "Доля компании: 60")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "in_progress")
        self.assertEqual(self.order.expenses, 0)
        back = self.client.post(self.url, {"action": "completion_back"})
        self.assertEqual(back.status_code, 200)
        self.assertContains(back, 'value="150.00"')
        self.assertContains(back, 'value="30.00"')
        preview = self.client.post(self.url, {
            "action": "preview_completion",
            "amount": "150.00",
            "expenses": "30.00",
            "comment": "Заменил насос",
        })
        self.assertContains(preview, "Подтверждение завершения заказа")
        response = self.client.post(self.url, {"action": "confirm_completion"})
        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "paid")
        self.assertEqual(self.order.amount, 150)
        self.assertEqual(self.order.expenses, 30)
        self.assertEqual(self.order.received_amount, 150)
        self.assertEqual(self.order.worker_percentage, self.worker.percentage)
        self.assertEqual(self.order.work_comment, "Заменил насос")
        self.assertEqual(self.order.events.count(), 3)
        duplicate = self.client.post(self.url, {"action": "confirm_completion"})
        self.assertEqual(duplicate.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.events.count(), 3)

    def test_web_confirmation_revalidates_order_state(self):
        self.order.status = "in_progress"
        self.order.save(update_fields=("status",))
        self.assertEqual(self.client.post(self.url, {
            "action": "preview_completion", "amount": "100", "expenses": "0",
            "comment": "Работа",
        }).status_code, 200)
        self.order.status = "assigned"
        self.order.save(update_fields=("status",))
        response = self.client.post(self.url, {"action": "confirm_completion"})
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "assigned")
        self.assertIsNone(self.order.paid_at)

    def test_free_repeat_is_completed_without_payment_or_awaiting_label(self):
        original = Order.objects.create(
            title="Original repair",
            client=self.order.client,
            service=self.order.service,
            employee=self.worker,
            status="paid",
            amount=100,
            worker_percentage=50,
        )
        repeat = Order.objects.create(
            title="Repeat repair",
            client=self.order.client,
            service=self.order.service,
            employee=self.worker,
            repeat_of=original,
            status="in_progress",
        )
        repeat_url = f"/orders/{repeat.pk}/"
        response = self.client.post(repeat_url, {"action": "complete_with_payment"})
        self.assertEqual(response.status_code, 302)
        repeat.refresh_from_db()
        self.assertEqual(repeat.status, "completed")
        self.assertIsNone(repeat.amount)
        self.assertIsNone(repeat.received_amount)
        self.assertIsNone(repeat.paid_at)
        self.assertContains(self.client.get(repeat_url), "Выполнен · бесплатно")
        self.assertNotContains(self.client.get(repeat_url), "Выполнен, ожидает оплаты")
        self.assertEqual(
            self.client.post(repeat_url, {
                "action": "received_payment",
                "received_amount": "100",
                "received_method": "cash",
            }).status_code,
            200,
        )
        repeat.refresh_from_db()
        self.assertEqual(repeat.status, "completed")
        self.assertIsNone(repeat.paid_at)

    def test_web_completion_rejects_invalid_expenses_and_replay(self):
        self.order.status = "in_progress"
        self.order.save(update_fields=("status",))
        invalid = self.client.post(self.url, {
            "action": "preview_completion",
            "amount": "100.00",
            "expenses": "101.00",
            "comment": "Работа",
        })
        self.assertEqual(invalid.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "in_progress")
        self.assertFalse(self.order.events.exists())

        valid = self.client.post(self.url, {
            "action": "preview_completion",
            "amount": "100.00",
            "expenses": "10.00",
            "comment": "Работа",
        })
        self.assertEqual(valid.status_code, 200)
        valid = self.client.post(self.url, {"action": "confirm_completion"})
        self.assertEqual(valid.status_code, 302)
        event_count = self.order.events.count()
        replay = self.client.post(self.url, {
            "action": "confirm_completion",
        })
        self.assertEqual(replay.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "paid")
        self.assertEqual(self.order.amount, 100)
        self.assertEqual(self.order.events.count(), event_count)

    def test_invalid_data_and_stage(self):
        self.assertContains(self.client.get(self.url), "Выполнен, ожидает оплаты")
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

    def test_worker_contact_hidden_until_acceptance(self):
        self.order.status = "assigned"
        self.order.client.phone = "+77001234567"
        self.order.client.save()
        self.order.save()
        self.assertNotContains(self.client.get(self.url), "+77001234567")
        self.assertNotContains(self.client.get("/my-orders/"), "+77001234567")
        self.client.post(self.url, {"action":"start"})
        self.assertContains(self.client.get(self.url), "+77001234567")
