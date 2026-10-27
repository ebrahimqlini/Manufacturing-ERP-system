from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("manufacturing", "0010_materialissue_cost_source_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="productionresult",
            name="posted_at",
            field=models.DateTimeField(
                auto_now_add=True,
                blank=True,
                editable=False,
                null=True,
                verbose_name="وقت تسجيل النظام",
            ),
        ),
    ]
