from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User, WorkerServiceRate


@admin.register(User)
class EmployeeAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("Распределение лидов", {"fields": ("role", "services", "is_available", "max_active_leads", "telegram_chat_id")}),
    )
    filter_horizontal = UserAdmin.filter_horizontal + ("services",)
    list_display = UserAdmin.list_display + ("is_available", "max_active_leads")


@admin.register(WorkerServiceRate)
class WorkerServiceRateAdmin(admin.ModelAdmin):
    list_display = ("worker", "service", "worker_percentage", "active", "updated_at")
    list_filter = ("active", "service")
