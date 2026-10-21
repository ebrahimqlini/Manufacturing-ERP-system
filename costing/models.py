from decimal import Decimal
import calendar
import uuid
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from catalog.models import Product, UnitOfMeasure
from manufacturing.models import ProductionLine, ProductionOrder


def finalized_period_for(year, month):
    """Return the finalized CostingPeriod for a calendar month, or None."""
    return CostingPeriod.objects.filter(
        year=year, month=month, status=CostingPeriod.Status.FINALIZED
    ).first()


def ensure_period_open(year, month, action_label):
    """Reject any change to a finalized month; used by every write path."""
    if finalized_period_for(year, month):
        raise ValidationError(
            f"لا يمكن {action_label} لشهر {year}/{month:02d} بعد اعتماد فترة التكاليف نهائيًا؛ أعد فتح الفترة أولاً."
        )


class RawMaterialPrice(models.Model):
    """Raw material price entered by the owner. Converted to base unit price."""
    material = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="raw_material_prices",
        verbose_name="المادة الخام",
    )
    price_per_unit = models.DecimalField(
        "السعر للوحدة (ج.م)",
        max_digits=18,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )
    unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        related_name="material_prices",
        verbose_name="وحدة السعر",
    )
    price_per_base_unit = models.DecimalField(
        "السعر للوحدة الأساسية (ج.م)",
        max_digits=18,
        decimal_places=6,
        editable=False,
    )
    effective_date = models.DateField("تاريخ السريان")
    notes = models.CharField("ملاحظات", max_length=240, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_material_prices",
        verbose_name="أنشئ بواسطة",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-effective_date", "material__sku"]
        constraints = [
            models.CheckConstraint(
                condition=Q(price_per_unit__gt=0),
                name="raw_material_price_positive",
            ),
            models.CheckConstraint(
                condition=Q(price_per_base_unit__gt=0),
                name="raw_material_base_price_positive",
            ),
        ]
        verbose_name = "سعر مادة خام"
        verbose_name_plural = "أسعار المواد الخام"

    def clean(self):
        errors = {}
        if self.material_id and not self.material.active:
            errors["material"] = "اختر مادة خام نشطة."
        if self.price_per_unit is not None and self.price_per_unit <= 0:
            errors["price_per_unit"] = "يجب أن يكون السعر أكبر من صفر."
        if self.material_id and self.unit_id:
            if self.unit.dimension != self.material.base_uom.dimension:
                errors["unit"] = "بعد وحدة السعر لا يتوافق مع بعد وحدة المخزون الأساسية للصنف."
            else:
                from inventory.services import to_base_quantity
                try:
                    base_factor = to_base_quantity(self.material, Decimal("1.0"), self.unit)
                    if base_factor > 0:
                        self.price_per_base_unit = (self.price_per_unit / base_factor).quantize(Decimal("0.000001"))
                except ValidationError as exc:
                    errors["unit"] = exc.messages
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.material.sku}: {self.price_per_unit} ج.م / {self.unit.code} ({self.effective_date})"


