from pathlib import Path
from django.contrib.staticfiles import finders
from apps.accounts.access import allowed
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render

from .models import Lead


@login_required(login_url="login")
@permission_required(("leads.view_lead", "customers.view_client"), raise_exception=True)
def lead_list(request):
    if not allowed(request.user, "manager", "operator"):
        raise PermissionDenied
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if status not in Lead.Status.values:
        status = ""
    leads = Lead.objects.select_related("client", "service", "employee").order_by("-created_at", "-pk")
    if query:
        leads = leads.filter(
            Q(client__name__icontains=query)
            | Q(client__phone__icontains=query)
            | Q(title__icontains=query)
            | Q(service__name__icontains=query)
            | Q(source__icontains=query)
        )
    if status:
        leads = leads.filter(status=status)
    page = Paginator(leads, 12).get_page(request.GET.get("page"))
    return render(request, "leads/lead_list.html", {
        "page_obj": page, "query": query, "selected_status": status,
        "statuses": Lead.Status.choices,
    })


@login_required(login_url="login")
@permission_required(("leads.view_lead", "customers.view_client"), raise_exception=True)
def kanban(request):
    if not allowed(request.user, "manager", "operator"):
        raise PermissionDenied
    paths = [finders.find(name) for name in ("kanban/kanban.js", "kanban/kanban.css")]
    version = max((Path(path).stat().st_mtime_ns for path in paths if path), default=0)
    return render(request, "leads/kanban.html", {"kanban_version": version})
