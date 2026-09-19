"""Appointment reminders run in the existing Telegram polling worker."""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from .models import Lead, LeadEvent
from apps.orders.models import Order


def deliver_appointment_reminders(api):
    from .telegram import TelegramError, notification_text
    now = timezone.now()
    due = Lead.objects.filter(scheduled_at__gte=now,
        scheduled_at__lte=now + timedelta(minutes=settings.APPOINTMENT_REMINDER_MINUTES))
    # Resolve the current assignee from the order after conversion.
    due = due.filter(
        Q(order__isnull=True, status__in=("new", "assigned", "in_progress"),
          employee__role="worker", employee__is_active=True, employee__telegram_chat_id__isnull=False)
        | Q(order__status__in=("assigned", "in_progress"), order__employee__role="worker",
            order__employee__is_active=True, order__employee__telegram_chat_id__isnull=False))
    due = due.filter(Q(appointment_reminded_at__isnull=True)
        | (Q(order__isnull=True) & ~Q(appointment_reminded_for_id=F("employee_id")))
        | (Q(order__isnull=False) & ~Q(appointment_reminded_for_id=F("order__employee_id"))))
    for pk in due.order_by("scheduled_at").values_list("pk", flat=True)[:20]:
        with transaction.atomic():
            lead = Lead.objects.select_for_update().get(pk=pk)
            order = Order.objects.select_for_update().filter(lead=lead).first()
            target = order or lead
            if target.status not in ("new", "assigned", "in_progress") or not target.employee_id:
                continue
            worker = target.employee
            if not worker.is_active or worker.role != "worker" or not worker.telegram_chat_id:
                continue
            if not lead.scheduled_at or not (timezone.now() <= lead.scheduled_at <= timezone.now() + timedelta(minutes=settings.APPOINTMENT_REMINDER_MINUTES)):
                continue
            if lead.appointment_reminded_at and lead.appointment_reminded_for_id == worker.pk:
                continue
            try:
                api.call("sendMessage", chat_id=worker.telegram_chat_id,
                    text="⏰ Напоминание о записи\n" + notification_text(target, accepted=target.status == "in_progress"))
            except TelegramError:
                continue  # Retry in the next poll; do not record a successful delivery.
            lead.appointment_reminded_at = timezone.now()
            lead.appointment_reminded_for = worker
            lead.save(update_fields=("appointment_reminded_at", "appointment_reminded_for"))
            description = f"Напоминание о записи {timezone.localtime(lead.scheduled_at):%d.%m.%Y %H:%M} отправлено мастеру {worker.get_full_name() or worker.username}"
            LeadEvent.objects.create(lead=lead, actor_name="Система", description=description)
            if order:
                from apps.orders.services import record_event
                record_event(order, None, description)