class RollYieldStandard(models.Model):
    """Standard expected yield of cartons per roll of raw material."""
    raw_material = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="roll_yield_standards",
        verbose_name="المادة الخام (الرول)",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="product_yield_standards",
        verbose_name="المنتج التام",
    )
    production_line = models.ForeignKey(
        ProductionLine,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="yield_standards",
        verbose_name="خط الإنتاج (اختياري)",
    )
    expected_cartons_per_roll = models.DecimalField(
        "عدد الكراتين المتوقعة لكل رول",
        max_digits=12,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )
    roll_unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        related_name="yield_roll_units",
        verbose_name="وحدة الرول",
    )
    carton_unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        related_name="yield_carton_units",
        verbose_name="وحدة الكرتونة/المنتج",
    )
    active = models.BooleanField("نشط", default=True)
    notes = models.CharField("ملاحظات", max_length=240, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_yield_standards",
        verbose_name="أنشئ بواسطة",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["product__sku", "raw_material__sku"]
        constraints = [
            models.UniqueConstraint(
                fields=["raw_material", "product", "production_line"],
                name="unique_roll_yield_standard",
            ),
            models.CheckConstraint(
                condition=Q(expected_cartons_per_roll__gt=0),
                name="expected_cartons_per_roll_positive",
            ),
        ]
        verbose_name = "معيار إنتاجية الرول"
        verbose_name_plural = "معايير إنتاجية الرولات"

    def clean(self):
        errors = {}
        if self.raw_material_id and not self.raw_material.active:
            errors["raw_material"] = "اختر مادة خام نشطة."
        if self.product_id and not self.product.active:
            errors["product"] = "اختر منتجًا تامًا نشطًا."
        if self.expected_cartons_per_roll is not None and self.expected_cartons_per_roll <= 0:
            errors["expected_cartons_per_roll"] = "عدد الكراتين المتوقعة يجب أن يكون أكبر من صفر."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        line_str = f" [{self.production_line.name_ar}]" if self.production_line else ""
        return f"{self.product.sku} من {self.raw_material.sku}: {self.expected_cartons_per_roll} كرتونة/رول{line_str}"


class MonthlyExpenseQuerySet(models.QuerySet):
    """Guard bulk ORM writes so a finalized month cannot be changed in bulk.

    Instance ``save()``/``delete()`` already call ``ensure_period_open``; this
    closes the ``QuerySet.update()``/``QuerySet.delete()`` and admin bulk-delete
    paths that bypass model methods.
    """

    def _assert_periods_open(self, action_label):
        for year, month in self.values_list("year", "month").distinct():
            ensure_period_open(year, month, action_label)

    def update(self, **kwargs):
        self._assert_periods_open("تعديل أو إضافة مصاريف")
        return super().update(**kwargs)

    def delete(self):
        self._assert_periods_open("حذف مصاريف")
        return super().delete()


class MonthlyExpense(models.Model):
    """Monthly factory expenses (labor, electricity, water, other industrial) with attribution scope."""

    objects = MonthlyExpenseQuerySet.as_manager()

    class ExpenseType(models.TextChoices):
        LABOR = "LABOR", "مصاريف عمالة"
        ELECTRICITY = "ELECTRICITY", "مصاريف كهرباء"
        WATER = "WATER", "مصاريف مياه"
        OTHER_INDUSTRIAL = "OTHER_INDUSTRIAL", "مصاريف صناعية أخرى"

    class AllocationScope(models.TextChoices):
        FACTORY = "FACTORY", "مشترك للمصنع ككل"
        LINE = "LINE", "خاص بخط إنتاج محدد"
        PRODUCT = "PRODUCT", "خاص بمنتج محدد"

    year = models.PositiveSmallIntegerField("السنة")
    month = models.PositiveSmallIntegerField(
        "الشهر",
        validators=[MinValueValidator(1), MaxValueValidator(12)],
        help_text=(
            "شهر تحميل المصروف على التكاليف. لا يفترض النظام أن فاتورة الكهرباء/المياه تغطي نفس "
            "الشهر الميلادي للاستهلاك؛ يُحمَّل المصروف كاملًا على الشهر المختار هنا. أي فرق بين فترة "
            "الفاتورة وفترة الاستهلاك يجب توضيحه في البيان/الملاحظات (قيد معروف في الاحتساب)."
        ),
    )
    expense_type = models.CharField("نوع المصروف", max_length=20, choices=ExpenseType.choices)
    scope = models.CharField(
        "نطاق المصروف",
        max_length=20,
        choices=AllocationScope.choices,
        default=AllocationScope.FACTORY,
    )
    production_line = models.ForeignKey(
        ProductionLine,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="monthly_expenses",
        verbose_name="خط الإنتاج",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="monthly_expenses",
        verbose_name="المنتج",
    )
    amount = models.DecimalField(
        "المبلغ (ج.م)",
        max_digits=18,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text=(
            "سجّل 0.00 فقط عند تأكيد عدم وجود مصروف فعلي في هذا الشهر (قيمة صفر مؤكدة). "
            "لا تترك المصروف بدون تسجيل إن كنت لا تعرف قيمته بعد؛ فالشهر بدون بند كهرباء/مياه يُعد ناقصًا."
        ),
    )
    description = models.CharField("بيان المصروف", max_length=240)
    notes = models.TextField("ملاحظات", blank=True)
    submission_key = models.UUIDField(default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_expenses",
        verbose_name="أنشئ بواسطة",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-year", "-month", "expense_type", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=0),
                name="monthly_expense_amount_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(year__gte=2020) & Q(year__lte=2100),
                name="monthly_expense_year_valid",
            ),
        ]
        verbose_name = "مصروف شهري"
        verbose_name_plural = "المصاريف الشهرية"

    def clean(self):
        errors = {}
        if self.amount is not None and self.amount < 0:
            errors["amount"] = "لا يمكن أن يكون مبلغ المصروف سالبًا؛ سجّل 0.00 عند تأكيد عدم وجود مصروف فعلي."
        if self.scope == self.AllocationScope.LINE and not self.production_line_id:
            errors["production_line"] = "حدد خط الإنتاج عند اختيار نطاق خاص بخط محدد."
        elif self.scope != self.AllocationScope.LINE and self.production_line_id:
            self.production_line = None
        if self.scope == self.AllocationScope.PRODUCT and not self.product_id:
            errors["product"] = "حدد المنتج عند اختيار نطاق خاص بمنتج محدد."
        elif self.scope != self.AllocationScope.PRODUCT and self.product_id:
            self.product = None
        if self.year is not None and self.month is not None and 1 <= self.month <= 12:
            # Applies to new and existing rows alike so a finalized month cannot
            # be changed indirectly through creation, editing, or the admin.
            ensure_period_open(self.year, self.month, "تعديل أو إضافة مصاريف")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        ensure_period_open(self.year, self.month, "تعديل أو إضافة مصاريف")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        ensure_period_open(self.year, self.month, "حذف مصاريف")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.year}/{self.month:02d} — {self.get_expense_type_display()} — {self.amount} ج.م"


