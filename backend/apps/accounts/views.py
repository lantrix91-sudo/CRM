from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Sum, Q
from django.shortcuts import get_object_or_404, redirect, render

from .access import allowed, role_of, roles_required
from .forms import ReceivedPaymentForm, ClientForm, LeadForm, OrderForm, EmployeeForm, ServiceForm
from .models import User
from apps.leads.models import Lead, TelegramNotice
from apps.orders.models import Order
from apps.services.models import Service
from apps.orders.services import convert_lead, transition_order, record_event

@login_required
def home(request):
    role = role_of(request.user)
    if role not in ("manager", "operator", "worker", "curator"):
        return render(request, "accounts/unassigned.html", status=403)
    return redirect({"manager": "manager-home", "operator": "operator-home", "worker": "worker-home", "curator": "profile"}[role])

@roles_required("manager")
def manager_home(request):
    return render(request, "accounts/manager.html", {
        "stages": [{"label": Order.Status(row["status"]).label, "total": row["total"]} for row in Order.objects.values("status").annotate(total=Count("pk"))],
        "workers": User.objects.filter(role="worker").annotate(active_orders=Count("orders", filter=Q(orders__status__in=("new", "assigned", "in_progress")), distinct=True), active_leads=Count("leads", filter=Q(leads__status__in=("new", "assigned", "in_progress")), distinct=True)).order_by("username"),
        "paid": Order.objects.filter(status="paid").aggregate(total=Sum("amount"))["total"] or 0,
        "leads_count": Lead.objects.count(),
        "services": Service.objects.order_by("name"),
    })

@roles_required("manager", "operator")
def operator_home(request):
    return render(request, "accounts/operator.html", {
        "leads": Lead.objects.exclude(status__in=("converted", "lost")).select_related("client", "service").order_by("-created_at")[:100],
        "orders": Order.objects.select_related("client", "employee").order_by("-created_at")[:100],
    })

@roles_required("worker")
def worker_home(request):
    return render(request, "accounts/worker.html", {
        "assigned_leads": Lead.objects.filter(employee=request.user, order__isnull=True, status__in=("new", "assigned", "in_progress")).select_related("client", "service").order_by("-created_at")[:100],
        "orders": Order.objects.filter(employee=request.user).exclude(status__in=("paid", "cancelled")).select_related("client", "service").order_by("-created_at")[:100],
    })

@roles_required("manager", "operator", "worker", "curator")
def order_detail(request, pk):
    with transaction.atomic():
        queryset = Order.objects.all()
        if role_of(request.user) == "worker":
            queryset = queryset.filter(employee=request.user)
        if role_of(request.user) == "curator":
            queryset = queryset.filter(employee__curator=request.user)
            if request.method != "GET":
                raise PermissionDenied
        order = get_object_or_404(queryset.select_for_update(), pk=pk)
        form = None
        payment_form = ReceivedPaymentForm(request.POST if request.method == "POST" and request.POST.get("action") == "received_payment" else None)
        if allowed(request.user, "manager", "operator") and order.status != "cancelled":
            form = OrderForm(request.POST if request.method == "POST" and request.POST.get("action") == "save" else None, instance=order)
        if request.method == "POST":
            action = request.POST.get("action")
            try:
                if action == "repeat_repair":
                    from apps.orders.services import repeat_repair
                    repeated = repeat_repair(pk, request.user)
                    messages.success(request, f"Повторный ремонт № {repeated.pk} назначен мастеру. Уведомление поставлено в очередь Telegram.")
                    return redirect("kanban")
                elif action == "cancel":
                    from apps.orders.services import cancel_order
                    cancel_order(pk, request.user, request.POST.get("reason", ""))
                    if request.POST.get("return_to") == "kanban":
                        return redirect("kanban")
                    return redirect("order-detail", pk=pk)
                elif action == "save":
                    if form is None:
                        raise PermissionDenied
                    previous_employee = order.employee_id
                    if form.is_valid():
                        order = form.save(commit=False)
                        if order.status in ("new", "assigned"):
                            order.status = "assigned" if order.employee_id else "new"
                        order.save()
                        if previous_employee != order.employee_id:
                            record_event(order, request.user, f"Изменён мастер: {order.employee or 'не назначен'}")
                            TelegramNotice.objects.filter(order=order, active=True).update(active=False)
                            if order.employee_id:
                                TelegramNotice.objects.create(order=order, employee=order.employee)
                        return redirect("order-detail", pk=pk)
                elif action == "received_payment":
                    if role_of(request.user) != "worker" or order.employee_id != request.user.pk:
                        raise PermissionDenied
                    if order.status != "completed":
                        raise ValidationError("Сообщить об оплате можно после завершения работы.")
                    if payment_form.is_valid():
                        from django.utils import timezone
                        order.received_amount = payment_form.cleaned_data["received_amount"]
                        order.received_method = payment_form.cleaned_data["received_method"]
                        order.received_at = timezone.now()
                        order.amount = order.received_amount
                        order.save(update_fields=("received_amount", "received_method", "received_at", "amount"))
                        transition_order(pk, "pay", actor=request.user)
                        record_event(order, request.user, f"Сообщил о получении оплаты: {order.received_amount} KZT, {order.get_received_method_display()}")
                        messages.success(request, "Оплата сохранена. Заказ оплачен.")
                        return redirect("order-detail", pk=pk)
                elif action == "pay":
                    if not allowed(request.user, "manager"):
                        raise PermissionDenied
                    transition_order(pk, "pay", actor=request.user)
                    return redirect("order-detail", pk=pk)
                elif action in ("start", "complete"):
                    if role_of(request.user) != "worker" or order.employee_id != request.user.pk:
                        raise PermissionDenied
                    transition_order(pk, action, actor=request.user)
                    return redirect("order-detail", pk=pk)
                elif action == "reject":
                    if role_of(request.user) != "worker" or order.status != "assigned":
                        raise PermissionDenied
                    record_event(order, request.user, "Мастер отклонил заказ")
                    order.employee = None
                    order.status = "new"
                    order.save(update_fields=("employee", "status"))
                    TelegramNotice.objects.filter(order=order, active=True).update(active=False)
                    return redirect("worker-home")
                else:
                    raise PermissionDenied
            except ValidationError as error:
                messages.error(request, "; ".join(error.messages))
        from apps.orders.services import payment_split
        net, worker_amount, manager_amount = payment_split(order)
        return render(request, "accounts/order.html", {"order": order, "form": form, "payment_form": payment_form, "payment_net": net, "payment_worker": worker_amount, "payment_manager": manager_amount})

