from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0003_product_available_to_customers_and_more"),
        ("manufacturing", "0011_productionresult_posted_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductionWasteReview",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reviewed_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="production_waste_reviews", to=settings.AUTH_USER_MODEL)),
                ("result", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="waste_review", to="manufacturing.productionresult")),
            ],
        ),
        migrations.CreateModel(
            name="ProductionMaterialWaste",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("roll_reference", models.CharField(blank=True, max_length=120, verbose_name="مرجع الرول")),
                ("quantity_base", models.DecimalField(decimal_places=12, max_digits=30, verbose_name="الكمية التالفة بالوحدة الأساسية")),
                ("unit_cost_snapshot", models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True, verbose_name="تكلفة الوحدة وقت الترحيل")),
                ("total_cost_snapshot", models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True, verbose_name="تكلفة الهالك وقت الترحيل")),
                ("cost_source", models.CharField(choices=[("LAST_PURCHASE_COST", "آخر سعر شراء وقت الترحيل"), ("STANDARD_PRICE_FALLBACK", "سعر قياسي احتياطي مؤرخ"), ("UNAVAILABLE", "غير متاح وقت الترحيل")], max_length=30)),
                ("posted_at", models.DateTimeField(auto_now_add=True, verbose_name="وقت الترحيل")),
                ("correction_reason", models.CharField(blank=True, max_length=255)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="posted_production_waste", to=settings.AUTH_USER_MODEL)),
                ("material", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="production_waste", to="catalog.product")),
                ("material_issue", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="waste_records", to="manufacturing.materialissue")),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="material_waste", to="manufacturing.productionorder")),
                ("result", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="material_waste", to="manufacturing.productionresult")),
                ("reversal_of", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="reversal", to="manufacturing.productionmaterialwaste")),
            ],
            options={"ordering": ["posted_at", "pk"], "permissions": [("record_production_waste", "Can record measured production material waste")]},
        ),
        migrations.AddConstraint(
            model_name="productionmaterialwaste",
            constraint=models.CheckConstraint(condition=models.Q(("quantity_base__gt", 0)), name="production_material_waste_positive"),
        ),
    ]
