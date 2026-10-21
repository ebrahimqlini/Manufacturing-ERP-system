from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("costing", "0008_monthly_profit_and_expense_controls"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="monthlyexpense",
            constraint=models.UniqueConstraint(
                fields=("submission_key",),
                name="unique_monthly_expense_submission",
            ),
        ),
        migrations.AddConstraint(
            model_name="monthlyprofitperiod",
            constraint=models.CheckConstraint(
                condition=models.Q(("year__gte", 2020), ("year__lte", 2100)),
                name="monthly_profit_year_valid",
            ),
        ),
    ]
