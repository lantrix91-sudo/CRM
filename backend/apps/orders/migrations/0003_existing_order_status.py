from django.db import migrations


def set_assigned(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    Order.objects.filter(status="new", employee__isnull=False).update(status="assigned")


class Migration(migrations.Migration):
    dependencies = [("orders", "0002_order_amount_order_completed_at_order_lead_and_more")]
    operations = [migrations.RunPython(set_assigned, migrations.RunPython.noop)]
