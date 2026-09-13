from django.contrib.auth.models import AbstractUser
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
    percentage = models.DecimalField("процент сотрудника", max_digits=5, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(100)])
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
