from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("costing", "0013_productcostingentry_scrap_breakdown_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="costingperiod",
            name="material_valuation_method",
            field=models.CharField(
                choices=[("LAST_PURCHASE_COST", "آخر سعر شراء للمادة (Last Purchase Cost)")],
                default="LAST_PURCHASE_COST",
                editable=False,
                help_text=(
                    "الطريقة الوحيدة المدعومة حاليًا: آخر سعر شراء من استلام مشتريات صحيح، "
                    "بحسب وقت الترحيل الناجح للنظام. سعر المالك المؤرخ احتياطي معلن عند غياب LPC."
                ),
                max_length=30,
                verbose_name="طريقة تقييم المواد",
            ),
        ),
        migrations.AlterField(
            model_name="productcostingentry",
            name="waste_material_cost",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                help_text=(
                    "توزيع تقديري لتكلفة الأمر حسب نسبة وحدات الهالك المسجلة، وليس كمية تالفة "
                    "مقاسة لكل مادة أو تقييم LPC وقت الحدث. لا يُعتمد ماليًا دون ربط المادة والكمية التالفة."
                ),
                max_digits=18,
                verbose_name="تكلفة المواد المنفصلة عن الهالك (ج.م)",
            ),
        ),
        migrations.AlterField(
            model_name="productionordercostingentry",
            name="waste_material_cost",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                help_text="تقدير بنسبة وحدات الهالك إلى إجمالي الوحدات؛ لا يمثل كمية تالفة لكل مادة ولا LPC وقت الحدث.",
                max_digits=18,
                verbose_name="تقدير تكلفة الهالك الموزع على مستوى الأمر (ج.م)",
            ),
        ),
    ]
