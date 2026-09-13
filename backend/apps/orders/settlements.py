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
    current_shift_gross = Decimal("0")
    current_shift_worker_share = Decimal("0")
    current_shift_company_share = Decimal("0")
    missing = []
    for order in orders:
        current_shift_gross += order.amount or Decimal("0")
        _, worker_share, company_share = payment_split(order)
        if worker_share is None:
            missing.append(order.pk)
        else:
            current_shift_company_share += company_share
            current_shift_worker_share += worker_share

    latest_shift = worker.closed_shifts.order_by("-closed_at", "-pk").first()
    transferred = worker.settlement_transfers.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    transferred_since_close = transferred
    carried_debt_at_close = latest_shift.balance if latest_shift else Decimal("0")
    if latest_shift:
        transferred_since_close = worker.settlement_transfers.filter(
            created_at__gt=latest_shift.closed_at
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    # Transfers after a close pay the carried debt first. Any remainder pays
    # the current shift, so each transfer reduces the outstanding balance once.
    carried_debt = max(carried_debt_at_close - transferred_since_close, Decimal("0"))
    current_shift_transfer = max(transferred_since_close - carried_debt_at_close, Decimal("0"))
    current_shift_debt = max(current_shift_company_share - current_shift_transfer, Decimal("0"))
    total_balance_due = carried_debt + current_shift_debt

    return {
        "orders": orders,
        "current_shift_gross": current_shift_gross,
        "current_shift_worker_share": current_shift_worker_share,
        "current_shift_company_share": current_shift_company_share,
        "carried_debt": carried_debt,
        "current_shift_debt": current_shift_debt,
        "total_balance_due": total_balance_due,
        "total_transferred_all_time": transferred,
        # Backwards-compatible keys for callers outside the settlement page.
        "current": current_shift_company_share,
        "worker_amount": current_shift_worker_share,
        "transferred": transferred,
        "balance": total_balance_due,
        "missing": missing,
    }


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
        shift = SettlementShift.objects.create(
            worker=worker,
            closed_by=actor,
            manager_amount=data["current_shift_company_share"],
            worker_amount=data["current_shift_worker_share"],
            balance=data["total_balance_due"],
        )
        Order.objects.filter(pk__in=[o.pk for o in data["orders"]]).update(settlement_shift=shift)
    else:
        raise ValidationError("Неизвестное действие.")
