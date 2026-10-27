import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from catalog.models import Product, UnitOfMeasure


class ImmutablePostedQuerySet(models.QuerySet):
    """Reject routine ORM edits; posted data is corrected with a reversal."""

    def update(self, **kwargs):
        raise ValidationError("السجلات المرحلة غير قابلة للتعديل؛ استخدم حركة عكس.")

    def delete(self):
        raise ValidationError("السجلات المرحلة لا تُحذف؛ استخدم حركة عكس.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("السجلات المرحلة غير قابلة للتعديل؛ استخدم حركة عكس.")


class Warehouse(models.Model):
    code = models.CharField("رمز المخزن", max_length=40, unique=True)
    name_ar = models.CharField("اسم المخزن بالعربية", max_length=120)
    name_en = models.CharField("اسم المخزن بالإنجليزية", max_length=120, blank=True)
    active = models.BooleanField("نشط", default=True)

    class Meta:
        ordering = ["code"]

    def clean(self):
        self.code = self.code.strip().upper()
        if not self.name_ar.strip():
            raise ValidationError({"name_ar": "اسم المخزن بالعربية مطلوب."})

    def __str__(self):
        return f"{self.code} — {self.name_ar}"


class StorageLocation(models.Model):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="locations", verbose_name="المخزن")
    code = models.CharField("رمز الموقع", max_length=40)
    name_ar = models.CharField("اسم الموقع بالعربية", max_length=120)
    name_en = models.CharField("اسم الموقع بالإنجليزية", max_length=120, blank=True)
    active = models.BooleanField("نشط", default=True)

    class Meta:
        ordering = ["warehouse__code", "code"]
        constraints = [models.UniqueConstraint(fields=["warehouse", "code"], name="unique_location_per_warehouse")]
        verbose_name = "موقع تخزين"
        verbose_name_plural = "مواقع التخزين"

    def clean(self):
        self.code = self.code.strip().upper()
        if not self.name_ar.strip():
            raise ValidationError({"name_ar": "اسم الموقع بالعربية مطلوب."})
        if self.warehouse_id and not self.warehouse.active and not self.pk:
            raise ValidationError({"warehouse": "لا يمكن إضافة موقع إلى مخزن غير نشط."})
        if self.pk:
            previous = StorageLocation.objects.filter(pk=self.pk).first()
            if previous and (previous.warehouse_id != self.warehouse_id or previous.code != self.code):
                if self.ledger_entries.exists():
                    raise ValidationError("لا يمكن تغيير المخزن أو الرمز بعد تسجيل حركات على الموقع؛ عطّله وأنشئ موقعًا جديدًا.")

    def __str__(self):
        return f"{self.warehouse.code} / {self.code} — {self.name_ar}"


