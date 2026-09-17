"""Worker-only API for the native mobile client."""
import hashlib
import secrets
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import authenticate
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission, AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from .models import MobileSession
from .forms import OrderCompletionForm, ReceivedPaymentForm
from apps.orders.models import Order
from apps.orders.services import complete_order_with_payment, transition_order, record_event, payment_split
from apps.orders.reporting import worker_dashboard
from apps.leads.models import TelegramNotice


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class MobileAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        session = MobileSession.objects.select_related("user").filter(
            token_hash=digest(header[7:]), expires_at__gt=timezone.now(),
        ).first()
        if not session or not session.user.is_active or session.password_hash != digest(session.user.password):
            raise AuthenticationFailed("Сеанс истёк. Войдите снова.")
        return session.user, session

    def authenticate_header(self, request):
        return "Bearer"


class WorkerOnly(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_active and request.user.role == "worker" and not request.user.is_superuser


class WorkerAPI(APIView):
    authentication_classes = [MobileAuthentication]
    permission_classes = [WorkerOnly]


class LoginThrottle(AnonRateThrottle):
    rate = "10/min"


class LoginInput(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=256, trim_whitespace=False)


class Login(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]

    def post(self, request):
        data = LoginInput(data=request.data)
        data.is_valid(raise_exception=True)
        user = authenticate(request=request, **data.validated_data)
        if not user or user.role != "worker" or user.is_superuser:
            raise AuthenticationFailed("Неверный логин или пароль либо нет доступа мастера.")
        token = secrets.token_urlsafe(32)
        MobileSession.objects.filter(user=user, expires_at__lte=timezone.now()).delete()
        MobileSession.objects.create(user=user, token_hash=digest(token),
            password_hash=digest(user.password), expires_at=timezone.now() + timedelta(days=7))
        return Response({"token": token, "name": user.get_full_name() or user.username})


class Logout(WorkerAPI):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        request.auth.delete()
        return Response(status=204)


def order_data(order, detail=False):
    contact = order.status in ("in_progress", "completed", "paid")
    result = {
        "id": order.pk, "title": order.title, "service": order.service.name,
        "status": order.status, "status_label": order.display_status,
        "is_free": order.completed_free, "repeat_of": order.repeat_of_id,
        "client": order.client.name if contact else None,
        "phone": order.client.phone if contact else None,
        "address": order.client.address, "appliance_type": order.appliance_type,
        "brand": order.brand, "comment": order.comment if contact else "",
        "amount": str(order.amount) if order.amount is not None else None,
        "expenses": str(order.expenses), "work_comment": order.work_comment,
        "actions": (["start", "reject"] if order.status == "assigned" else
                    ["complete"] if order.status == "in_progress" else
                    ["payment"] if order.status == "completed" and not order.completed_free else []),
    }
    if detail:
        result["events"] = list(order.events.values("id", "description", "actor_name", "created_at"))
    return result


def own_orders(user):
    return Order.objects.filter(employee=user).select_related("client", "service")


class Orders(WorkerAPI):
    def get(self, request):
        closed = Q(status__in=("paid", "cancelled")) | (Q(status="completed") & (Q(is_free=True) | Q(repeat_of__isnull=False)))
        orders = own_orders(request.user)
        orders = orders.filter(closed) if request.query_params.get("scope") == "history" else orders.exclude(closed)
        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(orders.order_by("-created_at", "-pk"), request)
        return paginator.get_paginated_response([order_data(o) for o in page])


class OrderDetail(WorkerAPI):
    def get(self, request, pk):
        return Response(order_data(get_object_or_404(own_orders(request.user), pk=pk), detail=True))


class OrderAction(WorkerAPI):
    def post(self, request, pk):
        if not isinstance(request.data, dict):
            raise ValidationError("Некорректный запрос.")
        try:
            with transaction.atomic():
                order = get_object_or_404(Order.objects.select_for_update().filter(employee=request.user), pk=pk)
                action = request.data.get("action")
                if action == "start":
                    transition_order(pk, "start", actor=request.user)
                elif action == "reject":
                    if order.status != "assigned":
                        raise DjangoValidationError("Отклонить можно только ещё не принятый заказ.")
                    order.employee = None
                    order.status = "new"
                    order.save(update_fields=("employee", "status"))
                    TelegramNotice.objects.filter(order=order, active=True).update(active=False)
                    record_event(order, request.user, "Мастер отклонил заказ в приложении")
                    return Response({"rejected": True})
                elif action == "preview":
                    if order.status != "in_progress":
                        raise DjangoValidationError("Заказ уже изменился. Обновите карточку.")
                    form = OrderCompletionForm(request.data)
                    if not form.is_valid():
                        return Response(form.errors, status=400)
                    data = form.cleaned_data
                    if order.repeat_of_id and (data["amount"] or data["expenses"]):
                        raise DjangoValidationError("Повторный ремонт выполняется бесплатно.")
                    percentage = str(request.user.percentage) if request.user.percentage is not None else None
                    net, worker, company = payment_split(order, amount=data["amount"],
                        expenses=data["expenses"], worker_percentage=request.user.percentage)
                    payload = {"order": pk, "worker": request.user.pk, "percentage": percentage,
                        "amount": str(data["amount"]), "expenses": str(data["expenses"]), "comment": data["comment"]}
                    return Response({**payload, "net": str(net),
                        "worker_share": str(worker) if worker is not None else None,
                        "company_share": str(company) if company is not None else None,
                        "confirmation": signing.dumps(payload, salt="mobile-completion")})
                elif action == "complete":
                    try:
                        data = signing.loads(request.data.get("confirmation", ""), salt="mobile-completion", max_age=900)
                    except (signing.BadSignature, TypeError):
                        raise DjangoValidationError("Предпросмотр устарел. Повторите расчёт.")
                    percentage = str(request.user.percentage) if request.user.percentage is not None else None
                    if data["order"] != pk or data["worker"] != request.user.pk or data["percentage"] != percentage:
                        raise DjangoValidationError("Данные изменились. Повторите расчёт.")
                    complete_order_with_payment(pk, Decimal(data["amount"]), Decimal(data["expenses"]),
                                                data["comment"], actor=request.user)
                elif action == "payment":
                    if order.status != "completed" or order.completed_free:
                        raise DjangoValidationError("Оплата недоступна для этого заказа.")
                    form = ReceivedPaymentForm(request.data)
                    if not form.is_valid():
                        return Response(form.errors, status=400)
                    amount = form.cleaned_data["received_amount"]
                    if amount < order.expenses:
                        raise DjangoValidationError("Сумма не может быть меньше расходов.")
                    order.received_amount = order.amount = amount
                    order.received_method = form.cleaned_data["received_method"]
                    order.received_at = timezone.now()
                    order.save(update_fields=("amount", "received_amount", "received_method", "received_at"))
                    transition_order(pk, "pay", actor=request.user)
                else:
                    raise DjangoValidationError("Неизвестное действие.")
                order.refresh_from_db()
                return Response(order_data(order, detail=True))
        except DjangoValidationError as error:
            return Response({"detail": "; ".join(error.messages)}, status=409)


class Profile(WorkerAPI):
    def get(self, request):
        return Response({"name": request.user.get_full_name() or request.user.username,
                         "percentage": request.user.percentage, "dashboard": worker_dashboard(request.user)})
