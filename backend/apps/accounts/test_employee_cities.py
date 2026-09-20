from django.test import TestCase
from django import forms
from .models import User
from .forms import EmployeeForm
from apps.customers.models import City
from apps.services.models import Service


class EmployeeCitiesTests(TestCase):
    def test_checkboxes_preserve_selection(self):
        worker = User.objects.create_user(username="worker", role="worker")
        city = City.objects.create(name="City")
        service = Service.objects.create(name="Repair")
        worker.service_cities.add(city)
        worker.services.add(service)
        form = EmployeeForm(instance=worker)
        for field in ("services", "service_cities"):
            self.assertIsInstance(form.fields[field].widget, forms.CheckboxSelectMultiple)
            self.assertIn("checked", str(form[field]))

    def test_manager_adds_city_without_creating_employee(self):
        manager = User.objects.create_user(username="manager", role="manager")
        self.client.force_login(manager)
        response = self.client.post("/employees/", {"action": "add_city", "name": "New city"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(City.objects.get(name="New city").is_active)
        self.assertEqual(User.objects.count(), 1)
        response = self.client.post("/employees/", {"action": "add_city", "name": "new CITY"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(City.objects.count(), 1)
        self.assertTrue(response.context["city_form"].errors)
        for role in ("operator", "worker"):
            user = User.objects.create_user(username=role, role=role)
            self.client.force_login(user)
            self.assertEqual(self.client.post("/employees/", {"action": "add_city", "name": role}).status_code, 403)
        self.assertEqual(City.objects.count(), 1)