class CostingPeriodQuerySet(models.QuerySet):
    """Guard bulk deletes so finalized periods cannot be removed in bulk."""

    def delete(self):
        if self.filter(status=CostingPeriod.Status.FINALIZED).exists():
            raise ValidationError("لا يمكن حذف فترة تكاليف معتمدة نهائيًا.")
        return super().delete()


class CostingPeriod(models.Model):
    """Monthly costing period with owner-selected allocation & waste policies and lifecycle status."""

    objects = CostingPeriodQuerySet.as_manager()

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "مبدئي / قيد الإعداد"
        FINALIZED = "FINALIZED", "معتمد نهائيًا"

    class AllocationPolicy(models.TextChoices):
        UNITS_PRODUCED = "UNITS_PRODUCED", "توزيع حسب عدد الوحدات الجيدة المنتجة"
        MATERIAL_COST = "MATERIAL_COST", "توزيع حسب تكلفة المواد الفعلية المستهلكة"
        MATERIAL_WEIGHT = "MATERIAL_WEIGHT", "توزيع حسب كمية/وزن المواد الفعلية المستهلكة"
        EQUAL_PER_LINE = "EQUAL_PER_LINE", "توزيع بالتساوي على خطوط الإنتاج ثم حسب الوحدات"

    class WastePolicy(models.TextChoices):
        ABSORBED_NORMAL = "ABSORBED_NORMAL", "تحميل الهالك الطبيعي على تكلفة الوحدات الجيدة"
        SEPARATE_ABNORMAL = "SEPARATE_ABNORMAL", "فصل تكلفة الهالك غير الطبيعي كمصروف مستقل"

    year = models.PositiveSmallIntegerField("السنة")
    month = models.PositiveSmallIntegerField(
        "الشهر",
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    start_date = models.DateField("بداية الفترة", null=True, blank=True, editable=False)
    end_date = models.DateField("نهاية الفترة", null=True, blank=True, editable=False)
    status = models.CharField(
        "حالة الفترة",
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    allocation_policy = models.CharField(
        "سياسة توزيع المصاريف المشتركة",
        max_length=30,
        choices=AllocationPolicy.choices,
        default=AllocationPolicy.UNITS_PRODUCED,
    )
    waste_policy = models.CharField(
        "سياسة معالجة الهالك",
        max_length=30,
        choices=WastePolicy.choices,
        default=WastePolicy.ABSORBED_NORMAL,
        help_text=(
            "ABSORBED_NORMAL: تكلفة المواد المستهلكة بالكامل (بما فيها نصيب الهالك) تُحمّل على الوحدات الجيدة. "
            "SEPARATE_ABNORMAL: تُفصل تكلفة المواد المقابلة للهالك المسجل بسياسة تناسب موثّقة وتُعرض كمصروف مستقل."
        ),
    )
    material_valuation_method = models.CharField(
        "طريقة تقييم المواد",
        max_length=30,
        choices=[("OWNER_STANDARD_PRICE", "سعر مالك يدوي لكل مادة خام (Standard)")],
        default="OWNER_STANDARD_PRICE",
        editable=False,
        help_text=(
            "الطريقة الوحيدة المدعومة حاليًا لأن قيد المخزون لا يحمل بيانات تكلفة؛ "
            "بديل FIFO/المتوسط المرجح غير متاح حتى تُرحّل تكاليف المشتريات. الأرقام مؤقتة حتى يعتمدها المحاسب."
        ),
    )
    labor_recorded = models.BooleanField(
        "تم تسجيل مصاريف العمالة",
        default=False,
        editable=False,
        help_text="يفرّق بين غياب بند العمالة وبين تسجيله صراحة بقيمة 0.00.",
    )
    labor_total = models.DecimalField("إجمالي مصاريف العمالة", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    electricity_total = models.DecimalField("إجمالي مصاريف الكهرباء", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    water_total = models.DecimalField("إجمالي مصاريف المياه", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    other_industrial_total = models.DecimalField("إجمالي المصاريف الصناعية الأخرى", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    materials_total = models.DecimalField("إجمالي تكلفة المواد الفعلية", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    total_production_cost = models.DecimalField("إجمالي تكلفة الإنتاج", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    is_complete = models.BooleanField("مكتمل البيانات والاحتساب", default=False)
    missing_data_notes = models.TextField("ملاحظات نواقص البيانات", blank=True)
    calculated_at = models.DateTimeField("تاريخ آخر احتساب", null=True, blank=True)
    calculated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="calculated_costing_periods",
        verbose_name="احسب بواسطة",
    )
    finalized_at = models.DateTimeField("تاريخ الاعتماد", null=True, blank=True)
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="finalized_costing_periods",
        verbose_name="اعتمد بواسطة",
    )
    reopened_at = models.DateTimeField("تاريخ إعادة الفتح", null=True, blank=True)
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reopened_costing_periods",
        verbose_name="أعيد فتحه بواسطة",
    )
    reopen_reason = models.CharField("سبب إعادة الفتح", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(fields=["year", "month"], name="unique_costing_period_year_month"),
        ]
        permissions = [
            (
                "view_costing_financials",
                "Can view monthly costing financial data (periods, entries, expenses, prices)",
            ),
        ]
        verbose_name = "فترة تكاليف شهرية"
        verbose_name_plural = "فترات التكاليف الشهرية"

    def delete(self, *args, **kwargs):
        if self.status == self.Status.FINALIZED:
            raise ValidationError("لا يمكن حذف فترة تكاليف معتمدة نهائيًا.")
        return super().delete(*args, **kwargs)

    def resolved_date_range(self):
        """Explicit ``(start_date, end_date)`` boundaries for this calendar month.

        Falls back to the calculated calendar range for any legacy row that was
        created before the explicit dates existed, so a period always has a
        well-defined month boundary.
        """
        if self.start_date and self.end_date:
            return self.start_date, self.end_date
        last_day = calendar.monthrange(self.year, self.month)[1]
        return date(self.year, self.month, 1), date(self.year, self.month, last_day)

    def __str__(self):
        return f"تكاليف {self.year}/{self.month:02d} ({self.get_status_display()})"


class ProductCostingEntry(models.Model):
    """Costing breakdown and yield analysis for each product in a monthly period."""
    period = models.ForeignKey(
        CostingPeriod,
        on_delete=models.CASCADE,
        related_name="product_entries",
        verbose_name="الفترة",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="costing_entries",
        verbose_name="المنتج",
    )
    production_line = models.ForeignKey(
        ProductionLine,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="خط الإنتاج",
    )
    good_units_produced = models.DecimalField(
        "الوحدات الجيدة المنتجة",
        max_digits=18,
        decimal_places=4,
        default=Decimal("0"),
    )
    scrap_units_produced = models.DecimalField(
        "الهالك المسجل",
        max_digits=18,
        decimal_places=4,
        default=Decimal("0"),
    )
    rolls_consumed = models.DecimalField(
        "عدد الرولات المستهلكة",
        max_digits=18,
        decimal_places=4,
        default=Decimal("0"),
    )
    expected_cartons = models.DecimalField(
        "الكراتين المتوقعة",
        max_digits=18,
        decimal_places=4,
        default=Decimal("0"),
    )
    actual_good_cartons = models.DecimalField(
        "الكراتين الجيدة الفعلية",
        max_digits=18,
        decimal_places=4,
        default=Decimal("0"),
    )
    yield_shortfall_percent = models.DecimalField(
        "نسبة عجز العائد %",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    yield_variance_cartons = models.DecimalField(
        "فارق الكراتين (الفعلي - المتوقع)",
        max_digits=18,
        decimal_places=4,
        default=Decimal("0"),
    )
    actual_material_cost = models.DecimalField(
        "تكلفة المواد الفعلية (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="إجمالي تكلفة المواد المستهلكة فعليًا خلال الفترة؛ يعادل مجموع الجزء المحمّل على الجيد وجزء الهالك المنفصل.",
    )
    material_cost_assigned_to_good = models.DecimalField(
        "تكلفة المواد المحمّلة على الإنتاج الجيد (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    waste_material_cost = models.DecimalField(
        "تكلفة المواد المنفصلة عن الهالك (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="صفر عندما تكون سياسة الهالك «تحميل على الجيد»؛ لا تُضاف فوق إجمالي تكلفة المواد حتى لا تُحتسب المادة مرتين.",
    )
    allocated_labor_cost = models.DecimalField(
        "نصيب العمالة (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    allocated_electricity_cost = models.DecimalField(
        "نصيب الكهرباء (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    allocated_water_cost = models.DecimalField(
        "نصيب المياه (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    allocated_other_cost = models.DecimalField(
        "نصيب المصاريف الصناعية الأخرى (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    total_production_cost = models.DecimalField(
        "إجمالي تكلفة الإنتاج (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    cost_per_good_unit = models.DecimalField(
        "تكلفة الوحدة الجيدة (ج.م)",
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
    )
    selling_price_snapshot = models.DecimalField(
        "سعر البيع المرجعي (ج.م)",
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="سعر البيع المعتمد من المالك للعرض المقارن فقط دون احتساب أرباح.",
    )
    material_breakdown = models.JSONField("تفاصيل استهلاك وتسعير المواد", default=list)
    calculation_status = models.CharField("حالة الاحتساب", max_length=20, default="COMPLETE")
    notes = models.TextField("ملاحظات", blank=True)

    class Meta:
        ordering = ["product__sku"]
        constraints = [
            models.UniqueConstraint(fields=["period", "product"], name="unique_product_costing_entry"),
        ]
        verbose_name = "بند تكلفة منتج"
        verbose_name_plural = "بنود تكاليف المنتجات"

    def __str__(self):
        return f"{self.period} — {self.product.sku}: {self.total_production_cost} ج.م"


class ProductionOrderCostingEntry(models.Model):
    """Cost breakdown of one production order inside a monthly period.

    Material cost comes from that order's own non-reversed issues in the period,
    so the sum of order entries equals the product entry and the monthly totals.
    """

    period = models.ForeignKey(
        CostingPeriod,
        on_delete=models.CASCADE,
        related_name="order_entries",
        verbose_name="الفترة",
    )
    order = models.ForeignKey(
        ProductionOrder,
        on_delete=models.PROTECT,
        related_name="costing_entries",
        verbose_name="أمر الإنتاج",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="order_costing_entries",
        verbose_name="المنتج",
    )
    production_line = models.ForeignKey(
        ProductionLine,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="order_costing_entries",
        verbose_name="خط الإنتاج",
    )
    good_units_produced = models.DecimalField("الوحدات الجيدة", max_digits=18, decimal_places=4, default=Decimal("0"))
    scrap_units_produced = models.DecimalField("الهالك المسجل", max_digits=18, decimal_places=4, default=Decimal("0"))
    actual_material_cost = models.DecimalField("تكلفة المواد الفعلية (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    material_cost_assigned_to_good = models.DecimalField(
        "تكلفة المواد المحمّلة على الجيد (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    waste_material_cost = models.DecimalField("تكلفة المواد المنفصلة عن الهالك (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    allocated_labor_cost = models.DecimalField("نصيب العمالة (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    allocated_electricity_cost = models.DecimalField("نصيب الكهرباء (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    allocated_water_cost = models.DecimalField("نصيب المياه (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    allocated_other_cost = models.DecimalField("نصيب المصاريف الأخرى (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    total_production_cost = models.DecimalField("إجمالي تكلفة الأمر (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    cost_per_good_unit = models.DecimalField(
        "تكلفة الوحدة الجيدة (ج.م)", max_digits=18, decimal_places=4, null=True, blank=True
    )
    calculation_status = models.CharField("حالة الاحتساب", max_length=20, default="COMPLETE")

    class Meta:
        ordering = ["order__order_number"]
        constraints = [
            models.UniqueConstraint(fields=["period", "order"], name="unique_order_costing_entry"),
        ]
        verbose_name = "بند تكلفة أمر إنتاج"
        verbose_name_plural = "بنود تكاليف أوامر الإنتاج"

    def __str__(self):
        return f"{self.period} — {self.order.order_number}: {self.total_production_cost} ج.م"


class ProductSellingPrice(models.Model):
    """Optional selling price owned and managed by the factory owner. For display only."""
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        related_name="selling_price_info",
        verbose_name="المنتج",
    )
    selling_price = models.DecimalField(
        "سعر البيع (ج.م)",
        max_digits=18,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )
    unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        verbose_name="وحدة البيع",
    )
    notes = models.CharField("ملاحظات", max_length=240, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        verbose_name="حُدث بواسطة",
    )

    class Meta:
        verbose_name = "سعر بيع المنتج"
        verbose_name_plural = "أسعار بيع المنتجات"

    def __str__(self):
        return f"{self.product.sku}: {self.selling_price} ج.م / {self.unit.code}"
