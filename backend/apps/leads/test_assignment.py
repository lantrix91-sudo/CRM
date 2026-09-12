from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.customers.models import Client
from apps.services.models import Service
from .models import Lead


class AssignmentTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_superuser(username="manager", password=None)
        self.worker = User.objects.create_user(username="worker", is_staff=False, role="worker", is_available=True, max_active_leads=1)
        self.other = User.objects.create_user(username="other", is_staff=False, role="worker", is_available=True)
        self.service = Service.objects.create(name="Repair")
        self.worker.services.add(self.service)
        self.other.services.add(self.service)
        self.customer = Client.objects.create(name="Client", phone="+77000000000")
        self.lead = Lead.objects.create(title="New", client=self.customer, service=self.service)
        self.api = APIClient(enforce_csrf_checks=True)
        self.api.force_login(self.manager)
        self.api.get("/kanban/")
        self.csrf = self.api.cookies["csrftoken"].value
        self.url = f"/api/leads/{self.lead.pk}/assign/"

    def assign(self, data=None):
        # The legacy service remains used by administration; public API now assigns orders.
        from .assignment import assign_lead, AssignmentUnavailable
        from types import SimpleNamespace
        try:
            lead = assign_lead(self.lead.pk, **(data or {}))
            return SimpleNamespace(status_code=200, data={"employee_id": lead.employee_id})
        except AssignmentUnavailable:
            return SimpleNamespace(status_code=409)

    def test_assignment_sets_employee_and_status(self):
        response = self.assign()
        self.assertEqual(response.status_code, 200)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.employee, self.worker)
        self.assertEqual(self.lead.status, Lead.Status.ASSIGNED)
        self.assertEqual(response.data["employee_id"], self.worker.pk)

    def test_least_loaded_and_capacity(self):
        Lead.objects.create(title="Busy", client=self.customer, service=self.service, employee=self.worker)
        self.assertEqual(self.assign().data["employee_id"], self.other.pk)

    def test_closed_leads_do_not_count(self):
        for status in (Lead.Status.WON, Lead.Status.LOST):
            Lead.objects.create(title="Closed", client=self.customer, service=self.service, employee=self.worker, status=status)
        self.assertEqual(self.assign().data["employee_id"], self.worker.pk)

    def test_load_in_other_services_counts(self):
        other_service = Service.objects.create(name="Other")
        Lead.objects.create(title="Busy", client=self.customer, service=other_service, employee=self.worker, status=Lead.Status.IN_PROGRESS)
        self.assertEqual(self.assign().data["employee_id"], self.other.pk)

    def test_ineligible_workers_are_skipped(self):
        for field in ("is_active", "is_available"):
            with self.subTest(field=field):
                setattr(self.worker, field, False)
                self.worker.save()
                self.assertEqual(self.assign().data["employee_id"], self.other.pk)
                self.lead.employee = None
                self.lead.status = Lead.Status.NEW
                self.lead.save()
                setattr(self.worker, field, True)
                self.worker.save()
        self.worker.services.clear()
        self.assertEqual(self.assign().data["employee_id"], self.other.pk)

    def test_no_candidate_leaves_lead_unchanged(self):
        self.worker.services.clear()
        self.other.is_available = False
        self.other.save()
        self.assertEqual(self.assign().status_code, 409)
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.employee_id)
        self.assertEqual(self.lead.status, Lead.Status.NEW)

    def test_does_not_reassign_or_reopen(self):
        self.assign()
        self.assertEqual(self.assign().status_code, 409)
        self.lead.employee = None
        self.lead.status = Lead.Status.WON
        self.lead.save()
        self.assertEqual(self.assign().status_code, 409)

    def test_selected_worker_must_have_skill_availability_and_capacity(self):
        for field, value in (("is_available", False), ("max_active_leads", 0), ("role", "operator")):
            original = getattr(self.worker, field)
            setattr(self.worker, field, value)
            self.worker.save()
            self.assertEqual(self.assign({"employee_id": self.worker.pk}).status_code, 409)
            setattr(self.worker, field, original)
            self.worker.save()
        self.worker.services.clear()
        self.assertEqual(self.assign({"employee_id": self.worker.pk}).status_code, 409)
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.employee_id)