class StockMovement(models.Model):
    objects = ImmutablePostedQuerySet.as_manager()

    class Type(models.TextChoices):
        OPENING = "OPENING", "رصيد افتتاحي"
        RECEIPT = "RECEIPT", "استلام"
        ISSUE = "ISSUE", "صرف"
        TRANSFER = "TRANSFER", "تحويل"
        ADJUSTMENT_IN = "ADJUSTMENT_IN", "تسوية زيادة"
        ADJUSTMENT_OUT = "ADJUSTMENT_OUT", "تسوية نقص"
        REVERSAL = "REVERSAL", "عكس حركة"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField("المرجع الفريد", max_length=100, unique=True)
    movement_type = models.CharField("نوع الحركة", max_length=20, choices=Type.choices)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="stock_movements", verbose_name="الصنف")
    quantity = models.DecimalField("الكمية المدخلة", max_digits=30, decimal_places=12)
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="stock_movements", verbose_name="وحدة الإدخال")
    base_quantity = models.DecimalField("الكمية بوحدة المخزون الأساسية", max_digits=30, decimal_places=12)
    source_location = models.ForeignKey(StorageLocation, on_delete=models.PROTECT, null=True, blank=True, related_name="outgoing_movements", verbose_name="من موقع")
    destination_location = models.ForeignKey(StorageLocation, on_delete=models.PROTECT, null=True, blank=True, related_name="incoming_movements", verbose_name="إلى موقع")
    occurred_at = models.DateTimeField("وقت الحركة")
    posted_at = models.DateTimeField("وقت الترحيل", auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_movements", verbose_name="المستخدم")
    external_reference = models.CharField("مرجع مستند خارجي", max_length=120, blank=True)
    source_reference_type = models.CharField("نوع مصدر الحركة", max_length=60, blank=True, help_text="مرجع عام لتكاملات لاحقة دون ارتباط بتطبيق محدد.")
    source_reference_id = models.CharField("معرّف مصدر الحركة", max_length=100, blank=True)
    reason = models.CharField("سبب الحركة", max_length=240, blank=True)
    notes = models.TextField("ملاحظات", blank=True)
    reversal_of = models.OneToOneField("self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversal", verbose_name="الحركة الأصلية")

    class Meta:
        ordering = ["-occurred_at", "-posted_at"]
        indexes = [models.Index(fields=["product", "occurred_at"]), models.Index(fields=["movement_type", "occurred_at"])]
        constraints = [models.UniqueConstraint(fields=["source_reference_type", "source_reference_id"], condition=~models.Q(source_reference_type="") & ~models.Q(source_reference_id=""), name="unique_stock_source_reference")]
        verbose_name = "حركة مخزون"
        verbose_name_plural = "حركات المخزون"
        permissions = [
            ("post_movement", "Can post inventory movements"),
            ("reverse_movement", "Can reverse inventory movements"),
        ]

    def clean(self):
        errors = {}
        if self.quantity is not None and self.quantity <= 0:
            errors["quantity"] = "الكمية يجب أن تكون أكبر من صفر."
        if self.base_quantity is not None and self.base_quantity <= 0:
            errors["base_quantity"] = "الكمية الأساسية يجب أن تكون أكبر من صفر."
        typ = self.movement_type
        if typ in (self.Type.ADJUSTMENT_IN, self.Type.ADJUSTMENT_OUT, self.Type.REVERSAL) and not self.reason.strip():
            errors["reason"] = "سبب التسوية أو العكس مطلوب."
        if bool(self.source_reference_type) != bool(self.source_reference_id):
            errors["source_reference_id"] = "نوع المصدر ومعرّفه مطلوبان معًا."
        if typ in (self.Type.OPENING, self.Type.RECEIPT, self.Type.ADJUSTMENT_IN) and (not self.destination_location_id or self.source_location_id):
            errors["destination_location"] = "هذه الحركة تتطلب موقع وجهة فقط."
        elif typ in (self.Type.ISSUE, self.Type.ADJUSTMENT_OUT) and (not self.source_location_id or self.destination_location_id):
            errors["source_location"] = "هذه الحركة تتطلب موقع مصدر فقط."
        elif typ == self.Type.TRANSFER:
            if not self.source_location_id or not self.destination_location_id:
                errors["destination_location"] = "التحويل يتطلب موقع مصدر ووجهة."
            elif self.source_location_id == self.destination_location_id:
                errors["destination_location"] = "يجب أن يختلف موقع المصدر عن الوجهة."
        if self.product_id and self.unit_id:
            if self.unit_id != self.product.base_uom_id and not self.product.unit_conversions.filter(unit_id=self.unit_id, active=True).exists():
                errors["unit"] = "لا يوجد تحويل نشط لهذه الوحدة على الصنف."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk and StockMovement.objects.filter(pk=self.pk).exists():
            raise ValidationError("حركات المخزون المرحلة غير قابلة للتعديل؛ استخدم حركة عكس.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("حركات المخزون المرحلة لا تُحذف؛ استخدم حركة عكس.")

    def __str__(self):
        return f"{self.reference} — {self.get_movement_type_display()}"


class StockLedgerEntry(models.Model):
    objects = ImmutablePostedQuerySet.as_manager()

    movement = models.ForeignKey(StockMovement, on_delete=models.PROTECT, related_name="ledger_entries")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="ledger_entries")
    location = models.ForeignKey(StorageLocation, on_delete=models.PROTECT, related_name="ledger_entries")
    quantity_base = models.DecimalField("التغير بالوحدة الأساسية", max_digits=30, decimal_places=12)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        indexes = [models.Index(fields=["product", "location"])]
        constraints = [models.CheckConstraint(condition=~models.Q(quantity_base=0), name="ledger_quantity_nonzero")]

    def save(self, *args, **kwargs):
        if self.pk and StockLedgerEntry.objects.filter(pk=self.pk).exists():
            raise ValidationError("قيد الأستاذ غير قابل للتعديل.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("قيد الأستاذ غير قابل للحذف.")


class InventoryLock(models.Model):
    """A serialization row only; all displayed stock is recalculated from ledger entries."""
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    location = models.ForeignKey(StorageLocation, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["product", "location"], name="unique_inventory_lock_pair")]


class LastPurchaseCost(models.Model):
    """Current last purchase cost per product (material).

    Updated atomically when a priced purchase receipt is successfully posted.
    Used by costing and waste valuation to fetch the latest applicable price.
    """
    product = models.OneToOneField(Product, on_delete=models.PROTECT, related_name="last_purchase_cost", verbose_name="الصنف")
    price_per_base_unit = models.DecimalField("آخر سعر شراء للوحدة الأساسية (ج.م)", max_digits=18, decimal_places=6)
    source_movement = models.ForeignKey(StockMovement, on_delete=models.PROTECT, related_name="last_purchase_cost_updates", verbose_name="حركة التوريد المرجعية")
    updated_at = models.DateTimeField("وقت التحديث", auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="last_purchase_cost_updates", verbose_name="حدّث بواسطة")

    class Meta:
        verbose_name = "آخر سعر شراء"
        verbose_name_plural = "آخر أسعار الشراء"

    def __str__(self):
        return f"{self.product.sku}: {self.price_per_base_unit} ج.م / {self.product.base_uom.code}"


class LastPurchaseCostHistory(models.Model):
    """Append-only history of last purchase cost changes for as-of valuation.

    Each change (receipt, reversal) appends a row. Used for point-in-time
    queries: latest row with effective_at <= target date.
    """
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="last_purchase_cost_history", verbose_name="الصنف")
    price_per_base_unit = models.DecimalField(
        "سعر الوحدة الأساسية (ج.م)",
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
        help_text=(
            "null تعني أنه لا يوجد سعر شراء صالح بعد عكس آخر استلام (وليس سعرًا "
            "صفرًا)؛ تُقرأ كنقص تسعير لا كتكلفة مجانية."
        ),
    )
    effective_at = models.DateTimeField("وقت السريان")
    source_movement = models.ForeignKey(StockMovement, on_delete=models.PROTECT, related_name="last_purchase_cost_history", verbose_name="حركة التوريد")
    change_reason = models.CharField("سبب التغيير", max_length=120, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["effective_at", "pk"]
        indexes = [models.Index(fields=["product", "effective_at"])]
        verbose_name = "تاريخ آخر سعر شراء"
        verbose_name_plural = "تاريخ آخر أسعار الشراء"

    def __str__(self):
        return f"{self.product.sku} @ {self.effective_at:%Y-%m-%d %H:%M}: {self.price_per_base_unit}"