@roles_required("manager", "operator")
def edit_record(request, kind, pk=None):
    forms = {"client": ClientForm, "lead": LeadForm, "service": ServiceForm}
    if kind == "service" and not allowed(request.user, "manager"):
        raise PermissionDenied
    if kind not in forms:
        raise PermissionDenied
    form_class = forms[kind]
    obj = get_object_or_404(form_class._meta.model, pk=pk) if pk else None
    if request.method == "POST" and request.POST.get("action") == "lost":
        if kind != "lead" or obj is None:
            raise PermissionDenied
        reason = request.POST.get("reason", "").strip()
        with transaction.atomic():
            lead = Lead.objects.select_for_update().get(pk=obj.pk)
            if Order.objects.filter(lead=lead).exists() or lead.status == "converted":
                messages.error(request, "Уже создан заказ. Закрыть его как неудачную сделку нельзя.")
            elif lead.status == "lost":
                messages.info(request, "Сделка уже закрыта.")
            elif not reason or len(reason) > 300:
                messages.error(request, "Укажите причину отказа (до 300 символов).")
            else:
                from django.utils import timezone
                lead.status = "lost"
                lead.lost_reason = reason
                lead.lost_at = timezone.now()
                lead.lost_by = request.user
                lead.save(update_fields=("status", "lost_reason", "lost_at", "lost_by"))
                TelegramNotice.objects.filter(lead=lead, active=True).update(active=False)
                messages.success(request, "Обращение закрыто как неудачная сделка.")
        return redirect("kanban")
    if request.method == "POST" and request.POST.get("action") == "convert":
        if kind != "lead" or obj is None:
            raise PermissionDenied
        try:
            order, _ = convert_lead(obj.pk, actor=request.user)
            if request.POST.get("return_to") == "kanban":
                return redirect("kanban")
            return redirect("order-detail", pk=order.pk)
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
        form = form_class(instance=obj)
    else:
        form = form_class(request.POST or None, instance=obj)
        if request.method == "POST" and form.is_valid():
            form.save()
            return redirect("operator-home")
    return render(request, "accounts/form.html", {"form": form, "kind": kind, "record": obj})

