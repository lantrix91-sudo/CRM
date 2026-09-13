from decimal import Decimal
from django.test import TestCase
from apps.accounts.models import User
from apps.accounts.forms import EmployeeForm
from apps.customers.models import Client
from apps.services.models import Service
from apps.orders.models import Order


class ProfileTests(TestCase):
    def setUp(self):
        self.worker = User.objects.create_user(username="profile_worker", role="worker", percentage=50)

    def test_scoped_earnings_and_read_only_curator(self):
        curator = User.objects.create_user(username="curator", role="curator", percentage=10)
        operator = User.objects.create_user(username="operator", role="operator", percentage=5)
        manager = User.objects.create_user(username="manager", role="manager", percentage=2)
        worker = User.objects.create_user(username="worker", role="worker", percentage=50,
                                          curator=curator)
        other = User.objects.create_user(username="other", role="worker")
        client = Client.objects.create(name="Client", phone="123")
        service = Service.objects.create(name="Repair")
        original = Order.objects.create(title="Paid", client=client, service=service, employee=worker, status="paid", amount=20000, worker_percentage=50)
        unrelated = Order.objects.create(title="Other", client=client, service=service, employee=other, status="paid", amount=90000)
        Order.objects.create(title="Unpaid", client=client, service=service, employee=worker, status="completed", amount=5000)
        Order.objects.create(title="Repeat", client=client, service=service, employee=worker, status="paid", repeat_of=original, amount=20000)
        for user, expected in ((curator, 2000), (worker, 10000)):
            self.client.force_login(user)
            response = self.client.get("/profile/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["earnings"], Decimal(expected))
            self.assertNotContains(response, f"/orders/{unrelated.pk}/")
            self.assertEqual(self.client.post("/profile/", {"percentage": "99"}).status_code, 405)
        self.client.force_login(curator)
        self.assertEqual(self.client.get(f"/orders/{original.pk}/").status_code, 200)
        self.assertEqual(self.client.get(f"/orders/{unrelated.pk}/").status_code, 404)
        self.assertEqual(self.client.post(f"/orders/{original.pk}/", {"action": "save"}).status_code, 403)
        self.assertEqual(self.client.get("/employees/").status_code, 403)

    def test_paid_order_shows_stored_percentage_and_financial_breakdown(self):
        client = Client.objects.create(name="Stored percentage client", phone="789")
        service = Service.objects.create(name="Stored percentage service")
        Order.objects.create(
            title="Stored percentage order", client=client, service=service,
            employee=self.worker, status="paid", amount=20000,
            worker_percentage=50,
        )
        self.worker.percentage = 80
        self.worker.save(update_fields=("percentage",))
        self.client.force_login(self.worker)
        response = self.client.get("/profile/")

        self.assertContains(response, "Стоимость услуг: 20000,00")
        self.assertContains(response, "Расходы: 0 KZT")
        self.assertContains(response, "После расходов: 20000,00")
        self.assertContains(response, "Процент мастера: 50,00 %")
        self.assertContains(response, "Доля мастера: 10000,00")
        self.assertContains(response, "Доля компании: 10000,00")

    def test_paid_breakdown_deducts_expenses_before_shares(self):
        client = Client.objects.create(name="Expenses client", phone="456")
        service = Service.objects.create(name="Expenses service")
        order = Order.objects.create(
            title="Paid with expenses", client=client, service=service,
            employee=self.worker, status="paid", amount=20000, expenses=3000,
            worker_percentage=50,
        )
        self.client.force_login(self.worker)
        response = self.client.get("/profile/")

        self.assertContains(response, "Стоимость услуг: 20000,00")
        self.assertContains(response, "Расходы: 3000,00")
        self.assertContains(response, "После расходов: 17000,00")
        self.assertContains(response, "Доля мастера: 8500,00")
        self.assertContains(response, "Доля компании: 8500,00")

    def test_worker_dashboard_has_today_month_and_current_shift_totals(self):
        client = Client.objects.create(name="Dashboard client", phone="321")
        service = Service.objects.create(name="Dashboard service")
        Order.objects.create(
            title="Dashboard order", client=client, service=service,
            employee=self.worker, status="paid", amount=20000, expenses=3000,
            worker_percentage=50,
        )
        self.client.force_login(self.worker)
        response = self.client.get("/profile/")
        dashboard = response.context["dashboard"]

        for period in ("today", "month", "current_shift"):
            self.assertEqual(dashboard[period]["order_count"], 1)
            self.assertEqual(dashboard[period]["revenue"], Decimal("20000"))
            self.assertEqual(dashboard[period]["expenses"], Decimal("3000"))
        self.assertEqual(dashboard["today"]["worker_amount"], Decimal("8500"))
        self.assertEqual(dashboard["current_shift"]["company_amount"], Decimal("8500"))
        self.assertEqual(dashboard["current_shift"]["balance_due"], Decimal("8500"))

    def test_percentage_validation(self):
        for value in ("-1", "100.01"):
            form = EmployeeForm({"username": "x", "role": "worker", "percentage": value})
            self.assertFalse(form.is_valid())
            self.assertIn("percentage", form.errors)

    def test_employee_form_has_four_roles_and_only_curator_assignment(self):
        form = EmployeeForm()
        self.assertEqual({value for value, label in User.Role.choices}, {"worker", "operator", "manager", "curator"})
        self.assertIn("curator", form.fields)
        self.assertNotIn("operator", form.fields)
        self.assertNotIn("supervisor", form.fields)
        curator = User.objects.create_user(username="eligible", role="curator")
        User.objects.create_user(username="not_curator", role="operator")
        self.assertEqual(list(form.fields["curator"].queryset), [curator])
