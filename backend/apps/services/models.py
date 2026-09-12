from django.db import models


class Service(models.Model):
    name = models.CharField("название", max_length=200, unique=True)
    description = models.TextField("описание", blank=True)
    created_at = models.DateTimeField("дата создания", auto_now_add=True)

    class Meta:
        verbose_name = "услуга"
        verbose_name_plural = "услуги"

    def __str__(self):
        return self.name
