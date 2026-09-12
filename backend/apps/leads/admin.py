from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from .models import Lead, IncomingCall
from .forms import LeadAdminForm


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    form = LeadAdminForm
    list_display = ("id", "title", "client", "service", "employee", "source", "status", "created_at")
    search_fields = ("title", "client__name", "client__phone", "source")
    list_filter = ("status", "source")
    autocomplete_fields = ("client", "service", "employee")
    list_select_related = ("client", "service", "employee")
    readonly_fields = ("created_at",)

    actions = ("create_orders",)

    @admin.action(description="Клиент согласился: создать заказ", permissions=["convert"])
    def create_orders(self, request, queryset):
        from apps.orders.services import convert_lead
        for pk in queryset.values_list("pk", flat=True):
            try:
                order, created = convert_lead(pk)
                self.message_user(request, f"Заказ № {order.pk}: " + ("создан" if created else "уже существует"))
            except ValidationError as error:
                self.message_user(request, "; ".join(error.messages), messages.ERROR)

    def has_convert_permission(self, request):
        return request.user.has_perms(("leads.change_lead", "orders.add_order", "customers.view_client"))


@admin.register(IncomingCall)
class IncomingCallAdmin(admin.ModelAdmin):
    list_display = ("id", "event_id", "phone", "client", "lead", "created_at")
    readonly_fields = ("event_id", "phone", "created_at", "lead")
    search_fields = ("phone", "event_id")

    def has_add_permission(self, request):
        return False

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields + (("client",) if obj and obj.lead_id else ())

    def save_model(self, request, obj, form, change):
        if obj.client_id and not obj.lead_id:
            obj.lead = Lead.objects.create(title="Входящий звонок", client=obj.client, source="Телефон")
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and request.user.has_perms(("leads.add_lead", "customers.view_client"))
