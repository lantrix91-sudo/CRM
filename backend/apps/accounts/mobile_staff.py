"""Manager/operator mobile endpoints; worker endpoints remain isolated."""
from django.core.exceptions import ValidationError as ModelError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import allowed, role_of
from .forms import LeadForm
from .mobile_api import MobileAuthentication
from .models import User
from apps.customers.models import City, Client
from apps.leads.models import Lead, TelegramNotice
from apps.orders.models import Order
from apps.orders.services import convert_lead, assign_order, return_order, cancel_order
from apps.services.models import Service


class StaffOnly(BasePermission):
    def has_permission(self, request, view):
        return allowed(request.user, "manager", "operator")


class StaffAPI(APIView):
    authentication_classes = [MobileAuthentication]
    permission_classes = [StaffOnly]

    def handle_exception(self, exc):
        if isinstance(exc, ModelError):
            return Response({"detail": "; ".join(exc.messages)}, status=409)
        return super().handle_exception(exc)


def paginate(request, queryset, serializer):
    pager = PageNumberPagination()
    pager.page_size = 25
    rows = pager.paginate_queryset(queryset, request)
    return pager.get_paginated_response([serializer(row) for row in rows])


def lead_data(lead, detail=False):
    data = {"id": lead.pk, "kind": "lead", "name": lead.client.name, "phone": lead.client.phone,
        "address": lead.client.address, "city": lead.city.name if lead.city_id else "",
        "city_id": lead.city_id, "service_id": lead.service_id,
        "service": lead.service.name if lead.service_id else "Не выбрана",
        "status": lead.status, "status_label": lead.get_status_display(), "source": lead.source,
        "scheduled_at": lead.scheduled_at, "comment": lead.comment,
        "appliance_type": lead.appliance_type, "brand": lead.brand}
    if detail:
        data["events"] = list(lead.events.values("id", "description", "actor_name", "created_at"))
    return data


def orders():
    return Order.objects.select_related("client", "service", "city", "employee", "lead").order_by("-created_at", "-pk")


def order_data(order, detail=False):
    data = {"id": order.pk, "kind": "order", "name": order.client.name, "phone": order.client.phone,
        "address": order.client.address, "city": order.city.name if order.city_id else "",
        "city_id": order.city_id, "service_id": order.service_id, "service": order.service.name,
        "status": order.status, "status_label": order.display_status,
        "employee_id": order.employee_id, "employee": str(order.employee) if order.employee else "Не назначен",
        "scheduled_at": order.scheduled_at, "comment": order.comment, "amount": order.amount,
        "work_comment": order.work_comment}
    if detail:
        notice = order.telegram_notices.filter(active=True).order_by("-created_at", "-pk").first()
        data["assignment_notice"] = str(notice.pk) if notice and order.status == "assigned" else None
        data["events"] = list(order.events.values("id", "description", "actor_name", "created_at"))
    return data


class Options(StaffAPI):
    def get(self, request):
        workers = User.objects.filter(role="worker", is_active=True).prefetch_related("services", "service_cities").order_by("username")
        return Response({"cities": list(City.objects.filter(is_active=True).values("id", "name")),
            "services": list(Service.objects.order_by("name").values("id", "name")),
            "workers": [{"id": w.pk, "name": w.get_full_name() or w.username, "available": w.is_available,
                "service_ids": [s.pk for s in w.services.all()], "city_ids": [c.pk for c in w.service_cities.all()]} for w in workers]})


class Leads(StaffAPI):
    def get(self, request):
        rows = Lead.objects.filter(order__isnull=True).select_related("client", "service", "city").order_by("-created_at", "-pk")
        if request.query_params.get("scope") != "history":
            rows = rows.exclude(status__in=("converted", "lost", "won"))
        else:
            rows = rows.filter(status__in=("lost", "won"))
        query = request.query_params.get("q", "").strip()
        if query:
            rows = rows.filter(Q(client__name__icontains=query) | Q(client__phone__icontains=query))
        return paginate(request, rows, lead_data)

    def post(self, request):
        if not isinstance(request.data, dict):
            raise ValidationError("Некорректный запрос.")
        form = LeadForm(request.data)
        form.instance._history_actor = request.user
        if not form.is_valid():
            return Response(form.errors, status=400)
        return Response(lead_data(form.save(), True), status=201)


