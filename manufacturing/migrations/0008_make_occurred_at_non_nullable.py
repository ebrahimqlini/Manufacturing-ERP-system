from django.db import migrations, models


def ensure_occurred_at_populated(apps, schema_editor):
    ProductionResult = apps.get_model("manufacturing", "ProductionResult")
    # Set NULL occurred_at to the created_at of the parent order as a safe
    # fallback. This migration exists only to make the field non-nullable;
    # new records always receive an explicit timestamp from the service layer.
    for result in ProductionResult.objects.filter(occurred_at__isnull=True):
        result.occurred_at = result.order.created_at
        result.save(update_fields=["occurred_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("manufacturing", "0007_alter_productionresult_occurred_at"),
    ]

    operations = [
        migrations.RunPython(ensure_occurred_at_populated, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="productionresult",
            name="occurred_at",
            field=models.DateTimeField("تاريخ الإنتاج"),
        ),
    ]