from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count

from .models import Lead, TelegramNotice


class AssignmentUnavailable(Exception):
    pass


@transaction.atomic
def assign_lead(pk, employee_id=None):
    lead = Lead.objects.select_for_update().get(pk=pk)
    from apps.orders.models import Order
    if Order.objects.filter(lead=lead).exists():
        raise AssignmentUnavailable("Для лида уже создан заказ. Работайте с заказом.")
    if lead.employee_id is not None or lead.status not in (Lead.Status.NEW, Lead.Status.IN_PROGRESS, Lead.Status.ASSIGNED):
        raise AssignmentUnavailable("Распределять можно только открытый лид без ответственного.")

    if not lead.service_id:
        raise AssignmentUnavailable("Сначала выберите услугу лида.")
    User = get_user_model()
    # Lock eligible employees in a fixed order. Concurrent assignments wait before counting load.
    employees = list(User.objects.select_for_update().filter(
        is_active=True, role="worker", is_available=True, services=lead.service_id,
    ).order_by("pk"))
    workloads = dict(Lead.objects.filter(
        employee_id__in=[employee.pk for employee in employees],
        status__in=(Lead.Status.NEW, Lead.Status.IN_PROGRESS, Lead.Status.ASSIGNED),
    ).values("employee_id").annotate(total=Count("pk")).values_list("employee_id", "total"))
    from apps.orders.models import Order
    order_loads = Order.objects.filter(
        employee_id__in=[employee.pk for employee in employees],
        status__in=(Order.Status.NEW, Order.Status.ASSIGNED, Order.Status.IN_PROGRESS),
    ).values("employee_id").annotate(total=Count("pk"))
    for item in order_loads:
        workloads[item["employee_id"]] = workloads.get(item["employee_id"], 0) + item["total"]
    available = [
        employee for employee in employees
        if workloads.get(employee.pk, 0) < employee.max_active_leads
    ]
    if employee_id is not None:
        available = [employee for employee in available if employee.pk == employee_id]
    if not available:
        raise AssignmentUnavailable("Нет доступных сотрудников для этой услуги. Проверьте навыки, доступность и загрузку.")
    employee = min(available, key=lambda item: (workloads.get(item.pk, 0), item.pk))
    lead.employee = employee
    lead.status = Lead.Status.ASSIGNED
    lead.save(update_fields=("employee", "status"))
    TelegramNotice.objects.filter(lead=lead, active=True).update(active=False)
    TelegramNotice.objects.create(lead=lead, employee=employee)
    return Lead.objects.select_related("client", "service", "employee").get(pk=lead.pk)
