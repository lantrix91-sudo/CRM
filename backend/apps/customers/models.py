from django.db import models


class Client(models.Model):
    name = models.CharField("имя", max_length=200)
    phone = models.CharField("номер WhatsApp", max_length=32, db_index=True)
    normalized_phone = models.CharField(max_length=16, blank=True, db_index=True, editable=False)
    address = models.CharField("адрес", max_length=300, blank=True)
    district = models.CharField("район", max_length=200, blank=True)
    created_at = models.DateTimeField("дата создания", auto_now_add=True)

    class Meta:
        verbose_name = "клиент"
        verbose_name_plural = "клиенты"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if kwargs.get("update_fields") is not None and "phone" not in kwargs["update_fields"]:
            return super().save(*args, **kwargs)
        from .phones import normalize_phone
        try:
            self.normalized_phone = normalize_phone(self.phone)
        except ValueError:
            self.normalized_phone = ""
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"normalized_phone"}
        super().save(*args, **kwargs)