class LeadDetail(StaffAPI):
    def get(self, request, pk):
        return Response(lead_data(get_object_or_404(Lead.objects.select_related("client", "service", "city"), pk=pk), True))

    @transaction.atomic
    def post(self, request, pk):
        lead = get_object_or_404(Lead.objects.select_for_update(), pk=pk)
        if not isinstance(request.data, dict):
            raise ValidationError("Некорректный запрос.")
        action = request.data.get("action")
        if action == "convert":
            order, _ = convert_lead(pk, request.user)
            return Response(order_data(order, True))
        if lead.status in ("lost", "converted", "won") or Order.objects.filter(lead=lead).exists():
            raise ModelError("Обращение закрыто или уже преобразовано в заказ.")
        if action == "save":
            form = LeadForm(request.data, instance=lead)
            form.instance._history_actor = request.user
            if not form.is_valid():
                return Response(form.errors, status=400)
            return Response(lead_data(form.save(), True))
        raise ValidationError("Неизвестное действие.")


class Orders(StaffAPI):
    def get(self, request):
        rows = orders()
        closed = Q(status__in=("paid", "cancelled")) | (Q(status="completed") & (Q(is_free=True) | Q(repeat_of__isnull=False)))
        rows = rows.filter(closed) if request.query_params.get("scope") == "history" else rows.exclude(closed)
        query = request.query_params.get("q", "").strip()
        if query:
            rows = rows.filter(Q(client__name__icontains=query) | Q(client__phone__icontains=query) | Q(title__icontains=query))
        return paginate(request, rows, order_data)


class ActionInput(serializers.Serializer):
    action = serializers.ChoiceField(choices=("assign", "return", "cancel"))
    employee_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    reason = serializers.CharField(max_length=300, required=False, default="")
    expected_notice = serializers.CharField(max_length=36, required=False, default="")


class OrderDetail(StaffAPI):
    def get(self, request, pk):
        return Response(order_data(get_object_or_404(orders(), pk=pk), True))

    def post(self, request, pk):
        get_object_or_404(Order, pk=pk)
        form = ActionInput(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        if data["action"] == "assign":
            order = assign_order(pk, data.get("employee_id"), request.user)
        elif data["action"] == "return":
            order = return_order(pk, request.user, data["reason"], data["expected_notice"])
        else:
            order = cancel_order(pk, request.user, data["reason"])
        return Response(order_data(order, True))


class Clients(StaffAPI):
    def get(self, request):
        rows = Client.objects.annotate(lead_count=Count("leads", distinct=True), order_count=Count("orders", distinct=True)).order_by("name", "pk")
        query = request.query_params.get("q", "").strip()
        if query:
            rows = rows.filter(Q(name__icontains=query) | Q(phone__icontains=query))
        return paginate(request, rows, lambda c: {"id": c.pk, "name": c.name, "phone": c.phone,
            "address": c.address, "lead_count": c.lead_count, "order_count": c.order_count})


class Dashboard(StaffAPI):
    def get(self, request):
        if role_of(request.user) != "manager":
            raise PermissionDenied("Финансовая сводка доступна руководителю.")
        from apps.orders.reporting import local_day_bounds
        start, end = local_day_bounds(timezone.localdate())
        paid = Order.objects.filter(status="paid", repeat_of__isnull=True, paid_at__gte=start, paid_at__lt=end)
        return Response({"date": timezone.localdate(), "paid_count": paid.count(),
            "revenue": paid.aggregate(value=Sum("amount"))["value"] or 0,
            "expenses": paid.aggregate(value=Sum("expenses"))["value"] or 0,
            "unassigned": Order.objects.filter(status="new", employee__isnull=True).count(),
            "active_orders": Order.objects.filter(status__in=("new", "assigned", "in_progress")).count(),
            "new_leads": Lead.objects.filter(order__isnull=True).exclude(status__in=("lost", "won", "converted")).count()})


class Team(StaffAPI):
    def get(self, request):
        if role_of(request.user) != "manager":
            raise PermissionDenied("Команда доступна руководителю.")
        rows = User.objects.filter(role="worker").prefetch_related("services", "service_cities", "service_rates__service").annotate(
            active_orders=Count("orders", filter=Q(orders__status__in=("new", "assigned", "in_progress")), distinct=True)).order_by("username")
        return paginate(request, rows, lambda w: {"id": w.pk, "name": w.get_full_name() or w.username,
            "available": w.is_available, "active": w.is_active, "active_orders": w.active_orders,
            "cities": [c.name for c in w.service_cities.all()],
            "rates": [{"service_id": r.service_id, "service": r.service.name,
                "percentage": r.worker_percentage, "active": r.active} for r in w.service_rates.all()]})


class AvailabilityInput(serializers.Serializer):
    available = serializers.BooleanField()


class WorkerAvailability(StaffAPI):
    def post(self, request, pk):
        if role_of(request.user) != "manager":
            raise PermissionDenied("Только руководитель может менять доступность мастера.")
        data = AvailabilityInput(data=request.data)
        data.is_valid(raise_exception=True)
        worker = get_object_or_404(User, pk=pk, role="worker", is_active=True)
        worker.is_available = data.validated_data["available"]
        worker.save(update_fields=("is_available",))
        return Response({"id": worker.pk, "available": worker.is_available})
