from django.test import TestCase
from apps.accounts.forms import LeadForm
from apps.customers.models import Client
from apps.leads.models import Lead
from apps.services.models import Service

class LeadClientTests(TestCase):
    def setUp(self):
        self.service=Service.objects.create(name="Repair")
        self.data={"title":"Repair","service":self.service.pk,"source":"OLX","client_name":"Ivan","client_phone":"+77001234567","client_address":"Street 1"}

    def test_create_client_and_lead(self):
        form=LeadForm(self.data)
        self.assertTrue(form.is_valid(), form.errors)
        lead=form.save()
        self.assertEqual(lead.client.name,"Ivan")
        self.assertEqual(lead.client.phone,"+77001234567")
        self.assertEqual(lead.client.address,"Street 1")

    def test_required_phone_and_optional_address(self):
        form=LeadForm({**self.data,"client_phone":"123"})
        self.assertFalse(form.is_valid())
        self.assertEqual(Client.objects.count(),0)
        form=LeadForm({**self.data,"client_address":""})
        self.assertTrue(form.is_valid(),form.errors)

    def test_existing_client_not_duplicated(self):
        for stored, entered in (("+77001234567", "8 (700) 123-45-67"), ("87001234567", "+7 700 123 45 67")):
            with self.subTest(stored=stored):
                client = Client.objects.create(name="Existing", phone=stored)
                form = LeadForm({**self.data, "client_phone": entered})
                self.assertTrue(form.is_valid(), form.errors)
                lead = form.save()
                self.assertEqual(lead.client_id, client.pk)
                self.assertEqual(Client.objects.count(), 1)
                client.refresh_from_db()
                self.assertEqual(client.name, "Existing")
                lead.delete()
                client.delete()

    def test_form_requires_no_title_or_client_selector(self):
        data = {key: value for key, value in self.data.items() if key != "title"}
        form = LeadForm(data)
        self.assertNotIn("title", form.fields)
        self.assertNotIn("client", form.fields)
        self.assertEqual(form.fields["client_name"].label, "Имя")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().title, "Ivan")

    def test_edit_prefills_client_and_preserves_title(self):
        client = Client.objects.create(name="Ivan", phone="87001234567", address="Street 1")
        lead = Lead.objects.create(title="Original title", client=client)
        form = LeadForm(instance=lead)
        self.assertEqual(form.initial["client_name"], "Ivan")
        self.assertEqual(form.initial["client_phone"], client.phone)
        self.assertEqual(form.initial["client_address"], client.address)
        form = LeadForm(self.data, instance=lead)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.title, "Original title")
        self.assertEqual(saved.client_id, client.pk)
        self.assertEqual(Client.objects.count(), 1)
