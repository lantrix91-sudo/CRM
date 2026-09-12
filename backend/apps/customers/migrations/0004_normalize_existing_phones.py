import re
from django.db import migrations


def backfill(apps, schema_editor):
    Client = apps.get_model("customers", "Client")
    for client in Client.objects.all().iterator():
        raw = client.phone.strip()
        digits = re.sub(r"[^0-9]", "", raw)
        if len(digits) == 11 and digits.startswith("8"):
            digits = "7" + digits[1:]
        value = "+" + digits if re.fullmatch(r"[+0-9() .-]+", raw) and 10 <= len(digits) <= 15 else ""
        Client.objects.filter(pk=client.pk).update(normalized_phone=value)


class Migration(migrations.Migration):
    dependencies = [("customers", "0003_client_normalized_phone")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
