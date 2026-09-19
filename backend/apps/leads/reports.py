from apps.accounts.access import allowed
from datetime import datetime, time, timedelta
from collections import Counter

from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from apps.customers.models import Client
from .models import IncomingCall, Lead

SOURCES = ("OLX", "Google", "Instagram", "Telegram", "Телефон")


@login_required(login_url="login")
@permission_required("leads.view_analytics", raise_exception=True)
def analytics(request):
    if not allowed(request.user, "manager", "operator"):
        raise PermissionDenied
    today = timezone.localdate()
    start = timezone.make_aware(datetime.combine(today, time.min))
    end = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min))
    grouped = Lead.objects.filter(created_at__gte=start, created_at__lt=end).values("source").annotate(total=Count("pk"))
    counts = Counter()
    aliases = {source.casefold(): source for source in SOURCES}
    aliases["phone"] = "Телефон"
    for row in grouped:
        raw = row["source"].strip()
        counts[aliases.get(raw.casefold(), "Другие" if raw else "Не указан")] += row["total"]
    labels = (*SOURCES, "Другие", "Не указан")
    return render(request, "leads/analytics.html", {
        "today": today, "rows": [(label, counts[label]) for label in labels], "total": sum(counts.values()),
    })


@login_required(login_url="login")
@permission_required(("customers.view_client", "leads.view_lead", "orders.view_order", "leads.view_incomingcall"), raise_exception=True)
def history(request, pk):
    if not allowed(request.user, "manager", "operator"):
        raise PermissionDenied
    customer = get_object_or_404(Client, pk=pk)
    return render(request, "leads/history.html", {
        "customer": customer,
        "leads": customer.leads.select_related("service").prefetch_related("events").order_by("-created_at")[:50],
        "orders": customer.orders.select_related("service").order_by("-created_at")[:50],
        "calls": customer.calls.order_by("-created_at")[:50],
    })


@login_required(login_url="login")
@permission_required(("leads.view_incomingcall", "customers.view_client"), raise_exception=True)
def calls(request):
    if not allowed(request.user, "manager", "operator"):
        raise PermissionDenied
    page = Paginator(IncomingCall.objects.select_related("client", "lead").order_by("-created_at"), 30).get_page(request.GET.get("page"))
    return render(request, "leads/calls.html", {"page_obj": page})
