from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import Product, ProductCategory, ProductUnitConversion, UnitOfMeasure


class CatalogTests(TestCase):
    def setUp(self):
        self.unit = UnitOfMeasure.objects.create(code="PCS", name_ar="قطعة", dimension="count")
        self.category, _ = ProductCategory.objects.get_or_create(code="raw-material", defaults={"name_ar": "مواد خام"})
        for code, name in (("work-in-progress", "تحت التشغيل"), ("finished-goods", "منتجات تامة"), ("packaging-material", "مواد تعبئة وتغليف"), ("scrap-waste", "هالك ومخلفات")):
            ProductCategory.objects.get_or_create(code=code, defaults={"name_ar": name})

    def test_shared_catalog_holds_cup_and_fiber_family_products(self):
        cups = Product.objects.create(sku="CUP-RAW", name_ar="ورق أكواب", category=self.category, base_uom=self.unit)
        trays = Product.objects.create(sku="TRAY-RAW", name_ar="مادة خط الحاويات", category=self.category, base_uom=self.unit)
        self.assertEqual(list(Product.objects.order_by("sku")), [cups, trays])
        self.assertEqual(Product.objects.filter(category=self.category).count(), 2)

    def test_conversion_is_product_specific_and_exact(self):
        alternate = UnitOfMeasure.objects.create(code="PACK", name_ar="عبوة", dimension="count")
        product = Product.objects.create(sku="CUP-RAW", name_ar="ورق أكواب", category=self.category, base_uom=self.unit)
        conversion = ProductUnitConversion.objects.create(product=product, unit=alternate, factor=Decimal("2.500000000000"))
        self.assertEqual(conversion.factor * Decimal("0.25"), Decimal("0.62500000000000"))

    def test_incompatible_conversion_is_rejected(self):
        product = Product.objects.create(sku="CUP-RAW", name_ar="ورق أكواب", category=self.category, base_uom=self.unit)
        mass = UnitOfMeasure.objects.create(code="KG", name_ar="كيلوجرام", dimension="mass")
        conversion = ProductUnitConversion(product=product, unit=mass, factor=Decimal("1"))
        with self.assertRaises(ValidationError):
            conversion.full_clean()

    def test_required_stock_categories_are_seeded(self):
        codes = set(ProductCategory.objects.values_list("code", flat=True))
        self.assertTrue({"raw-material", "work-in-progress", "finished-goods", "packaging-material", "scrap-waste"}.issubset(codes))
