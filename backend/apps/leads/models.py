import uuid
from django.utils import timezone

from django.conf import settings
from django.db import models, transaction


class Lead(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новый"
        IN_PROGRESS = "in_progress", "В работе"
        ASSIGNED = "assigned", "Назначен"
        WON = "won", "Выполнен"
        CONVERTED = "converted", "Создан заказ"
        LOST = "lost", "Закрыт без сделки"

    scheduled_at = models.DateTimeField("дата и время записи", null=True, blank=True, db_index=True)
    appointment_reminded_at = models.DateTimeField(null=True, blank=True, editable=False)
    appointment_reminded_for = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        editable=False, on_delete=models.SET_NULL, related_name="appointment_reminders")

    lost_reason = models.CharField("причина отказа", max_length=300, blank=True)
    lost_at = models.DateTimeField("закрыта без сделки", null=True, blank=True)
    lost_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="closed_leads")
    source = models.CharField("источник", max_length=100, blank=True)
    status = models.CharField(
        "статус", max_length=20, choices=Status.choices,
        default=Status.NEW, db_index=True,
    )
    appliance_type = models.CharField("тип техники", max_length=100, blank=True)
    brand = models.CharField("бренд", max_length=100, blank=True)
    comment = models.TextField("комментарий", max_length=1000, blank=True)
    title = models.CharField("название", max_length=200)
    city = models.ForeignKey("customers.City", verbose_name="город", on_delete=models.PROTECT, related_name="leads", null=True, blank=True)
    client = models.ForeignKey(
        "customers.Client", verbose_name="клиент",
        on_delete=models.PROTECT, related_name="leads",
    )
    service = models.ForeignKey(
        "services.Service", verbose_name="услуга", null=True, blank=True,
        on_delete=models.PROTECT, related_name="leads",
    )
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="ответственный сотрудник",
        on_delete=models.SET_NULL, null=True, blank=True, related_name="leads",
    )
    created_at = models.DateTimeField("дата создания", auto_now_add=True, db_index=True)

    class Meta:
        permissions = [("view_analytics", "Просмотр аналитики источников")]
        verbose_name = "лид"
        verbose_name_plural = "лиды"

    @transaction.atomic
    def save(self, *args, **kwargs):
        fields = kwargs.get("update_fields")
        track = fields is None or "scheduled_at" in fields
        previous = type(self).objects.select_for_update().filter(pk=self.pk).values_list("scheduled_at", flat=True).first() if self.pk and track else None
        changed = track and previous != self.scheduled_at
        if changed:
            self.appointment_reminded_at = None
            self.appointment_reminded_for_id = None
            if fields is not None:
                kwargs["update_fields"] = set(fields) | {"appointment_reminded_at", "appointment_reminded_for"}
        super().save(*args, **kwargs)
        if changed:
            def label(value):
                return timezone.localtime(value).strftime("%d.%m.%Y %H:%M") if value else "не указана"
            actor = getattr(self, "_history_actor", None)
            LeadEvent.objects.create(lead=self, actor=actor,
                actor_name=(actor.get_full_name() or actor.username) if actor else "Система",
                description=f"Запись: {label(previous)} → {label(self.scheduled_at)}")

    def __str__(self):
        return self.title


class LeadEvent(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    actor_name = models.CharField(max_length=301)
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")


class TelegramNotice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="telegram_notices", null=True, blank=True)
    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="telegram_notices", null=True, blank=True)
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)
    state = models.CharField(max_length=12, default="pending", choices=[("pending", "Ожидает"), ("sent", "Отправлено"), ("cancelled", "Отменено")])
    chat_id = models.BigIntegerField(null=True, blank=True)
    message_id = models.BigIntegerField(null=True, blank=True)
    reminder_at = models.DateTimeField(null=True, blank=True, db_index=True)
    draft_comment = models.TextField(blank=True, default="")
    payment_step = models.CharField(max_length=12, blank=True, default="")
    draft_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    draft_expenses = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    amount_prompt_id = models.BigIntegerField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=(models.Q(lead__isnull=False, order__isnull=True) | models.Q(lead__isnull=True, order__isnull=False)),
            name="telegram_notice_one_target",
        )]


class TelegramBotState(models.Model):
    offset = models.BigIntegerField(default=0)


class IncomingCall(models.Model):
    event_id = models.CharField("ID звонка АТС", max_length=128, unique=True)
    phone = models.CharField("телефон", max_length=16)
    client = models.ForeignKey("customers.Client", null=True, blank=True, on_delete=models.PROTECT, related_name="calls")
    lead = models.OneToOneField(Lead, null=True, blank=True, on_delete=models.PROTECT, related_name="incoming_call")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "входящий звонок"
        verbose_name_plural = "входящие звонки"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.client_id and self.client.normalized_phone != self.phone:
            raise ValidationError({"client": "Выберите клиента с номером этого звонка."})
