"""Temporary records for the explicitly enabled PostgreSQL browser test."""
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.sessions.models import Session
from django.db import connection, transaction
from django.test import Client as Browser
from apps.customers.models import Client
from apps.services.models import Service
from apps.leads.models import Lead
from apps.orders.models import Order

assert connection.vendor == "postgresql"
action = sys.argv[1]
if action == "create":
    with transaction.atomic():
        suffix = uuid4().hex
        employee = get_user_model().objects.create_user(username="kanban_e2e_" + suffix, role="worker", is_available=True)
        employee.user_permissions.add(
            Permission.objects.get(content_type__app_label="leads", codename="view_lead"),
            Permission.objects.get(content_type__app_label="customers", codename="view_client"),
            Permission.objects.get(content_type__app_label="leads", codename="change_lead"),
        )
        customer = Client.objects.create(name="Kanban E2E " + suffix, phone="+77000000000")
        service = Service.objects.create(name="Kanban E2E " + suffix)
        employee.services.add(service)
        unassigned = len(sys.argv) > 2 and sys.argv[2] == "unassigned"
        lead = Lead.objects.create(title="Kanban E2E", client=customer, service=service, employee=None if unassigned else employee, source="Test")
        operator = get_user_model().objects.create_user(username="operator_e2e_" + suffix, role="operator")
        manager = get_user_model().objects.create_user(username="manager_e2e_" + suffix, role="manager")
        order = Order.objects.create(title="Role E2E", client=customer, service=service, employee=employee, status="assigned", amount=15000) if len(sys.argv) > 2 and sys.argv[2] == "roles" else None
        worker_browser = Browser()
        worker_browser.force_login(employee)
        manager_browser = Browser()
        manager_browser.force_login(manager)
        browser = Browser()
        browser.force_login(operator)
        fixture = {
            "employee_id": employee.pk, "username": employee.username,
            "client_id": customer.pk, "service_id": service.pk, "lead_id": lead.pk,
            "session": browser.session.session_key,
            "worker_session": worker_browser.session.session_key, "manager_session": manager_browser.session.session_key,
            "operator_id": operator.pk, "manager_id": manager.pk, "order_id": order.pk if order else None,
        }
    print(json.dumps(fixture))
else:
    fixture = json.load(sys.stdin)
    employee = get_user_model().objects.get(pk=fixture["employee_id"], username=fixture["username"])
    assert employee.username.startswith("kanban_e2e_")
    lead = Lead.objects.get(pk=fixture["lead_id"], client_id=fixture["client_id"], service_id=fixture["service_id"])
    assert lead.employee_id in (None, employee.pk)
    if action == "status":
        print(lead.status)
    elif action == "cleanup":
        with transaction.atomic():
            if fixture["order_id"]:
                Order.objects.get(pk=fixture["order_id"], client_id=fixture["client_id"]).delete()
            Order.objects.filter(lead=lead).delete()
            lead.delete()
            Client.objects.get(pk=fixture["client_id"]).delete()
            Service.objects.get(pk=fixture["service_id"]).delete()
            Session.objects.filter(session_key__in=[fixture["session"], fixture["worker_session"], fixture["manager_session"]]).delete()
            get_user_model().objects.get(pk=fixture["operator_id"], username__startswith="operator_e2e_").delete()
            get_user_model().objects.get(pk=fixture["manager_id"], username__startswith="manager_e2e_").delete()
            employee.delete()
        print("Temporary PostgreSQL browser fixture removed")
    else:
        raise ValueError("Unknown action")
