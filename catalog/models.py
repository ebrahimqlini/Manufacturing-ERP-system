from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


class UnitOfMeasure(models.Model):
    code = models.CharField("الرمز", max_length=24, unique=True)
    name_ar = models.CharField("اسم الوحدة بالعربية", max_length=80)
    name_en = models.CharField("اسم الوحدة بالإنجليزية", max_length=80, blank=True)
    dimension = models.CharField("البعد", max_length=40, help_text="مفتاح بعد قابل للضبط؛ لا تُخلط الأبعاد المختلفة.")
    active = models.BooleanField("نشط", default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(condition=~models.Q(code=""), name="uom_code_not_empty"),
            models.CheckConstraint(condition=~models.Q(name_ar=""), name="uom_ar_name_not_empty"),
            models.CheckConstraint(condition=~models.Q(dimension=""), name="uom_dimension_not_empty"),
        ]

    def clean(self):
        self.code = self.code.strip().upper()
        self.dimension = self.dimension.strip().lower()
        if not self.name_ar.strip():
            raise ValidationError({"name_ar": "اسم الوحدة بالعربية مطلوب."})

    def __str__(self):
        return f"{self.code} — {self.name_ar}"


class ProductCategory(models.Model):
    code = models.SlugField("الرمز", max_length=40, unique=True)
    name_ar = models.CharField("اسم الفئة بالعربية", max_length=100)
    name_en = models.CharField("اسم الفئة بالإنجليزية", max_length=100, blank=True)
    description = models.TextField("الوصف", blank=True)
    active = models.BooleanField("نشطة", default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "فئة صنف"
        verbose_name_plural = "فئات الأصناف"
        constraints = [models.CheckConstraint(condition=~models.Q(name_ar=""), name="category_ar_name_not_empty")]

    def clean(self):
        if not self.name_ar.strip():
            raise ValidationError({"name_ar": "اسم الفئة بالعربية مطلوب."})

    def __str__(self):
        return f"{self.name_ar} ({self.code})"


class Product(models.Model):
    sku = models.CharField("كود الصنف", max_length=64, unique=True)
    name_ar = models.CharField("اسم الصنف بالعربية", max_length=180)
    name_en = models.CharField("اسم الصنف بالإنجليزية", max_length=180, blank=True)
    description = models.TextField("الوصف", blank=True)
    category = models.ForeignKey(ProductCategory, on_delete=models.PROTECT, related_name="products", verbose_name="الفئة")
    base_uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="base_products", verbose_name="وحدة المخزون الأساسية")
    active = models.BooleanField("نشط", default=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    class Meta:
        ordering = ["sku"]
        constraints = [
            models.CheckConstraint(condition=~models.Q(sku=""), name="product_sku_not_empty"),
            models.CheckConstraint(condition=~models.Q(name_ar=""), name="product_ar_name_not_empty"),
        ]

    def clean(self):
        self.sku = self.sku.strip()
        if not self.name_ar.strip():
            raise ValidationError({"name_ar": "اسم الصنف بالعربية مطلوب."})
        if self.pk:
            previous = Product.objects.filter(pk=self.pk).first()
            if previous and self.stock_movements.exists() and (previous.sku != self.sku or previous.base_uom_id != self.base_uom_id):
                raise ValidationError("لا يمكن تغيير كود الصنف أو وحدته الأساسية بعد تسجيل حركة؛ عطّل الصنف وأنشئ صنفًا جديدًا.")
        if self.category_id and not self.category.active and not self.pk:
            raise ValidationError({"category": "لا يمكن إنشاء صنف بفئة غير نشطة."})
        if self.base_uom_id and not self.base_uom.active and not self.pk:
            raise ValidationError({"base_uom": "لا يمكن إنشاء صنف بوحدة غير نشطة."})

    def __str__(self):
        return f"{self.sku} — {self.name_ar}"


class ProductUnitConversion(models.Model):
    """Product-specific factor: one alternate unit equals factor base units."""
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="unit_conversions", verbose_name="الصنف")
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="product_conversions", verbose_name="الوحدة البديلة")
    factor = models.DecimalField("عدد الوحدات الأساسية لكل وحدة بديلة", max_digits=30, decimal_places=12, validators=[MinValueValidator(0)])
    active = models.BooleanField("نشط", default=True)

    class Meta:
        ordering = ["product__sku", "unit__code"]
        constraints = [
            models.UniqueConstraint(fields=["product", "unit"], name="unique_product_alt_uom"),
            models.CheckConstraint(condition=models.Q(factor__gt=0), name="conversion_factor_positive"),
        ]
        verbose_name = "تحويل وحدة صنف"
        verbose_name_plural = "تحويلات وحدات الأصناف"

    def clean(self):
        if self.product_id and self.unit_id:
            if self.unit_id == self.product.base_uom_id:
                raise ValidationError({"unit": "الوحدة الأساسية لا تحتاج إلى تعريف تحويل بديل."})
            if self.unit.dimension != self.product.base_uom.dimension:
                raise ValidationError({"unit": "لا يمكن التحويل بين وحدات ذات أبعاد مختلفة."})
        if self.factor is not None and self.factor <= 0:
            raise ValidationError({"factor": "عامل التحويل يجب أن يكون أكبر من صفر."})

    def __str__(self):
        return f"{self.product.sku}: {self.unit.code} × {self.factor}"
