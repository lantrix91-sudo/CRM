from apps.accounts.access import allowed
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Lead


BOARD_STATUSES = (Lead.Status.NEW, Lead.Status.IN_PROGRESS, Lead.Status.ASSIGNED, Lead.Status.WON)


class BoardPermission(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return (
            allowed(user, "manager", "operator")
            and user.has_perms(("leads.view_lead", "customers.view_client"))
            and (request.method in ("GET", "HEAD", "OPTIONS") or user.has_perm("leads.change_lead"))
        )


class LeadCardSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    phone = serializers.CharField(source="client.phone", read_only=True)
    service_name = serializers.CharField(source="service.name", read_only=True, default="Услуга не выбрана")
    employee_name = serializers.SerializerMethodField()

    class Meta:
        model = Lead
        fields = ("id", "employee_id", "title", "client_name", "phone", "service_id", "service_name", "employee_name", "source", "status")

    def get_employee_name(self, obj):
        if obj.employee is None:
            return None
        return obj.employee.get_full_name() or obj.employee.username


class StatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=BOARD_STATUSES)
    expected_status = serializers.ChoiceField(choices=Lead.Status.choices)

    def validate(self, attrs):
        if set(self.initial_data) - {"status", "expected_status"}:
            raise serializers.ValidationError("Разрешено изменять только статус.")
        return attrs


class BoardAPI(APIView):
    permission_classes = (BoardPermission,)

    def get(self, request):
        leads = Lead.objects.select_related("client", "service", "employee").order_by("-created_at", "-pk")
        from apps.accounts.models import User
        workers = User.objects.filter(role="worker", is_active=True, is_available=True).prefetch_related("services").order_by("username")
        from apps.orders.models import Order
        cards = []
        for lead in leads.filter(order__isnull=True).exclude(status=Lead.Status.LOST):
            card = dict(LeadCardSerializer(lead).data)
            card.update(key=f"lead-{lead.pk}", kind="lead", status="new", detail="Ожидает согласия клиента")
            cards.append(card)
        for order in Order.objects.select_related("client", "service", "employee", "lead").order_by("-created_at"):
            cards.append({"id": order.pk, "key": f"order-{order.pk}", "kind": "order", "title": order.title,
                "client_name": order.client.name, "phone": order.client.phone, "service_id": order.service_id,
                "service_name": order.service.name, "employee_id": order.employee_id,
                "employee_name": (order.employee.get_full_name() or order.employee.username) if order.employee else None,
                "source": order.lead.source if order.lead else "", "status": {"new": "in_progress", "assigned": "assigned", "in_progress": "assigned", "completed": "completed", "paid": "paid"}[order.status],
                "detail": {"new": "Клиент согласился · нужен мастер", "assigned": "Ожидает принятия мастером", "in_progress": "Мастер приступил", "completed": f"Мастер получил {order.received_amount} KZT · проверьте оплату" if order.received_at else "Работа завершена · ожидает оплаты", "paid": "Оплата подтверждена"}[order.status]})
        return Response({
            "workers": [{"id": worker.pk, "name": worker.get_full_name() or worker.username, "service_ids": [service.pk for service in worker.services.all()]} for worker in workers],
            "leads": cards,
            "columns": [{"id": key, "label": label} for key, label in (("new", "Новый"), ("in_progress", "В работе"), ("assigned", "Назначен"), ("completed", "Завершён"), ("paid", "Оплачен"))],
            "archived_count": leads.filter(status=Lead.Status.LOST).count(),
            "can_change": request.user.has_perm("leads.change_lead"),
            "can_manage": allowed(request.user, "manager"),
            "can_add": request.user.has_perm("leads.add_lead"),
        })


class LeadStatusAPI(APIView):
    permission_classes = (BoardPermission,)

    def patch(self, request, pk):
        return Response({"detail": "Статус определяется согласием клиента и действиями мастера. Обновите канбан."}, status=409)


class LeadAssignAPI(APIView):
    permission_classes = (BoardPermission,)

    def post(self, request, pk):
        return Response({"detail": "Сначала подтвердите согласие клиента и создайте заказ."}, status=409)


class OrderAssignAPI(APIView):
    permission_classes = (BoardPermission,)

    def post(self, request, pk):
        from apps.orders.services import assign_order
        from apps.orders.models import Order
        from django.core.exceptions import ValidationError
        if not isinstance(request.data, dict) or set(request.data) - {"employee_id"}:
            return Response({"detail": "Некорректные поля."}, status=400)
        employee_id = request.data.get("employee_id")
        if "employee_id" in request.data and (type(employee_id) is not int or employee_id <= 0):
            return Response({"detail": "Выберите мастера."}, status=400)
        try:
            assign_order(pk, employee_id, request.user)
        except Order.DoesNotExist:
            return Response(status=404)
        except ValidationError as error:
            return Response({"detail": "; ".join(error.messages)}, status=409)
        return Response({"id": pk})
