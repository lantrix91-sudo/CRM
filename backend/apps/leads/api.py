from apps.accounts.access import allowed
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Lead, TelegramNotice


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
    city_name = serializers.CharField(source="city.name", read_only=True)

    class Meta:
        model = Lead
        fields = ("id", "employee_id", "title", "client_name", "phone", "service_id", "service_name", "employee_name", "source", "status", "city_id", "city_name", "scheduled_at")

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
        city_id = request.query_params.get("city")
        if city_id and city_id.isdigit():
            city_id = int(city_id)
        else:
            city_id = None
        city_filter = {"city_id": city_id} if city_id else {}
        leads = Lead.objects.select_related("client", "service", "employee", "city", ).filter( **city_filter ).order_by("-created_at", "-pk",)
        from apps.accounts.models import User
        workers = User.objects.filter(role="worker", is_active=True, is_available=True,)
        if city_id:
            workers = workers.filter(service_cities__id=city_id)
        workers = workers.prefetch_related( "services", "service_cities", ).order_by("username")
        from apps.orders.models import Order
        from django.utils import timezone
        notices = {}
        for notice in TelegramNotice.objects.filter(order__isnull=False, active=True).order_by("-created_at", "-pk"):
            notices.setdefault(notice.order_id, notice)
        cards = []
        client_history = []
        for lead in leads.filter(order__isnull=True).select_related("lost_by"):
            card = dict(LeadCardSerializer(lead).data)
            card.update(key=f"lead-{lead.pk}", kind="lead", status="lost" if lead.status == "lost" else "new", detail=(f"Причина: {lead.lost_reason or 'Не указана'}. Закрыл: {lead.lost_by or 'Не указан'}. Дата: {lead.lost_at.strftime('%d.%m.%Y') if lead.lost_at else 'Не указана'}" if lead.status == "lost" else "Ожидает согласия клиента"))
            cards.append(card)
            client_history.append((lead.created_at, card["key"], lead.client_id, lead.client.phone))
        for order in Order.objects.select_related("client", "service", "employee", "lead", "city").filter(**city_filter).order_by("-created_at"):
            notice = notices.get(order.pk)
            waiting = max(0, int((timezone.now() - notice.created_at).total_seconds() // 60)) if notice and order.status == "assigned" else None
            delivery = None
            if order.employee_id and order.status in ("assigned", "in_progress"):
                delivery = "Отправлено в Telegram" if notice and notice.state == "sent" else "Ошибка доставки · повтор автоматически" if notice and notice.attempts else "Ждёт отправки"
                if not order.employee.telegram_chat_id:
                    delivery = "Telegram не подключён"
                elif not notice:
                    delivery = "Нет уведомления в очереди"
            cards.append({"delivery": delivery, "waiting_minutes": waiting, "overdue": waiting is not None and waiting >= 15,
                "assignment_notice": str(notice.pk) if notice and order.status == "assigned" else None,
                "repeat_of_id": order.repeat_of_id, "id": order.pk, "key": f"order-{order.pk}", "kind": "order", "title": order.title,
                "client_name": order.client.name, "phone": order.client.phone, "service_id": order.service_id,
                "service_name": order.service.name, "employee_id": order.employee_id,
                "city_id": order.city_id, "city_name": order.city.name if order.city_id else "",
                "scheduled_at": order.scheduled_at,
                "employee_name": (order.employee.get_full_name() or order.employee.username) if order.employee else None,
                "source": order.lead.source if order.lead else "", "status": order.board_status,
                "detail": order.status_detail})
            client_history.append((order.lead.created_at if order.lead else order.created_at,
                                   f"order-{order.pk}", order.client_id, order.client.phone))
        from apps.customers.phones import normalize_phone
        previous_counts = {}
        seen_clients = {}
        for created_at, key, client_id, phone in sorted(client_history):
            try:
                identity = ("phone", normalize_phone(phone))
            except ValueError:
                identity = ("client", client_id)
            previous_counts[key] = seen_clients.get(identity, 0)
            seen_clients[identity] = previous_counts[key] + 1
        from django.db.models import Count
        workloads = {}
        active_statuses = ("new", "assigned", "in_progress")
        for queryset in (
            Lead.objects.filter(order__isnull=True, status__in=active_statuses),
            Order.objects.filter(status__in=active_statuses),
        ):
            for row in queryset.filter(employee__isnull=False).values("employee_id").annotate(total=Count("pk")):
                employee_id = row["employee_id"]
                workloads[employee_id] = workloads.get(employee_id, 0) + row["total"]
        for card in cards:
            card["client_previous_count"] = previous_counts[card["key"]]
            card["employee_active_count"] = workloads.get(card["employee_id"], 0)
        return Response({
            "workers": [{"id": worker.pk, "name": worker.get_full_name() or worker.username, "service_ids": [service.pk for service in worker.services.all()], "city_ids": [city.pk for city in worker.service_cities.all()]} for worker in workers],
            "leads": cards,
            "columns": [{"id": key, "label": label} for key, label in (("new", "Новый"), ("in_progress", "В работе"), ("assigned", "Назначен"), ("completed", "Завершён"), ("paid", "Закрыт"), ("lost", "Неудачные сделки"))],
            "archived_count": leads.filter(status=Lead.Status.LOST).count(),
            "can_change": request.user.has_perm("leads.change_lead"),
            "can_manage": allowed(request.user, "manager"),
            "can_add": request.user.has_perm("leads.add_lead"),
            "cities": self._city_counts(request),
        })

    @staticmethod
    def _city_counts(request):
        from apps.customers.models import City
        from django.db.models import Count, Q
        lead_q = Q(leads__order__isnull=True, leads__status__in=("new", "assigned", "in_progress"))
        order_q = Q(orders__status__in=("new", "assigned", "in_progress"))
        rows = City.objects.filter(is_active=True).annotate(
            lead_count=Count("leads", filter=lead_q, distinct=True),
            order_count=Count("orders", filter=order_q, distinct=True),
        ).order_by("name")
        return [{"id": city.pk, "name": city.name, "count": city.lead_count + city.order_count} for city in rows]


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
        from apps.orders.services import assign_order, return_order
        from apps.orders.models import Order
        from django.core.exceptions import ValidationError
        if not isinstance(request.data, dict) or set(request.data) - {"employee_id", "reason", "expected_notice", "action"}:
            return Response({"detail": "Некорректные поля."}, status=400)
        employee_id = request.data.get("employee_id")
        if "employee_id" in request.data and (type(employee_id) is not int or employee_id <= 0):
            return Response({"detail": "Выберите мастера."}, status=400)
        try:
            if "expected_notice" in request.data and "reason" not in request.data:
                return Response({"detail": "Укажите причину переназначения."}, status=400)
            if "reason" in request.data and not isinstance(request.data["reason"], str):
                return Response({"detail": "Укажите причину переназначения."}, status=400)
            if request.data.get("action") == "return":
                if "employee_id" in request.data:
                    return Response({"detail": "При возврате мастер не назначается."}, status=400)
                return_order(pk, request.user, request.data.get("reason"), request.data.get("expected_notice"))
            elif "action" in request.data or "reason" in request.data or "expected_notice" in request.data:
                return Response({"detail": "Сначала верните заказ оператору, затем назначьте мастера."}, status=400)
            else:
                assign_order(pk, employee_id, request.user)
        except Order.DoesNotExist:
            return Response(status=404)
        except ValidationError as error:
            return Response({"detail": "; ".join(error.messages)}, status=409)
        return Response({"id": pk})
