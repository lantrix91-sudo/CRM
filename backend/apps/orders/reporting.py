from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from .models import Order, SettlementShift, WorkerTransfer
from .services import payment_split
from .settlements import totals


def order_breakdown(order):
    net_amount, worker_amount, company_amount = payment_split(order)
    return {
        "service_amount": order.amount or Decimal("0"),
        "expenses": order.expenses or Decimal("0"),
        "net_amount": net_amount,
        "worker_percentage": order.worker_percentage,
        "worker_amount": worker_amount,
        "company_amount": company_amount,
    }


def financial_summary(orders):
    summary = {
        "order_count": 0,
        "revenue": Decimal("0"),
        "expenses": Decimal("0"),
        "worker_amount": Decimal("0"),
        "company_amount": Decimal("0"),
        "net_amount": Decimal("0"),
        "complete": True,
    }
    for order in orders:
        breakdown = order_breakdown(order)
        summary["order_count"] += 1
        summary["revenue"] += breakdown["service_amount"]
        summary["expenses"] += breakdown["expenses"]
        summary["net_amount"] += breakdown["net_amount"]
        if breakdown["worker_amount"] is None:
            summary["complete"] = False
        else:
            summary["worker_amount"] += breakdown["worker_amount"]
            summary["company_amount"] += breakdown["company_amount"]
    if not summary["complete"]:
        summary["worker_amount"] = None
        summary["company_amount"] = None
    return summary


def paid_orders(worker, start=None, end=None):
    queryset = Order.objects.filter(
        employee=worker, status=Order.Status.PAID, repeat_of__isnull=True,
    ).select_related("client", "service", "employee")
    if start:
        queryset = queryset.filter(Q(paid_at__gte=start) | Q(paid_at__isnull=True, created_at__gte=start))
    if end:
        queryset = queryset.filter(Q(paid_at__lt=end) | Q(paid_at__isnull=True, created_at__lt=end))
    return queryset.order_by("-paid_at", "-created_at", "-pk")


def local_day_bounds(day):
    current_tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), current_tz)
    return start, start + timedelta(days=1)


def worker_dashboard(worker, now=None):
    now = now or timezone.localtime()
    today_start, today_end = local_day_bounds(now.date())
    month_start, _ = local_day_bounds(now.date().replace(day=1))
    today = financial_summary(paid_orders(worker, today_start, today_end))
    month = financial_summary(paid_orders(worker, month_start, now + timedelta(microseconds=1)))
    settlement = totals(worker)
    current_orders = settlement["orders"]
    current = financial_summary(current_orders)
    current["balance_due"] = settlement["total_balance_due"]
    current["transferred_all_time"] = settlement["total_transferred_all_time"]
    return {"today": today, "current_shift": current, "month": month}


def shift_opened_at(shift):
    previous = SettlementShift.objects.filter(
        worker=shift.worker, closed_at__lt=shift.closed_at,
    ).order_by("-closed_at", "-pk").first()
    if previous:
        return previous.closed_at
    first_order = shift.orders.order_by("created_at", "pk").first()
    return first_order.created_at if first_order else shift.closed_at


def closed_shift_detail(shift):
    orders = list(shift.orders.select_related("client", "service", "employee").order_by("pk"))
    order_rows = [{"order": order, "breakdown": order_breakdown(order)} for order in orders]
    summary = financial_summary(orders)
    summary["worker_amount"] = shift.worker_amount
    summary["company_amount"] = shift.manager_amount
    opened_at = shift_opened_at(shift)
    transfers = WorkerTransfer.objects.filter(
        worker=shift.worker, created_at__gt=opened_at, created_at__lte=shift.closed_at,
    ).order_by("created_at", "pk")
    return {
        "shift": shift,
        "orders": order_rows,
        "summary": summary,
        "opened_at": opened_at,
        "transfers": transfers,
    }


def parse_export_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def export_rows(worker, date_from=None, date_to=None):
    start = local_day_bounds(date_from)[0] if date_from else None
    end = local_day_bounds(date_to)[1] if date_to else None
    orders = paid_orders(worker, start, end)
    transfers = WorkerTransfer.objects.filter(worker=worker)
    if start:
        transfers = transfers.filter(created_at__gte=start)
    if end:
        transfers = transfers.filter(created_at__lt=end)
    opening_balance = export_opening_balance(worker, start)
    rows = []
    if opening_balance:
        rows.append({
            "date": start,
            "order_number": "",
            "operation": "Входящий остаток",
            "service_amount": Decimal("0"),
            "expenses": Decimal("0"),
            "net_amount": Decimal("0"),
            "worker_percentage": None,
            "worker_amount": Decimal("0"),
            "company_amount": Decimal("0"),
            "transfer_amount": Decimal("0"),
            "remaining_balance": opening_balance,
            "comment": "Долг до начала выбранного периода",
        })
    for order in orders:
        breakdown = order_breakdown(order)
        rows.append({
            "date": order.paid_at or order.created_at,
            "order_number": order.pk,
            "operation": "Оплата заказа",
            **breakdown,
            "transfer_amount": Decimal("0"),
            "remaining_balance": Decimal("0"),
            "comment": "",
        })
    for transfer in transfers:
        rows.append({
            "date": transfer.created_at,
            "order_number": "",
            "operation": "Перевод от мастера",
            "service_amount": Decimal("0"),
            "expenses": Decimal("0"),
            "net_amount": Decimal("0"),
            "worker_percentage": None,
            "worker_amount": Decimal("0"),
            "company_amount": Decimal("0"),
            "transfer_amount": transfer.amount,
            "remaining_balance": Decimal("0"),
            "comment": transfer.comment,
        })
    rows.sort(key=lambda row: (row["date"], row["order_number"] or 0))
    balance = opening_balance
    for row in rows[1:] if opening_balance else rows:
        balance += row["company_amount"] - row["transfer_amount"]
        row["remaining_balance"] = max(balance, Decimal("0"))
    return rows


def export_opening_balance(worker, start):
    if not start:
        return Decimal("0")
    latest_shift = SettlementShift.objects.filter(
        worker=worker, closed_at__lt=start,
    ).order_by("-closed_at", "-pk").first()
    if latest_shift:
        balance = latest_shift.balance
        transfers = WorkerTransfer.objects.filter(
            worker=worker, created_at__gt=latest_shift.closed_at, created_at__lt=start,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        pre_shift_orders = paid_orders(
            worker, latest_shift.closed_at, start,
        ).filter(settlement_shift__isnull=True)
        balance += sum(
            (order_breakdown(order)["company_amount"] or Decimal("0") for order in pre_shift_orders),
            Decimal("0"),
        )
        return max(balance - transfers, Decimal("0"))

    orders = paid_orders(worker, end=start)
    company_total = sum(
        (order_breakdown(order)["company_amount"] or Decimal("0") for order in orders),
        Decimal("0"),
    )
    transfers = WorkerTransfer.objects.filter(
        worker=worker, created_at__lt=start,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    return max(company_total - transfers, Decimal("0"))
