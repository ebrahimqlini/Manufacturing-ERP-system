from django.db import migrations


def seed_reference_configuration(apps, schema_editor):
    Line = apps.get_model("manufacturing", "ProductionLine")
    Operation = apps.get_model("manufacturing", "ProductionOperation")

    Line.objects.get_or_create(
        code="paper-cups",
        defaults={"name_ar": "خط الأكواب الورقية", "name_en": "Paper cups", "active": True},
    )
    Line.objects.get_or_create(
        code="fiber-containers",
        defaults={"name_ar": "خط حاويات وصواني الألياف الورقية", "name_en": "Fiber-paper containers and trays", "active": True},
    )

    operations = (
        ("cup-printing", "طباعة", "Printing"),
        ("cup-cutting", "تقطيع", "Cutting"),
        ("cup-forming", "تشكيل الأكواب", "Cup forming"),
        ("cup-bottom-assembly", "تركيب القاع", "Bottom assembly"),
        ("cup-packaging", "تعبئة الأكواب", "Packaging"),
        ("fiber-pressing-in-mold", "كبس مادة الألياف في قالب", "Pressing fiber material in a mold"),
    )
    for code, name_ar, name_en in operations:
        Operation.objects.get_or_create(
            code=code,
            defaults={"name_ar": name_ar, "name_en": name_en, "active": True},
        )


class Migration(migrations.Migration):
    dependencies = [("manufacturing", "0001_initial")]

    operations = [migrations.RunPython(seed_reference_configuration, migrations.RunPython.noop)]
