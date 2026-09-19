from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


class User(AbstractUser):
    """Employee account; keep the existing authentication model and table."""

    class Role(models.TextChoices):
        MANAGER = "manager", "Руководитель"
        OPERATOR = "operator", "Оператор"
        CURATOR = "curator", "Куратор"
        WORKER = "worker", "Мастер"

    operator = models.ForeignKey("self", verbose_name="оператор мастера", null=True, blank=True, on_delete=models.SET_NULL, related_name="operator_workers", limit_choices_to={"role": "operator"})
    supervisor = models.ForeignKey("self", verbose_name="руководитель мастера", null=True, blank=True, on_delete=models.SET_NULL, related_name="managed_workers", limit_choices_to={"role": "manager"})
    curator = models.ForeignKey("self", verbose_name="куратор мастера", null=True, blank=True, on_delete=models.SET_NULL, related_name="supervised_workers", limit_choices_to={"role": "curator"})
    role = models.CharField("роль", max_length=12, choices=Role.choices, blank=True, default="")
    telegram_chat_id = models.BigIntegerField("Telegram ID (личный чат)", null=True, blank=True, unique=True, validators=[MinValueValidator(1)])
    telegram_link_token = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    telegram_link_expires = models.DateTimeField(null=True, blank=True, editable=False)
    services = models.ManyToManyField("services.Service", verbose_name="умеет выполнять услуги", blank=True, related_name="employees")
    service_cities = models.ManyToManyField("customers.City", verbose_name="города работы", blank=True, related_name="workers")
    is_available = models.BooleanField("принимает новые лиды", default=False)
    max_active_leads = models.PositiveIntegerField("лимит активных лидов", default=5, validators=[MinValueValidator(1)])

    class Meta:
        verbose_name = "сотрудник"
        verbose_name_plural = "сотрудники"


class MobileSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="mobile_sessions")
    token_hash = models.CharField(max_length=64, unique=True)
    password_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)


class WorkerServiceRate(models.Model):
    worker = models.ForeignKey(User, on_delete=models.CASCADE, related_name="service_rates")
    service = models.ForeignKey("services.Service", on_delete=models.CASCADE, related_name="worker_rates")
    worker_percentage = models.DecimalField(
        "процент мастера", max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    active = models.BooleanField("активна", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("worker", "service"), name="unique_worker_service_rate"),
        ]
        verbose_name = "ставка мастера по услуге"
        verbose_name_plural = "ставки мастеров по услугам"

    def clean(self):
        super().clean()
        if self.worker_id and self.worker.role != User.Role.WORKER:
            raise ValidationError({"worker": "Ставка доступна только для мастера."})

    @property
    def company_percentage(self):
        return 100 - self.worker_percentage
