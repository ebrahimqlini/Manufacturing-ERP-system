from django.db import migrations


def add_categories(apps, schema_editor):
    Category = apps.get_model("catalog", "ProductCategory")
    for code, ar, en in [
        ("raw-material", "مواد خام", "Raw materials"),
        ("work-in-progress", "تحت التشغيل", "Work in progress"),
        ("finished-goods", "منتجات تامة", "Finished goods"),
        ("packaging-material", "مواد تعبئة وتغليف", "Packaging materials"),
        ("scrap-waste", "هالك ومخلفات", "Scrap and waste"),
        ("consumables", "مستهلكات", "Consumables"),
    ]:
        Category.objects.get_or_create(code=code, defaults={"name_ar": ar, "name_en": en, "active": True})


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]
    operations = [migrations.RunPython(add_categories, migrations.RunPython.noop)]
