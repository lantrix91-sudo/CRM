import uuid
from django.utils import timezone

from django.conf import settings
from django.db import models


class Lead(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новый"
        IN_PROGRESS = "in_progress", "В работе"
        ASSIGNED = "assigned", "Назначен"
        WON = "won", "Выполнен"
        CONVERTED = "converted", "Создан заказ"
        LOST = "lost", "Закрыт без сделки"

    source = models.CharField("источник", max_length=100, blank=True)
    status = models.CharField(
        "статус", max_length=20, choices=Status.choices,
        default=Status.NEW, db_index=True,
    )
    title = models.CharField("название", max_length=200)
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

    def __str__(self):
        return self.title


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
