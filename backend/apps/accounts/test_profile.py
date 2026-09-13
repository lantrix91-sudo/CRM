from decimal import Decimal
from django.test import TestCase
from apps.accounts.models import User
from apps.accounts.forms import EmployeeForm
from apps.customers.models import Client
from apps.services.models import Service
from apps.orders.models import Order


class ProfileTests(TestCase):
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
