from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.leads.models import Lead, TelegramNotice
from .models import Order, OrderEvent


@transaction.atomic
def convert_lead(pk, actor=None):
    lead = Lead.objects.select_for_update().get(pk=pk)
    existing = Order.objects.filter(lead=lead).first()
    if existing:
        return existing, False
    if lead.status == Lead.Status.LOST:
        raise ValidationError("Закрытый без сделки лид нельзя преобразовать в заказ.")
    if not lead.service_id:
        raise ValidationError("Перед созданием заказа выберите услугу лида.")
    employee = lead.employee if lead.employee_id and lead.employee.is_active and lead.employee.role == "worker" else None
    order = Order.objects.create(
        lead=lead, title=lead.title, client=lead.client, service=lead.service,
        employee=employee, status=Order.Status.ASSIGNED if employee else Order.Status.NEW,
    )
    lead.status = Lead.Status.CONVERTED
    lead.save(update_fields=("status",))
    TelegramNotice.objects.filter(lead=lead, active=True).update(active=False)
    if employee:
        TelegramNotice.objects.create(order=order, employee=employee)
    record_event(order, actor, "Клиент согласился; создан заказ")
    if employee:
        record_event(order, actor, f"Сохранён назначенный мастер: {employee}")
    return order, True


@transaction.atomic
def transition_order(pk, action, actor=None):
    order = Order.objects.select_for_update().get(pk=pk)
    transitions = {
        "start": (Order.Status.ASSIGNED, Order.Status.IN_PROGRESS),
        "complete": (Order.Status.IN_PROGRESS, Order.Status.COMPLETED),
        "pay": (Order.Status.COMPLETED, Order.Status.PAID),
    }
    if action not in transitions or order.status != transitions[action][0]:
        raise ValidationError("Недопустимый переход для текущего этапа заказа.")
    if action in ("start", "complete") and not order.employee_id:
        raise ValidationError("Сначала назначьте мастера.")
    if action == "pay":
        if order.amount is None or order.amount <= 0:
            raise ValidationError("Перед отметкой оплаты укажите положительную стоимость.")
        order.paid_at = timezone.now()
    if action == "complete":
        order.completed_at = timezone.now()
    order.status = transitions[action][1]
    order.save(update_fields=("status", "paid_at", "completed_at"))
    record_event(order, actor, {"start": "Мастер принял заказ и приступил", "complete": "Мастер подтвердил завершение", "pay": "Подтверждена оплата"}[action])
    return order


def record_event(order, actor, description):
    OrderEvent.objects.create(order=order, actor=actor, actor_name=(actor.get_full_name() or actor.username) if actor else "Система", description=description)


@transaction.atomic
def assign_order(pk, employee_id, actor):
    from apps.accounts.models import User
    order = Order.objects.select_for_update().get(pk=pk)
    if order.status != Order.Status.NEW or order.employee_id:
        raise ValidationError("Заказ уже назначен. Обновите доску.")
    skilled = User.objects.filter(role="worker", is_active=True, services=order.service_id)
    if employee_id is not None:
        skilled = skilled.filter(pk=employee_id)
    if not skilled.exists():
        raise ValidationError(f'Нет активного мастера с услугой «{order.service.name}». Руководителю нужно указать эту услугу у подходящего сотрудника в разделе «Сотрудники».')
    workers = list(skilled.select_for_update().filter(is_available=True).order_by("pk"))
    if not workers:
        raise ValidationError(f'Мастера с услугой «{order.service.name}» сейчас недоступны. Проверьте доступность в разделе «Сотрудники».')
    candidates = []
    for worker in workers:
        load = Order.objects.filter(employee=worker, status__in=("new", "assigned", "in_progress")).count()
        load += Lead.objects.filter(employee=worker, order__isnull=True, status__in=("new", "assigned", "in_progress")).count()
        if load < worker.max_active_leads and (employee_id is None or worker.pk == employee_id):
            candidates.append((load, worker.pk, worker))
    if not candidates:
        raise ValidationError("У подходящих мастеров достигнут лимит активных заказов. Дождитесь завершения работы или проверьте лимиты в разделе «Сотрудники».")
    order.employee = min(candidates, key=lambda item: item[:2])[2]
    order.status = Order.Status.ASSIGNED
    order.save(update_fields=("employee", "status"))
    TelegramNotice.objects.filter(order=order, active=True).update(active=False)
    TelegramNotice.objects.create(order=order, employee=order.employee)
    record_event(order, actor, f"Назначен мастер: {order.employee}")
    return order
