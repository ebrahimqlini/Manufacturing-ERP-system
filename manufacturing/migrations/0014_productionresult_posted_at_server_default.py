import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("manufacturing", "0013_productionresultreversal")]

    operations = [
        migrations.AlterField(
            model_name="productionresult",
            name="posted_at",
            field=models.DateTimeField(blank=True, default=django.utils.timezone.now, editable=False, null=True, verbose_name="وقت تسجيل النظام"),
        ),
    ]
