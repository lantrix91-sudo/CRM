from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0009_settlementshift_order_settlement_shift_and_more")]

    operations = [
        migrations.AlterField(
            model_name="orderevent", name="description", field=models.TextField(),
        ),
    ]
