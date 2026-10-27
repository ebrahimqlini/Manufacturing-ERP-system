from decimal import Decimal
import calendar
import uuid
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import connection, models, transaction
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


def ensure_profit_period_open(year, month, action_label):
    """Reject changes that would invalidate a finalized monthly profit period."""
    if MonthlyProfitPeriod.objects.filter(
        year=year, month=month, status=MonthlyProfitPeriod.Status.FINALIZED
    ).exists():
        raise ValidationError(
            f"لا يمكن {action_label} لشهر {year}/{month:02d} بعد اعتماد نتيجة الشهر نهائيًا؛ "
            "أعد فتح فترة الأرباح والخسائر أولاً."
        )


# --- Month-scoped closing locks -------------------------------------------
# Every write that changes the financial contents of a calendar month must
# serialize against the finalization of that month BEFORE it validates that the
# month is still open. A plain ``select_for_update()`` is not sufficient on its
# own: it locks nothing when the period row does not exist yet, so the very
# first posting of a month could pass its "is the month open?" check against a
# missing row and commit into a month that another transaction finalizes at the
# same time. PostgreSQL transaction advisory locks exist whether or not any row
# exists yet and are released automatically at commit or rollback.
#
# Locking protocol (used consistently by every period-sensitive writer):
#   1. Acquire ``lock_costing_month(year, month)`` first, then, if the write
#      also affects the profit period of the month, ``lock_profit_month``.
#   2. Only afterwards acquire row locks (period rows, documents, inventory).
#   3. Writers that affect only one of the two periods take only that lock;
#      never acquire the costing lock after the profit lock (that would invert
#      the order and could deadlock against finalize/reopen/reversal paths).
# Both locks are re-entrant inside one transaction (PostgreSQL counts
# acquisitions), so nested service calls (finalize -> calculate -> expense
# save) never block on themselves.

# Arbitrary but fixed per-app salts: "CP" for costing, "PF" for profit. They
# keep the two lock families from ever colliding on the same 64-bit key.
_COSTING_MONTH_LOCK_SALT = 0x43500001
_PROFIT_MONTH_LOCK_SALT = 0x50460001


def _month_advisory_key(salt, year, month):
    return (salt << 32) | ((int(year) & 0xFFFFFFFF) << 8) | (int(month) & 0xFF)


def _advisory_month_lock(salt, year, month):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [_month_advisory_key(salt, year, month)],
        )


def lock_costing_month(year, month):
    """Serialize every costing-affecting write for one calendar month.

    Must be called inside ``transaction.atomic()``. Acquire it before the
    profit-month lock and before any row lock.
    """
    _advisory_month_lock(_COSTING_MONTH_LOCK_SALT, year, month)


