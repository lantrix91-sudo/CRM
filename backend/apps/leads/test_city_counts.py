from django.test import TestCase
from apps.customers.models import City, Client
from apps.services.models import Service
from apps.orders.models import Order
from .models import Lead
from .api import BoardAPI


class ActiveCityCountsTests(TestCase):
    def test_counts_active_records_without_double_counting_converted_leads(self):
        city = City.objects.create(name="Test city")
        empty = City.objects.create(name="Empty city")
        customer = Client.objects.create(name="Client", phone="+77001234567")
        service = Service.objects.create(name="Repair")
        for status in ("new", "assigned", "in_progress", "lost", "won", "converted"):
            Lead.objects.create(title="Lead", city=city, client=customer, service=service, status=status)
        for status in ("new", "assigned", "in_progress", "completed", "paid", "cancelled"):
            Order.objects.create(title="Order", city=city, client=customer, service=service, status=status)
        linked = Lead.objects.create(title="Linked", city=city, client=customer, service=service)
        Order.objects.create(title="Converted order", lead=linked, city=city, client=customer, service=service, status="new")
        counts = {row["id"]: row["count"] for row in BoardAPI._city_counts(None)}
        self.assertEqual(counts[city.pk], 7)
        self.assertEqual(counts[empty.pk], 0)