@roles_required("manager")
def employees(request, pk=None):
    obj = get_object_or_404(User, pk=pk, is_superuser=False) if pk else None
    telegram_link = None
    if request.method == "POST" and request.POST.get("action") == "telegram_link":
        if obj is None or obj.role != "worker" or not obj.is_active:
            raise PermissionDenied
        import os, uuid
        from datetime import timedelta
        from django.utils import timezone
        from apps.leads.telegram import TelegramAPI, TelegramError
        try:
            username = TelegramAPI(os.environ.get("TELEGRAM_BOT_TOKEN", "")).call("getMe")["username"]
            with transaction.atomic():
                obj = User.objects.select_for_update().get(pk=obj.pk)
                if obj.telegram_chat_id:
                    messages.error(request, "Telegram уже подключён. Для смены аккаунта сначала очистите Telegram ID и сохраните сотрудника.")
                else:
                    obj.telegram_link_token = uuid.uuid4()
                    obj.telegram_link_expires = timezone.now() + timedelta(hours=24)
                    obj.save(update_fields=("telegram_link_token", "telegram_link_expires"))
                    telegram_link = f"https://t.me/{username}?start=link_{obj.telegram_link_token.hex}"
        except TelegramError as error:
            messages.error(request, str(error))
        form = EmployeeForm(instance=obj)
    else:
        form = EmployeeForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        # Avoid locking the current manager out through their own role editor.
        if obj and obj.pk == request.user.pk and (form.cleaned_data["role"] != "manager" or not form.cleaned_data["is_active"]):
            form.add_error("role", "Нельзя отключить или изменить собственную роль.")
        else:
            form.save()
            return redirect("employees")
    return render(request, "accounts/employees.html", {
        "form": form, "employee_record": obj, "telegram_link": telegram_link, "employees": User.objects.filter(is_superuser=False).order_by("username"),
    })


@roles_required("manager", "operator")
def resolve_call(request, pk):
    from apps.leads.models import IncomingCall
    from .forms import CallResolveForm
    with transaction.atomic():
        call = get_object_or_404(IncomingCall.objects.select_for_update(), pk=pk)
        if call.lead_id:
            return redirect("record-edit", kind="lead", pk=call.lead_id)
        form = CallResolveForm(request.POST or None, phone=call.phone)
        if request.method == "POST" and form.is_valid():
            call.client = form.cleaned_data["client"]
            call.lead = Lead.objects.create(title="Входящий звонок", client=call.client, source="Телефон")
            call.save(update_fields=("client", "lead"))
            return redirect("record-edit", kind="lead", pk=call.lead_id)
        return render(request, "accounts/form.html", {"form": form, "kind": "client"})


@roles_required("worker")
def assigned_lead_detail(request, pk):
    lead = get_object_or_404(Lead.objects.select_related("client", "service").filter(
        employee=request.user, order__isnull=True, status__in=("new", "assigned", "in_progress"),
    ), pk=pk)
    return render(request, "accounts/assigned_lead.html", {"lead": lead})


@roles_required("worker")
def worker_history(request):
    from django.core.paginator import Paginator
    orders = Order.objects.filter(employee=request.user, status__in=("paid", "cancelled")).select_related("client", "service").order_by("-paid_at", "-pk")
    return render(request, "accounts/worker_history.html", {"page_obj": Paginator(orders, 20).get_page(request.GET.get("page"))})