def lock_profit_month(year, month):
    """Serialize every profit-affecting write for one calendar month.

    Must be called inside ``transaction.atomic()``. Always acquire the costing
    month lock first when both periods are affected.
    """
    _advisory_month_lock(_PROFIT_MONTH_LOCK_SALT, year, month)


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
    supplier_name = models.CharField(
        "المورد أو مصدر المواصفة",
        max_length=180,
        blank=True,
        help_text=(
            "اسم مورد المادة الخام أو مصدر المواصفة المعتمدة؛ يُحفظ كنص وثّق "
            "دون ربط بسجل المورد التلقائي."
        ),
    )
    specification_reference = models.CharField(
        "مرجع المواصفة",
        max_length=120,
        blank=True,
        help_text="رقم أو مرجع وثيقة مواصفة المورد / المواصفة الداخلية.",
    )
    revision = models.PositiveSmallIntegerField(
        "إصدار المواصفة",
        default=1,
        validators=[MinValueValidator(1)],
    )
    effective_date = models.DateField("تاريخ سريان المواصفة", null=True, blank=True)
    target_yield_percent = models.DecimalField(
        "نسبة العائد المستهدفة %",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01")), MaxValueValidator(Decimal("100"))],
        help_text="تُملأ فقط عند اعتمادها صراحةً؛ لا يُفترض أي قيمة افتراضية.",
    )
    max_scrap_percent = models.DecimalField(
        "أقصى نسبة هالك مقبولة %",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text=(
            "حد الهالك الأقصى المعتمد؛ يُترك فارغًا حين لا يوجد حد معتمد حتى "
            "لا يُصنَّف الهالك طبيعيًا أو زائدًا دون سند."
        ),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_yield_standards",
        verbose_name="أنشئ بواسطة",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="updated_yield_standards",
        verbose_name="عُدِّل بواسطة",
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
            models.CheckConstraint(
                condition=Q(target_yield_percent__isnull=True)
                | (
                    Q(target_yield_percent__gte=Decimal("0.01"))
                    & Q(target_yield_percent__lte=Decimal("100"))
                ),
                name="yield_target_percent_range",
            ),
            models.CheckConstraint(
                condition=Q(max_scrap_percent__isnull=True)
                | (
                    Q(max_scrap_percent__gte=Decimal("0"))
                    & Q(max_scrap_percent__lte=Decimal("100"))
                ),
                name="yield_max_scrap_percent_range",
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
            ensure_profit_period_open(year, month, action_label)

    def update(self, **kwargs):
        self._assert_periods_open("تعديل أو إضافة مصاريف")
        return super().update(**kwargs)

    def delete(self):
        self._assert_periods_open("حذف مصاريف")
        return super().delete()

    def active(self):
        return self.filter(status=MonthlyExpense.Status.ACTIVE)


class MonthlyExpense(models.Model):
    """Monthly factory expenses (labor, electricity, water, other industrial) with attribution scope."""

    objects = MonthlyExpenseQuerySet.as_manager()

    class ExpenseType(models.TextChoices):
        LABOR = "LABOR", "مصاريف عمالة"
        ELECTRICITY = "ELECTRICITY", "مصاريف كهرباء"
        WATER = "WATER", "مصاريف مياه"
        MAINTENANCE = "MAINTENANCE", "مصاريف صيانة"
        OTHER_INDUSTRIAL = "OTHER_INDUSTRIAL", "مصاريف تشغيلية أخرى"

    class AllocationScope(models.TextChoices):
        FACTORY = "FACTORY", "مشترك للمصنع ككل"
        LINE = "LINE", "خاص بخط إنتاج محدد"
        PRODUCT = "PRODUCT", "خاص بمنتج محدد"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "ساري"
        REVERSED = "REVERSED", "معكوس"

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
    expense_date = models.DateField("تاريخ المصروف", null=True, blank=True)
    payment_date = models.DateField("تاريخ السداد", null=True, blank=True)
    reference = models.CharField("مرجع المصروف", max_length=120, blank=True)
    status = models.CharField(
        "حالة المصروف",
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    reversed_at = models.DateTimeField("تاريخ العكس", null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversed_expenses",
        verbose_name="عكس بواسطة",
    )
    reversal_reason = models.CharField("سبب العكس", max_length=255, blank=True)
    submission_key = models.UUIDField(default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_expenses",
        verbose_name="أنشئ بواسطة",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="updated_expenses",
        verbose_name="آخر تعديل بواسطة",
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
            models.UniqueConstraint(
                fields=["submission_key"],
                name="unique_monthly_expense_submission",
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
        if self.expense_date and self.payment_date and self.payment_date < self.expense_date:
            errors["payment_date"] = "تاريخ السداد لا يسبق تاريخ المصروف."
        if self.year is not None and self.month is not None and 1 <= self.month <= 12:
            if self.expense_date is None:
                self.expense_date = date(self.year, self.month, 1)
            # Applies to new and existing rows alike so a finalized month cannot
            # be changed indirectly through creation, editing, or the admin.
            ensure_period_open(self.year, self.month, "تعديل أو إضافة مصاريف")
            ensure_profit_period_open(self.year, self.month, "تعديل أو إضافة مصاريف")
        if errors:
            raise ValidationError(errors)

    def _lock_financial_months(self):
        # An expense changes both the costing totals and the profit totals of
        # its month, so it must serialize against the finalization of both
        # periods before the open-period checks below run. The locks are only
        # meaningful inside a transaction, so save/delete open one when the
        # caller has not already.
        lock_costing_month(self.year, self.month)
        lock_profit_month(self.year, self.month)

    def save(self, *args, **kwargs):
        with transaction.atomic():
            self._lock_financial_months()
            ensure_period_open(self.year, self.month, "تعديل أو إضافة مصاريف")
            ensure_profit_period_open(self.year, self.month, "تعديل أو إضافة مصاريف")
            if self.expense_date is None and self.year and self.month:
                self.expense_date = date(self.year, self.month, 1)
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            self._lock_financial_months()
            ensure_period_open(self.year, self.month, "حذف مصاريف")
            ensure_profit_period_open(self.year, self.month, "حذف مصاريف")
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
        choices=[("LAST_PURCHASE_COST", "آخر سعر شراء للمادة (Last Purchase Cost)")],
        default="LAST_PURCHASE_COST",
        editable=False,
        help_text=(
            "الطريقة الوحيدة المدعومة حاليًا: آخر سعر شراء من استلام مشتريات صحيح، "
            "بحسب وقت الترحيل الناجح للنظام. سعر المالك المؤرخ احتياطي معلن عند غياب LPC."
        ),
    )
    labor_recorded = models.BooleanField(
        "تم تسجيل مصاريف العمالة",
        default=False,
        editable=False,
        help_text="يفرّق بين غياب بند العمالة وبين تسجيله صراحة بقيمة 0.00.",
    )
    electricity_recorded = models.BooleanField(
        "تم تسجيل مصاريف الكهرباء",
        default=False,
        editable=False,
        help_text="لقطة وقت الاحتساب تفرّق بين غياب بند الكهرباء وتسجيله صراحة بقيمة 0.00.",
    )
    water_recorded = models.BooleanField(
        "تم تسجيل مصاريف المياه",
        default=False,
        editable=False,
        help_text="لقطة وقت الاحتساب تفرّق بين غياب بند المياه وتسجيله صراحة بقيمة 0.00.",
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
            ("finalize_costing_period", "Can finalize monthly costing periods"),
            ("reopen_costing_period", "Can reopen finalized costing periods"),
            ("calculate_monthly_costing", "Can calculate monthly costing"),
            ("manage_expenses", "Can create, edit, and reverse monthly expenses"),
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
    yield_standard = models.ForeignKey(
        RollYieldStandard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="costing_entries",
        verbose_name="معيار العائد المستخدم وقت الاحتساب",
        help_text=(
            "يُحفظ للاحتفاظ بمرجع المعيار المستخدم وقت الاحتساب؛ قيم العتبات "
            "أسفله لقطة ثابتة لا تتغير بتعديل المعيار لاحقًا."
        ),
    )
    yield_standard_revision = models.PositiveSmallIntegerField(
        "إصدار معيار العائد وقت الاحتساب",
        null=True,
        blank=True,
    )
    yield_scrap_threshold_percent = models.DecimalField(
        "عتبة الهالك القصوى وقت الاحتساب %",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="لقطة من الحد المعتمد وقت الاحتساب؛ null تعني عدم وجود حد وقتها.",
    )
    yield_target_percent = models.DecimalField(
        "نسبة العائد المستهدفة وقت الاحتساب %",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
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
        "تكلفة هالك المواد المقاس (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=(
            "مجموع تكلفة هالك المواد المقاس والمُقيّم وقت الترحيل. القيمة غير المتاحة "
            "تبقى فارغة في سجل الحدث وتمنع اكتمال الفترة."
        ),
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
    material_cost_per_good_unit = models.DecimalField(
        "تكلفة المواد المباشرة للوحدة الجيدة (ج.م)",
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        help_text=(
            "لقطة من تكلفة المواد المحمّلة على الجيد مقسومة على الوحدات الجيدة. "
            "تُستخدم لتكلفة المبيعات دون إعادة احتساب تكلفة المنتج، ولا تشمل "
            "العمالة أو الكهرباء أو الصيانة أو المصاريف التشغيلية الأخرى."
        ),
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
    # Immutable event snapshots copied from measured material-waste records.
    scrap_breakdown = models.JSONField(
        "تفاصيل الهالك لكل مادة",
        default=list,
        help_text="تفاصيل أحداث قياس الهالك المادي والقيم المثبتة وقت الترحيل.",
    )
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
    waste_material_cost = models.DecimalField(
        "تكلفة الهالك المادي المقاس (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="مجموع تكاليف أحداث الهالك المادي المسجلة لهذا الأمر وفق سعر الوحدة وقت الترحيل.",
    )
    allocated_labor_cost = models.DecimalField("نصيب العمالة (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    allocated_electricity_cost = models.DecimalField("نصيب الكهرباء (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    allocated_water_cost = models.DecimalField("نصيب المياه (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    allocated_other_cost = models.DecimalField("نصيب المصاريف الأخرى (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    total_production_cost = models.DecimalField("إجمالي تكلفة الأمر (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    cost_per_good_unit = models.DecimalField(
        "تكلفة الوحدة الجيدة (ج.م)", max_digits=18, decimal_places=4, null=True, blank=True
    )
    # Immutable per-event material waste valuation snapshots used by costing reports.
    scrap_breakdown = models.JSONField(
        "تفاصيل الهالك لكل مادة",
        default=list,
        help_text="لقطات أحداث الهالك المادي المرتبطة بأوامر هذا المنتج خلال الفترة.",
    )
    # Reserved schema compatibility for the same unresolved attribution design.
    # The current engine never sets it; it exists only so an already-applied
    # forward migration remains consistent without dropping columns.
    yield_classification = models.CharField(
        "تصنيف الهالك التشغيلي (محجوز)", max_length=30, blank=True, default=""
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


class MonthlyProfitPeriodQuerySet(models.QuerySet):
    """Protect finalized monthly results from bulk edits or deletion."""

    def _assert_unfinalized(self, action_label):
        if self.filter(status=MonthlyProfitPeriod.Status.FINALIZED).exists():
            raise ValidationError(
                f"لا يمكن {action_label} لفترة أرباح وخسائر معتمدة نهائيًا؛ أعد فتحها أولاً."
            )

    def update(self, **kwargs):
        self._assert_unfinalized("تعديل")
        return super().update(**kwargs)

    def delete(self):
        self._assert_unfinalized("حذف")
        return super().delete()


class MonthlyProfitPeriod(models.Model):
    """Persisted monthly management profit, operating expenses, and loss carry-forward.

    This is an internally consistent management result, not a general-ledger
    accounting close. Sales revenue is recognized from active shipment lines;
    COGS is the persisted direct-material cost of the products actually shipped;
    operating expenses are recorded period expenses and are never allocated or
    deducted a second time inside COGS.
    """

    objects = MonthlyProfitPeriodQuerySet.as_manager()

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "مبدئي / قابل للتعديل"
        FINALIZED = "FINALIZED", "معتمد نهائيًا"

    class ScrapTreatment(models.TextChoices):
        NONE = "NONE", "لا يوجد هالك مالي معترف به"
        ABSORBED = "ABSORBED_IN_PRODUCT_COST", "مضمّن في تكلفة الوحدات الجيدة"
        SEPARATED = "SEPARATED_DEDUCTED", "مفصل ومخصوم كخط خسارة من نتيجة الشهر"
        NO_COSTING = "NO_COSTING_PERIOD", "لا توجد فترة تكاليف مرتبطة"

    year = models.PositiveSmallIntegerField("السنة")
    month = models.PositiveSmallIntegerField(
        "الشهر",
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    start_date = models.DateField("بداية الفترة", null=True, blank=True, editable=False)
    end_date = models.DateField("نهاية الفترة", null=True, blank=True, editable=False)
    status = models.CharField(
        "الحالة",
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    costing_period = models.ForeignKey(
        CostingPeriod,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="profit_periods",
        verbose_name="فترة التكاليف المرتبطة",
    )

    revenue = models.DecimalField(
        "صافي إيراد المبيعات (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    cogs = models.DecimalField(
        "تكلفة البضاعة المباعة (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    gross_profit = models.DecimalField(
        "إجمالي الربح (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    labor_expense = models.DecimalField(
        "مصاريف العمالة (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    electricity_expense = models.DecimalField(
        "مصاريف الكهرباء (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    maintenance_expense = models.DecimalField(
        "مصاريف الصيانة (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    other_expense = models.DecimalField(
        "مصاريف تشغيلية أخرى (ج.م)", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    operating_expenses_total = models.DecimalField(
        "إجمالي المصاريف التشغيلية (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    monthly_result = models.DecimalField(
        "نتيجة الشهر قبل تغطية الخسائر (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    opening_accumulated_loss = models.DecimalField(
        "رصيد الخسائر المتراكمة الافتتاحي (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    loss_recovered = models.DecimalField(
        "الخسائر السابقة المستردة من نتيجة الشهر (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    closing_accumulated_loss = models.DecimalField(
        "رصيد الخسائر المتراكمة الختامي (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    profit_after_loss_recovery = models.DecimalField(
        "الربح المتبقي بعد تغطية الخسائر (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    separated_scrap_loss = models.DecimalField(
        "خسارة الهالك المفصل المخصومة من نتيجة الشهر (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=(
            "الجزء من تكلفة الهالك المعزول عن تكلفة الوحدات الجيدة بواسطة سياسة "
            "الفترة ولم يدخل تكلفة الإنتاج أو تكلفة البضاعة المباعة؛ يُخصم مرة واحدة فقط."
        ),
    )
    absorbed_scrap_cost = models.DecimalField(
        "تكلفة الهالك المحمّلة ضمن تكلفة الإنتاج (ج.م، عرض فقط)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=(
            "تكلفة أحداث الهالك المادي المقاس والمعترف بها ضمن تكلفة الإنتاج تحت "
            "سياسة التحميل؛ لا تُخصم مرة أخرى لأنها ضمن تكلفة الوحدة ثم COGS عند البيع."
        ),
    )
    unreconciled_scrap_cost = models.DecimalField(
        "تكلفة هالك غير محوَّلة أو غير مؤكدة (ج.م)",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=(
            "هالك مفصل لا يزال غير مخصوم: إنتاج غير مكتمل أو احتساب غير معتمد؛ "
            "يُعرض صراحةً ولا يُخصم من نتيجة الشهر."
        ),
    )
    scrap_treatment = models.CharField(
        "معالجة الهالك في النتيجة",
        max_length=30,
        choices=ScrapTreatment.choices,
        default=ScrapTreatment.NONE,
    )

    labor_recorded = models.BooleanField("تم تسجيل مصاريف العمالة", default=False, editable=False)
    electricity_recorded = models.BooleanField("تم تسجيل مصاريف الكهرباء", default=False, editable=False)
    maintenance_recorded = models.BooleanField("تم تسجيل مصاريف الصيانة", default=False, editable=False)
    other_recorded = models.BooleanField("تم تسجيل مصاريف تشغيلية أخرى", default=False, editable=False)
    cogs_complete = models.BooleanField("تكلفة البضاعة المباعة مكتملة", default=False, editable=False)
    is_complete = models.BooleanField("البيانات مكتملة", default=False, editable=False)
    missing_data_notes = models.TextField("ملاحظات البيانات الناقصة", blank=True)

    active_shipment_count = models.PositiveIntegerField("عدد الشحنات النشطة", default=0, editable=False)
    active_shipment_line_count = models.PositiveIntegerField("عدد بنود الشحن النشطة", default=0, editable=False)
    revenue_product_count = models.PositiveIntegerField("عدد المنتجات المباعة", default=0, editable=False)
    expense_record_count = models.PositiveIntegerField("عدد سجلات المصاريف النشطة", default=0, editable=False)

    calculated_at = models.DateTimeField("تاريخ آخر احتساب", null=True, blank=True)
    calculated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="calculated_profit_periods",
        verbose_name="احسب بواسطة",
    )
    finalized_at = models.DateTimeField("تاريخ الاعتماد", null=True, blank=True)
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="finalized_profit_periods",
        verbose_name="اعتمد بواسطة",
    )
    reopened_at = models.DateTimeField("تاريخ إعادة الفتح", null=True, blank=True)
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reopened_profit_periods",
        verbose_name="أعيد فتحه بواسطة",
    )
    reopen_reason = models.CharField("سبب إعادة الفتح", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(
                fields=["year", "month"], name="unique_monthly_profit_period"
            ),
            models.CheckConstraint(
                condition=Q(year__gte=2020) & Q(year__lte=2100),
                name="monthly_profit_year_valid",
            ),
        ]
        permissions = [
            ("finalize_monthly_profit", "Can finalize monthly profit periods"),
            ("reopen_monthly_profit", "Can reopen finalized monthly profit periods"),
            ("calculate_monthly_profit", "Can calculate monthly profit"),
        ]
        verbose_name = "فترة أرباح وخسائر شهرية"
        verbose_name_plural = "فترات الأرباح والخسائر الشهرية"

    def save(self, *args, **kwargs):
        # A finalized row may only move to DRAFT through the audited reopen
        # service, or from DRAFT to FINALIZED through finalization. Direct
        # edits to an already-finalized instance are rejected.
        if self.pk and not self._state.adding:
            previous_status = (
                type(self).objects.filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )
            allowed_reopen = bool(getattr(self, "_allow_reopen_save", False))
            allowed_finalize = bool(getattr(self, "_allow_finalize_save", False))
            if previous_status == self.Status.FINALIZED:
                if self.status == self.Status.FINALIZED or not allowed_reopen:
                    raise ValidationError(
                        "لا يمكن تعديل فترة أرباح وخسائر معتمدة نهائيًا مباشرة؛ "
                        "أعد فتحها ووثّق سبب التصحيح."
                    )
            elif self.status == self.Status.FINALIZED and not allowed_finalize:
                raise ValidationError(
                    "اعتماد نتيجة الشهر يتم من خدمة الاعتماد المخوّلة فقط."
                )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        stored_status = (
            type(self).objects.filter(pk=self.pk)
            .values_list("status", flat=True)
            .first()
            if self.pk
            else self.status
        )
        if self.status == self.Status.FINALIZED or stored_status == self.Status.FINALIZED:
            raise ValidationError("لا يمكن حذف فترة أرباح وخسائر معتمدة نهائيًا.")
        return super().delete(*args, **kwargs)

    @property
    def recovery_state(self):
        if self.closing_accumulated_loss > 0 and self.monthly_result < 0:
            return "LOSS_ADDED"
        if self.loss_recovered > 0 and self.closing_accumulated_loss > 0:
            return "PARTIAL_RECOVERY"
        if self.loss_recovered > 0:
            return "FULL_RECOVERY"
        if self.closing_accumulated_loss > 0:
            return "LOSS_CARRIED_FORWARD"
        return "NO_LOSS"

    def __str__(self):
        return f"أرباح وخسائر {self.year}/{self.month:02d} ({self.get_status_display()})"


class MonthlyProfitCOGSLineQuerySet(models.QuerySet):
    """Protect COGS detail that belongs to a finalized monthly result."""

    def _assert_open(self):
        if MonthlyProfitPeriod.objects.filter(
            pk__in=self.values("profit_period_id"),
            status=MonthlyProfitPeriod.Status.FINALIZED,
        ).exists():
            raise ValidationError(
                "لا يمكن تعديل أو حذف بنود تكلفة بضاعة تخص فترة أرباح معتمدة نهائيًا."
            )

    def update(self, **kwargs):
        self._assert_open()
        return super().update(**kwargs)

    def delete(self):
        self._assert_open()
        return super().delete()


class MonthlyProfitCOGSLine(models.Model):
    """Persisted COGS detail for one product sold inside a monthly profit period."""

    objects = MonthlyProfitCOGSLineQuerySet.as_manager()

    class CalculationStatus(models.TextChoices):
        COMPLETE = "COMPLETE", "مكتمل"
        MISSING_COST = "MISSING_COST", "تكلفة غير متاحة"
        NON_AUTHORITATIVE = "NON_AUTHORITATIVE", "تكلفة غير معتمدة نهائيًا"

    profit_period = models.ForeignKey(
        MonthlyProfitPeriod,
        on_delete=models.CASCADE,
        related_name="cogs_lines",
        verbose_name="فترة الأرباح والخسائر",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="profit_cogs_lines",
        verbose_name="المنتج",
    )
    quantity_sold_base = models.DecimalField(
        "الكمية المباعة بالوحدة الأساسية", max_digits=30, decimal_places=12
    )
    base_uom_code = models.CharField("رمز الوحدة الأساسية", max_length=40)
    base_uom_name_ar = models.CharField("اسم الوحدة الأساسية", max_length=120)
    revenue = models.DecimalField("صافي إيراد المنتج (ج.م)", max_digits=18, decimal_places=2)
    cost_per_base_unit = models.DecimalField(
        "تكلفة المواد المباشرة للوحدة الأساسية (ج.م)",
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )
    cogs = models.DecimalField("تكلفة البضاعة المباعة (ج.م)", max_digits=18, decimal_places=2)
    shipment_count = models.PositiveIntegerField("عدد الشحنات", default=0)
    source_costing_entry = models.ForeignKey(
        ProductCostingEntry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="profit_cogs_lines",
        verbose_name="بند التكلفة المصدر",
    )
    source_period = models.ForeignKey(
        CostingPeriod,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="profit_cogs_lines",
        verbose_name="فترة التكلفة المصدر",
    )
    calculation_status = models.CharField(
        "حالة الاحتساب",
        max_length=20,
        choices=CalculationStatus.choices,
        default=CalculationStatus.COMPLETE,
    )
    shipment_references = models.JSONField("مراجع الشحنات", default=list)
    notes = models.CharField("ملاحظات", max_length=255, blank=True)

    class Meta:
        ordering = ["product__sku"]
        constraints = [
            models.UniqueConstraint(
                fields=["profit_period", "product"],
                name="unique_profit_cogs_product",
            ),
        ]
        verbose_name = "بند تكلفة بضاعة مباعة"
        verbose_name_plural = "بنود تكلفة البضاعة المباعة"

    def _ensure_open(self):
        if self.profit_period_id and MonthlyProfitPeriod.objects.filter(
            pk=self.profit_period_id,
            status=MonthlyProfitPeriod.Status.FINALIZED,
        ).exists():
            raise ValidationError(
                "لا يمكن تعديل أو حذف بنود تكلفة بضاعة تخص فترة أرباح معتمدة نهائيًا."
            )

    def save(self, *args, **kwargs):
        self._ensure_open()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._ensure_open()
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.profit_period} — {self.product.sku}: {self.cogs} ج.م"
