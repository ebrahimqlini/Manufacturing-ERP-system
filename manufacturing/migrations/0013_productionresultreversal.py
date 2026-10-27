from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("manufacturing", "0012_productionmaterialwaste_and_review"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductionResultReversal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reversed_at", models.DateTimeField(auto_now_add=True)),
                ("reason", models.CharField(max_length=255)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="production_result_reversals", to=settings.AUTH_USER_MODEL)),
                ("result", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="correction", to="manufacturing.productionresult")),
            ],
            options={"permissions": [("correct_production_result", "Can correct or reverse production results")]},
        ),
    ]
