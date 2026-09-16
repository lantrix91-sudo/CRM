from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator


class Order(models.Model):
    """A client's confirmed request for one service."""

    class Status(models.TextChoices):
        NEW = "new", "Новый"
        ASSIGNED = "assigned", "Назначен мастер"
        IN_PROGRESS = "in_progress", "В работе"
        COMPLETED = "completed", "Выполнен, ожидает оплаты"
        PAID = "paid", "Оплачен"
        CANCELLED = "cancelled", "Отменён клиентом"

    settlement_shift = models.ForeignKey("SettlementShift", null=True, blank=True, on_delete=models.PROTECT, related_name="orders")
    repeat_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="repeat_repairs", verbose_name="повторный ремонт заказа")
    lead = models.OneToOneField("leads.Lead", on_delete=models.PROTECT, null=True, blank=True, related_name="order", verbose_name="исходный лид")
    status = models.CharField("этап", max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)
    amount = models.DecimalField("стоимость (KZT)", max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    is_free = models.BooleanField("завершён бесплатно", default=False)
    expenses = models.DecimalField("расходы (KZT)", max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    work_comment = models.TextField("что сделано", blank=True, max_length=2000)
    worker_percentage = models.DecimalField("процент мастера при оплате", max_digits=5, decimal_places=2, null=True, blank=True)
    completed_at = models.DateTimeField("выполнен", null=True, blank=True)
    cancellation_reason = models.CharField("причина отмены", max_length=300, blank=True)
    cancelled_at = models.DateTimeField("дата отмены", null=True, blank=True)
    paid_at = models.DateTimeField("оплачен", null=True, blank=True)

    received_amount = models.DecimalField("получено мастером (KZT)", max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0.01)])
    received_method = models.CharField("способ оплаты", max_length=20, blank=True, choices=(("cash", "Наличные"), ("transfer", "Перевод"), ("card", "Карта")))
    received_at = models.DateTimeField("мастер сообщил об оплате", null=True, blank=True)

    appliance_type = models.CharField("тип техники", max_length=100, blank=True)
    brand = models.CharField("бренд", max_length=100, blank=True)
    comment = models.TextField("комментарий", max_length=1000, blank=True)
    title = models.CharField("название", max_length=200)
    client = models.ForeignKey(
        "customers.Client", verbose_name="клиент",
        on_delete=models.PROTECT, related_name="orders",
    )
    service = models.ForeignKey(
        "services.Service", verbose_name="услуга",
        on_delete=models.PROTECT, related_name="orders",
    )
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="ответственный сотрудник",
        on_delete=models.SET_NULL, null=True, blank=True, related_name="orders",
    )
    created_at = models.DateTimeField("дата создания", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "заказ"
        verbose_name_plural = "заказы"

    def __str__(self):
        return self.title

    @property
    def completed_free(self):
        return self.status == self.Status.COMPLETED and (self.is_free or bool(self.repeat_of_id))

    @property
    def display_status(self):
        return "Выполнен · бесплатно" if self.completed_free else self.get_status_display()

    def clean(self):
        super().clean()
        if self.employee_id and (not self.employee.is_active or self.employee.role != "worker"):
            raise ValidationError({"employee": "Выберите активного сотрудника."})
        if self.status == self.Status.IN_PROGRESS and not self.employee_id:
            raise ValidationError({"employee": "У заказа в работе должен быть мастер."})
        if self.pk:
            old = type(self).objects.filter(pk=self.pk).first()
            if old and old.status == self.Status.IN_PROGRESS and old.employee_id != self.employee_id:
                raise ValidationError({"employee": "Нельзя менять мастера после начала работы."})


class OrderEvent(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    actor_name = models.CharField(max_length=200, blank=True)
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "pk")


class SettlementShift(models.Model):
    worker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="closed_shifts")
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="shifts_closed")
    closed_at = models.DateTimeField(auto_now_add=True)
    manager_amount = models.DecimalField(max_digits=14, decimal_places=2)
    worker_amount = models.DecimalField(max_digits=14, decimal_places=2)
    balance = models.DecimalField(max_digits=14, decimal_places=2)


class WorkerTransfer(models.Model):
    worker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="settlement_transfers")
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="received_transfers")
    amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(0.01)])
    comment = models.CharField(max_length=300, blank=True)
    request_id = models.UUIDField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
