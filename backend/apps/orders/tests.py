from django.contrib.auth import get_user_model
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from apps.customers.models import Client
from apps.services.models import Service

from .models import Order


class OrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.employee = get_user_model().objects.create_user(username="order_employee")
        cls.client_record = Client.objects.create(name="Test client", phone="+77000000000")
        cls.service = Service.objects.create(name="Test service")
        cls.order = Order.objects.create(
            title="Test order", client=cls.client_record,
            service=cls.service, employee=cls.employee,
        )

    def test_relations_are_loaded_in_one_query(self):
        with self.assertNumQueries(1):
            order = Order.objects.select_related("client", "service", "employee").get(pk=self.order.pk)
            self.assertEqual(order.client.name, "Test client")
            self.assertEqual(order.service.name, "Test service")
            self.assertEqual(order.employee.username, "order_employee")
        self.assertEqual(self.client_record.orders.get(), order)

    def test_client_and_service_with_orders_cannot_be_deleted(self):
        for obj in (self.client_record, self.service):
            with self.subTest(model=type(obj).__name__):
                with self.assertRaises(ProtectedError):
                    obj.delete()
        self.assertTrue(Order.objects.filter(pk=self.order.pk).exists())

    def test_deleting_employee_preserves_order(self):
        self.employee.delete()
        self.order.refresh_from_db()
        self.assertIsNone(self.order.employee_id)

    def test_order_can_be_created_without_employee(self):
        order = Order(
            title="Unassigned", client=self.client_record, service=self.service,
        )
        order.full_clean()
        order.save()
        self.assertIsNone(order.employee_id)
        self.assertIsNotNone(order.created_at)
