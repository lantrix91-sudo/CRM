from decimal import Decimal
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from apps.accounts.models import User
from apps.accounts.access import allowed
from .models import Order, SettlementShift, WorkerTransfer
from .services import payment_split


def totals(worker):
    orders = list(Order.objects.filter(employee=worker, status="paid", repeat_of__isnull=True, settlement_shift__isnull=True).order_by("pk"))
    current = Decimal("0")
    worker_amount = Decimal("0")
    missing = []
    for order in orders:
        _, part, manager = payment_split(order)
        if part is None:
            missing.append(order.pk)
        else:
            current += manager
            worker_amount += part
    closed = worker.closed_shifts.aggregate(total=Sum("manager_amount"))["total"] or Decimal("0")
    transferred = worker.settlement_transfers.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    return {"orders": orders, "current": current, "worker_amount": worker_amount,
            "transferred": transferred, "balance": closed + current - transferred, "missing": missing}


@transaction.atomic
def settle(worker_id, actor, action, amount=None, comment="", request_id=None):
    if not allowed(actor, "manager"):
        raise PermissionDenied
    worker = User.objects.select_for_update().get(pk=worker_id, role="worker")
    if action == "transfer" and WorkerTransfer.objects.filter(request_id=request_id, worker=worker).exists():
        return
    data = totals(worker)
    if data["missing"]:
        raise ValidationError("Есть оплаченные заказы без сохранённого процента: " + ", ".join(map(str, data["missing"])))
    if action == "transfer":
        if amount is None or not amount.is_finite() or amount <= 0 or amount > data["balance"]:
            raise ValidationError("Перевод должен быть больше нуля и не превышать долг.")
        WorkerTransfer.objects.create(worker=worker, received_by=actor, amount=amount, comment=comment, request_id=request_id)
    elif action == "close":
        if not data["orders"]:
            raise ValidationError("Нет новых оплаченных заказов для закрытия смены.")
        # Freeze exactly the orders used in this calculation; later payments enter the next shift.
        shift = SettlementShift.objects.create(worker=worker, closed_by=actor, manager_amount=data["current"],
                                                worker_amount=data["worker_amount"], balance=data["balance"])
        Order.objects.filter(pk__in=[o.pk for o in data["orders"]]).update(settlement_shift=shift)
    else:
        raise ValidationError("Неизвестное действие.")
