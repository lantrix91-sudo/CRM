from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from django.db import models


class User(AbstractUser):
    """Employee account; keep the existing authentication model and table."""

    class Role(models.TextChoices):
        MANAGER = "manager", "Руководитель"
        OPERATOR = "operator", "Оператор"
        WORKER = "worker", "Мастер"

    role = models.CharField("роль", max_length=12, choices=Role.choices, blank=True, default="")
    telegram_chat_id = models.BigIntegerField("Telegram ID (личный чат)", null=True, blank=True, unique=True, validators=[MinValueValidator(1)])
    telegram_link_token = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    telegram_link_expires = models.DateTimeField(null=True, blank=True, editable=False)
    services = models.ManyToManyField("services.Service", verbose_name="умеет выполнять услуги", blank=True, related_name="employees")
    is_available = models.BooleanField("принимает новые лиды", default=False)
    max_active_leads = models.PositiveIntegerField("лимит активных лидов", default=5, validators=[MinValueValidator(1)])

    class Meta:
        verbose_name = "сотрудник"
        verbose_name_plural = "сотрудники"