@roles_required("manager", "operator", "worker", "curator")
def profile(request):
    if request.method != "GET":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["GET"])
    from decimal import Decimal, ROUND_HALF_UP
    from apps.orders.reporting import order_breakdown, worker_dashboard
    role = role_of(request.user)
    orders = Order.objects.none()
    if role == "worker":
        orders = Order.objects.filter(employee=request.user)
    elif role == "curator":
        orders = Order.objects.filter(employee__curator=request.user)
    elif role == "manager":
        orders = Order.objects.all()
    orders = orders.select_related("client", "service", "employee").order_by("-created_at")
    paid = orders.filter(status="paid", repeat_of__isnull=True)
    orders = list(orders[:100])
    for order in orders:
        if order.status == "paid":
            order.financial_breakdown = order_breakdown(order)
    total = paid.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    earnings = None if request.user.percentage is None else (total * request.user.percentage / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    expenses_total = paid.aggregate(total=Sum("expenses"))["total"] or Decimal("0")
    if role in ("worker", "manager"):
        from apps.orders.services import payment_split
        earnings = Decimal("0")
        for order in paid.select_related("employee"):
            net, worker_amount, manager_amount = payment_split(order)
            if worker_amount is None:
                earnings = None
                break
            earnings += worker_amount if role == "worker" else manager_amount
    return render(request, "accounts/profile.html", {
        "orders": orders,
        "show_earnings": role in ("worker", "curator", "manager"), "paid_total": total,
        "earnings": earnings, "expenses_total": expenses_total, "net_total": total - expenses_total,
        "dashboard": worker_dashboard(request.user) if role == "worker" else None,
    })


@roles_required("manager", "worker")
def settlement_shift_detail(request, pk):
    from apps.orders.models import SettlementShift
    from apps.orders.reporting import closed_shift_detail
    queryset = SettlementShift.objects.select_related("worker", "closed_by")
    if role_of(request.user) == "worker":
        queryset = queryset.filter(worker=request.user)
    shift = get_object_or_404(queryset, pk=pk)
    return render(request, "accounts/settlement_shift_detail.html", closed_shift_detail(shift))


@roles_required("manager", "worker")
def settlement_export(request, pk=None):
    import openpyxl
    from decimal import Decimal
    from django.http import HttpResponse
    from apps.orders.reporting import export_rows, parse_export_date

    is_manager = allowed(request.user, "manager")
    if is_manager:
        worker = get_object_or_404(User, pk=pk, role="worker") if pk else get_object_or_404(User, pk=request.GET.get("worker"), role="worker")
    else:
        if pk and pk != request.user.pk:
            raise PermissionDenied
        worker = request.user
    date_from = parse_export_date(request.GET.get("date_from"))
    date_to = parse_export_date(request.GET.get("date_to"))
    if request.GET.get("date_from") and date_from is None or request.GET.get("date_to") and date_to is None:
        raise ValidationError("Дата должна быть указана в формате ГГГГ-ММ-ДД.")
    if date_from and date_to and date_from > date_to:
        raise ValidationError("Дата начала не может быть позже даты окончания.")
    rows = export_rows(worker, date_from, date_to)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Финансы"
    headers = ["Дата", "Номер заказа", "Тип операции", "Стоимость услуг", "Расходы", "После расходов", "Процент мастера", "Доля мастера", "Доля компании", "Перевод", "Остаток", "Комментарий"]
    sheet.append(headers)
    for row in rows:
        sheet.append([
            row["date"].strftime("%Y-%m-%d %H:%M") if row["date"] else "",
            row["order_number"], row["operation"], row["service_amount"], row["expenses"],
            row["net_amount"], row["worker_percentage"], row["worker_amount"],
            row["company_amount"], row["transfer_amount"], row["remaining_balance"], row["comment"],
        ])
    totals_row = len(rows) + 3
    sheet.cell(totals_row, 1, "Итого по операциям")
    for column, key in ((4, "service_amount"), (5, "expenses"), (6, "net_amount"), (8, "worker_amount"), (9, "company_amount"), (10, "transfer_amount")):
        sheet.cell(totals_row, column, sum((row[key] or Decimal("0") for row in rows), Decimal("0")))
    opening = rows[0]["remaining_balance"] if rows and rows[0]["operation"] == "Входящий остаток" else Decimal("0")
    closing = rows[-1]["remaining_balance"] if rows else opening
    summary_rows = [
        ("Входящий остаток", opening),
        ("Переводы получены", sum((row["transfer_amount"] for row in rows), Decimal("0"))),
        ("Остаток на конец периода", closing),
    ]
    for offset, (label, value) in enumerate(summary_rows, start=1):
        sheet.cell(totals_row + offset, 1, label)
        sheet.cell(totals_row + offset, 2, value)
    for column in range(1, len(headers) + 1):
        sheet.column_dimensions[openpyxl.utils.get_column_letter(column)].width = 18
    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="worker-{worker.pk}-financial-history.xlsx"'
    workbook.save(response)
    return response


@roles_required("manager", "worker")
def settlements(request, pk=None):
    import uuid
    from django import forms
    from apps.orders.settlements import totals, settle
    is_manager = allowed(request.user, "manager")
    if not is_manager:
        if pk is not None and pk != request.user.pk:
            raise PermissionDenied
        pk = request.user.pk
    workers = User.objects.filter(role="worker").order_by("username")
    worker = get_object_or_404(workers, pk=pk) if pk else None
    class TransferForm(forms.Form):
        amount = forms.DecimalField(label="Получено от мастера (KZT)", max_digits=14, decimal_places=2, min_value=0.01)
        comment = forms.CharField(label="Комментарий", max_length=300, required=False)
        request_id = forms.UUIDField(widget=forms.HiddenInput)
    form = TransferForm(request.POST if request.method == "POST" and request.POST.get("action") == "transfer" else None,
                        initial={"request_id": uuid.uuid4()})
    if request.method == "POST":
        if not is_manager or worker is None:
            raise PermissionDenied
        action = request.POST.get("action")
        try:
            if action == "close":
                settle(worker.pk, request.user, action)
            elif action == "transfer" and form.is_valid():
                settle(worker.pk, request.user, action, **form.cleaned_data)
            else:
                return render(request, "accounts/settlements.html", {"worker": worker, "workers": workers, "summary": totals(worker), "form": form, "can_manage": is_manager})
            return redirect("worker-settlement", pk=worker.pk)
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
    return render(request, "accounts/settlements.html", {"worker": worker, "workers": workers,
                  "summary": totals(worker) if worker else None, "form": form, "can_manage": is_manager})
