from django.db import migrations, models


def backfill_utility_recorded(apps, schema_editor):
    CostingPeriod = apps.get_model("costing", "CostingPeriod")
    MonthlyExpense = apps.get_model("costing", "MonthlyExpense")
    for expense_type, field in (
        ("ELECTRICITY", "electricity_recorded"),
        ("WATER", "water_recorded"),
    ):
        recorded_months = MonthlyExpense.objects.filter(
            expense_type=expense_type
        ).values_list("year", "month").distinct()
        for year, month in recorded_months:
            CostingPeriod.objects.filter(year=year, month=month).update(**{field: True})


class Migration(migrations.Migration):

    dependencies = [
        ("costing", "0006_costingperiod_labor_recorded"),
    ]

    operations = [
        migrations.AddField(
            model_name="costingperiod",
            name="electricity_recorded",
            field=models.BooleanField(
                default=False,
                editable=False,
                help_text="لقطة وقت الاحتساب تفرّق بين غياب بند الكهرباء وتسجيله صراحة بقيمة 0.00.",
                verbose_name="تم تسجيل مصاريف الكهرباء",
            ),
        ),
        migrations.AddField(
            model_name="costingperiod",
            name="water_recorded",
            field=models.BooleanField(
                default=False,
                editable=False,
                help_text="لقطة وقت الاحتساب تفرّق بين غياب بند المياه وتسجيله صراحة بقيمة 0.00.",
                verbose_name="تم تسجيل مصاريف المياه",
            ),
        ),
        migrations.RunPython(backfill_utility_recorded, migrations.RunPython.noop),
    ]
