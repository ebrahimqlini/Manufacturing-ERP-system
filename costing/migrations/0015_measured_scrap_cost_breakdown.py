from django.db import migrations, models
from decimal import Decimal


class Migration(migrations.Migration):
    dependencies = [("costing", "0014_clarify_scrap_allocation_estimate")]

    operations = [
        migrations.AlterField(
            model_name="monthlyprofitperiod", name="absorbed_scrap_cost",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), help_text="تكلفة أحداث الهالك المادي المقاس والمعترف بها ضمن تكلفة الإنتاج تحت سياسة التحميل؛ لا تُخصم مرة أخرى لأنها ضمن تكلفة الوحدة ثم COGS عند البيع.", max_digits=18, verbose_name="تكلفة الهالك المحمّلة ضمن تكلفة الإنتاج (ج.م، عرض فقط)"),
        ),
        migrations.AlterField(
            model_name="productcostingentry", name="waste_material_cost",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), help_text="مجموع تكلفة هالك المواد المقاس والمُقيّم وقت الترحيل. القيمة غير المتاحة تبقى فارغة في سجل الحدث وتمنع اكتمال الفترة.", max_digits=18, verbose_name="تكلفة هالك المواد المقاس (ج.م)"),
        ),
        migrations.AlterField(
            model_name="productcostingentry", name="scrap_breakdown",
            field=models.JSONField(default=list, help_text="تفاصيل أحداث قياس الهالك المادي والقيم المثبتة وقت الترحيل.", verbose_name="تفاصيل الهالك لكل مادة"),
        ),
        migrations.AlterField(
            model_name="productionordercostingentry", name="waste_material_cost",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), help_text="مجموع تكاليف أحداث الهالك المادي المسجلة لهذا الأمر وفق سعر الوحدة وقت الترحيل.", max_digits=18, verbose_name="تكلفة الهالك المادي المقاس (ج.م)"),
        ),
        migrations.AlterField(
            model_name="productionordercostingentry", name="scrap_breakdown",
            field=models.JSONField(default=list, help_text="لقطات أحداث الهالك المادي المرتبطة بأوامر هذا المنتج خلال الفترة.", verbose_name="تفاصيل الهالك لكل مادة"),
        ),
    ]
