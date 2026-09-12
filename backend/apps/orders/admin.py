from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from apps.leads.models import TelegramNotice
from .models import Order
from .services import transition_order


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "client", "service", "employee", "status", "amount", "paid_at")
    search_fields = ("title", "client__name", "client__phone")
    autocomplete_fields = ("client", "service", "employee")
    list_select_related = ("client", "service", "employee")
    list_filter = ("status",)
    readonly_fields = ("lead", "status", "created_at", "completed_at", "paid_at")
    actions = ("start_work", "complete_work", "mark_paid")

    def get_readonly_fields(self, request, obj=None):
        fields = self.readonly_fields
        if obj and obj.status in (Order.Status.COMPLETED, Order.Status.PAID):
            fields += ("employee",)
        if obj and obj.status == Order.Status.PAID:
            fields += ("amount",)
        return fields

    def save_model(self, request, obj, form, change):
        previous = Order.objects.get(pk=obj.pk) if change else None
        employee_changed = previous is None or previous.employee_id != obj.employee_id
        if obj.status in (Order.Status.NEW, Order.Status.ASSIGNED):
            obj.status = Order.Status.ASSIGNED if obj.employee_id else Order.Status.NEW
        super().save_model(request, obj, form, change)
        if employee_changed:
            TelegramNotice.objects.filter(order=obj, active=True).update(active=False)
            if obj.employee_id and obj.status == Order.Status.ASSIGNED:
                TelegramNotice.objects.create(order=obj, employee=obj.employee)

    def advance(self, request, queryset, action):
        for pk in queryset.values_list("pk", flat=True):
            try:
                transition_order(pk, action)
                self.message_user(request, f"Заказ № {pk}: этап обновлён.")
            except ValidationError as error:
                self.message_user(request, f"Заказ № {pk}: {'; '.join(error.messages)}", messages.ERROR)

    @admin.action(description="Начать работу", permissions=["change"])
    def start_work(self, request, queryset):
        self.advance(request, queryset, "start")

    @admin.action(description="Отметить выполненным", permissions=["change"])
    def complete_work(self, request, queryset):
        self.advance(request, queryset, "complete")

    @admin.action(description="Подтвердить получение оплаты", permissions=["change"])
    def mark_paid(self, request, queryset):
        self.advance(request, queryset, "pay")
