from decimal import Decimal

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
        appliance_type=lead.appliance_type, brand=lead.brand, comment=lead.comment,
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
        if order.repeat_of_id or order.is_free:
            raise ValidationError("Повторный ремонт выполняется бесплатно и не требует оплаты.")
        if order.amount is None or order.amount <= 0:
            raise ValidationError("Перед отметкой оплаты укажите положительную стоимость.")
        order.worker_percentage = order.employee.percentage if order.employee else None
        order.paid_at = timezone.now()
    if action == "complete":
        order.completed_at = timezone.now()
    order.status = transitions[action][1]
    order.save(update_fields=("status", "paid_at", "completed_at", "worker_percentage"))
    record_event(order, actor, {"start": "Мастер принял заказ и приступил", "complete": "Мастер подтвердил завершение", "pay": "Подтверждена оплата"}[action])
    return order


@transaction.atomic
def complete_order_with_payment(pk, amount, expenses, comment, actor=None, completion_event=None):
    """Persist the shared Telegram/Web completion sequence for an ordinary order."""
    order = Order.objects.select_for_update().get(pk=pk)
    if order.status != Order.Status.IN_PROGRESS:
        raise ValidationError("Завершить заказ можно только в работе.")
    if order.repeat_of_id and (amount != Decimal("0") or expenses != Decimal("0")):
        raise ValidationError("Повторный ремонт выполняется бесплатно.")
    if amount is None or amount < Decimal("0"):
        raise ValidationError("Укажите сумму от 0.")
    if expenses is None or expenses < Decimal("0") or expenses > amount:
        raise ValidationError("Расходы не могут превышать полученную сумму.")
    if not isinstance(comment, str) or not comment.strip() or len(comment.strip()) > 2000:
        raise ValidationError("Укажите, что было сделано (до 2000 символов).")

    order.is_free = amount == 0
    order.amount = amount
    order.expenses = expenses
    order.work_comment = comment.strip()
    order.save(update_fields=("is_free", "amount", "expenses", "work_comment"))
    transition_order(order.pk, "complete", actor=actor)
    if amount > 0:
        order.amount = amount
        order.received_amount = amount
        order.received_at = timezone.now()
        order.save(update_fields=("amount", "received_amount", "received_at"))
        transition_order(order.pk, "pay", actor=actor)
    record_event(order, actor, completion_event or (
        f"Получена оплата: {amount} KZT" + ("; без оплаты" if amount == 0 else "")
    ))
    TelegramNotice.objects.filter(order=order, active=True).update(active=False)
    return order


def record_event(order, actor, description):
    OrderEvent.objects.create(order=order, actor=actor, actor_name=(actor.get_full_name() or actor.username) if actor else "Система", description=description)


@transaction.atomic
def assign_order(pk, employee_id, actor, reason=None, expected_notice=None):
    from apps.accounts.models import User
    order = Order.objects.select_for_update().get(pk=pk)
    previous = order.employee
    reassign = reason is not None
    if reassign:
        current = TelegramNotice.objects.filter(order=order, active=True).order_by("-created_at", "-pk").first()
        if order.status != Order.Status.ASSIGNED or not current or str(current.pk) != expected_notice:
            raise ValidationError("Назначение уже изменилось или мастер приступил. Обновите доску.")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 150:
            raise ValidationError("Укажите причину переназначения (до 150 символов).")
        if employee_id is None or employee_id == order.employee_id:
            raise ValidationError("Выберите другого мастера.")
    elif order.status != Order.Status.NEW or order.employee_id:
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
    record_event(order, actor, f"Переназначен: {previous} → {order.employee}. Причина: {reason.strip()}" if reassign else f"Назначен мастер: {order.employee}")
    return order


@transaction.atomic
def return_order(pk, actor, reason, expected_notice):
    order = Order.objects.select_for_update().get(pk=pk)
    current = TelegramNotice.objects.filter(order=order, active=True).order_by("-created_at", "-pk").first()
    if order.status != Order.Status.ASSIGNED or not current or str(current.pk) != expected_notice:
        raise ValidationError("Назначение уже изменилось или мастер приступил. Обновите доску.")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 150:
        raise ValidationError("Укажите причину возврата (до 150 символов).")
    previous = order.employee
    order.employee = None
    order.status = Order.Status.NEW
    order.save(update_fields=("employee", "status"))
    TelegramNotice.objects.filter(order=order, active=True).update(active=False)
    record_event(order, actor, f"Возвращён оператору от мастера {previous}. Причина: {reason.strip()}")
    return order


@transaction.atomic
def cancel_order(pk, actor, reason):
    from apps.accounts.access import allowed
    from django.core.exceptions import PermissionDenied
    if not allowed(actor, "manager", "operator"):
        raise PermissionDenied
    order = Order.objects.select_for_update().get(pk=pk)
    if order.status == Order.Status.CANCELLED:
        return order
    if order.status not in (Order.Status.NEW, Order.Status.ASSIGNED, Order.Status.IN_PROGRESS) or order.received_at:
        raise ValidationError("Завершённый или оплаченный заказ нельзя отменить этим действием.")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 300:
        raise ValidationError("Укажите причину отмены (до 300 символов).")
    order.status = Order.Status.CANCELLED
    order.cancellation_reason = reason.strip()
    order.cancelled_at = timezone.now()
    order.save(update_fields=("status", "cancellation_reason", "cancelled_at"))
    TelegramNotice.objects.filter(order=order, active=True).update(active=False)
    record_event(order, actor, f"Клиент отказался. Причина: {reason.strip()}")
    return order


@transaction.atomic
def repeat_repair(pk, actor):
    from apps.accounts.access import allowed
    from django.core.exceptions import PermissionDenied
    if not allowed(actor, "manager", "operator"):
        raise PermissionDenied
    original = Order.objects.select_for_update().get(pk=pk)
    if original.status not in (Order.Status.COMPLETED, Order.Status.PAID):
        raise ValidationError("Повторный ремонт доступен после завершения заказа.")
    existing = original.repeat_repairs.exclude(status__in=("completed", "paid", "cancelled")).first()
    if existing:
        return existing
    worker = original.employee
    if not worker or not worker.is_active or worker.role != "worker":
        raise ValidationError("Мастер исходного заказа недоступен. Укажите активного мастера перед повторным ремонтом.")
    repeated = Order.objects.create(
        repeat_of=original, title=f"Повторный ремонт заказа № {original.pk}",
        client=original.client, service=original.service, employee=worker, status=Order.Status.IN_PROGRESS,
        appliance_type=original.appliance_type, brand=original.brand, comment=original.comment,
    )
    TelegramNotice.objects.create(order=repeated, employee=worker)
    record_event(original, actor, f"Создан повторный ремонт: заказ № {repeated.pk}")
    record_event(repeated, actor, f"Повторный ремонт заказа № {original.pk}; назначен мастер: {worker}")
    return repeated


_UNSET = object()


def payment_split(order, amount=None, expenses=None, worker_percentage=_UNSET):
    from decimal import Decimal, ROUND_HALF_UP
    net = (amount if amount is not None else (order.amount or Decimal("0"))) - (
        expenses if expenses is not None else order.expenses
    )
    percentage = order.worker_percentage if worker_percentage is _UNSET else worker_percentage
    if percentage is None:
        return net, None, None
    worker_amount = (net * percentage / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return net, worker_amount, net - worker_amount
