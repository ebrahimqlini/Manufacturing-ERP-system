from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q, Sum

from catalog.models import Product, UnitOfMeasure
from inventory.models import StockMovement, StorageLocation


CENT = Decimal("0.01")
SIX_PLACES = Decimal("0.000001")


class Supplier(models.Model):
    """Shared supplier master data. No payment, tax, or bank details are stored."""

    code = models.CharField("رمز المورد", max_length=40, unique=True)
    name_ar = models.CharField("اسم المورد بالعربية", max_length=180)
    name_en = models.CharField("اسم المورد بالإنجليزية", max_length=180, blank=True)
    contact_person = models.CharField("مسؤول التواصل", max_length=120, blank=True)
    phone = models.CharField("الهاتف", max_length=40, blank=True)
    email = models.EmailField("البريد الإلكتروني", blank=True)
    address = models.TextField("العنوان", blank=True)
    notes = models.TextField("ملاحظات", blank=True)
    active = models.BooleanField("نشط", default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_suppliers",
        verbose_name="أنشئ بواسطة",
    )
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(condition=~Q(code=""), name="supplier_code_not_empty"),
            models.CheckConstraint(condition=~Q(name_ar=""), name="supplier_ar_name_not_empty"),
        ]
        verbose_name = "مورد"
        verbose_name_plural = "الموردون"

    def clean(self):
        self.code = (self.code or "").strip().upper()
        self.name_ar = (self.name_ar or "").strip()
        errors = {}
        if not self.code:
            errors["code"] = "رمز المورد مطلوب."
        if not self.name_ar:
            errors["name_ar"] = "اسم المورد بالعربية مطلوب."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.code} — {self.name_ar}"


class PurchaseOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "مسودة"
        CONFIRMED = "CONFIRMED", "مؤكد"
        CANCELLED = "CANCELLED", "ملغي"

    class ReceiptState(models.TextChoices):
        NOT_RECEIVED = "NOT_RECEIVED", "لم يُستلم"
        PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED", "مستلم جزئيًا"
        RECEIVED = "RECEIVED", "مستلم بالكامل"

    order_number = models.CharField("رقم أمر الشراء", max_length=40, unique=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders", verbose_name="المورد")
    order_date = models.DateField("تاريخ الأمر", default=date.today)
    expected_date = models.DateField("تاريخ التوريد المتوقع", null=True, blank=True)
    supplier_reference = models.CharField("مرجع المورد", max_length=120, blank=True)
    status = models.CharField("الحالة", max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    notes = models.TextField("ملاحظات", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_purchase_orders",
        verbose_name="أنشئ بواسطة",
    )
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)
    confirmed_at = models.DateTimeField("تاريخ التأكيد", null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="confirmed_purchase_orders",
        verbose_name="أكد بواسطة",
    )
    cancelled_at = models.DateTimeField("تاريخ الإلغاء", null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cancelled_purchase_orders",
        verbose_name="ألغى بواسطة",
    )
    cancel_reason = models.CharField("سبب الإلغاء", max_length=240, blank=True)

    class Meta:
        ordering = ["-order_date", "-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(expected_date__isnull=True) | Q(expected_date__gte=F("order_date")),
                name="purchase_order_expected_not_before_order",
            ),
        ]
        verbose_name = "أمر شراء"
        verbose_name_plural = "أوامر الشراء"
        permissions = [
            ("confirm_purchase_order", "Can confirm purchase orders"),
            ("cancel_purchase_order", "Can cancel purchase orders"),
        ]

    def __str__(self):
        return self.order_number

    def clean(self):
        errors = {}
        if self.supplier_id and not self.pk and not self.supplier.active:
            errors["supplier"] = "اختر موردًا نشطًا لأمر شراء جديد."
        if self.order_date and self.expected_date and self.expected_date < self.order_date:
            errors["expected_date"] = "تاريخ التوريد المتوقع لا يسبق تاريخ الأمر."
        if errors:
            raise ValidationError(errors)

    @property
    def ordered_quantity_base(self):
        return self.lines.aggregate(total=Sum("quantity_base"))["total"] or Decimal("0")

    @property
    def received_quantity_base(self):
        return (
            PurchaseReceiptLine.objects.filter(
                receipt__order=self, movement__reversal__isnull=True
            ).aggregate(total=Sum("quantity_base"))["total"]
            or Decimal("0")
        )

    @property
    def outstanding_quantity_base(self):
        return self.ordered_quantity_base - self.received_quantity_base

    @property
    def ordered_value(self):
        return self.lines.aggregate(total=Sum("line_total"))["total"] or Decimal("0.00")

    @property
    def received_value(self):
        return (
            PurchaseReceiptLine.objects.filter(
                receipt__order=self, movement__reversal__isnull=True
            ).aggregate(total=Sum("line_total"))["total"]
            or Decimal("0.00")
        )

    @property
    def receipt_state(self):
        if not self.lines.exists():
            return self.ReceiptState.NOT_RECEIVED
        received = self.received_quantity_base
        if received <= 0:
            return self.ReceiptState.NOT_RECEIVED
        if received < self.ordered_quantity_base:
            return self.ReceiptState.PARTIALLY_RECEIVED
        return self.ReceiptState.RECEIVED

    @property
    def receipt_state_label(self):
        return dict(self.ReceiptState.choices)[self.receipt_state]

    @property
    def can_receive(self):
        return self.status == self.Status.CONFIRMED and self.receipt_state != self.ReceiptState.RECEIVED

    @property
    def can_cancel(self):
        return self.status in (self.Status.DRAFT, self.Status.CONFIRMED) and not self.receipts.exists()


class PurchaseOrderLine(models.Model):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="lines", verbose_name="أمر الشراء")
    material = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="purchase_order_lines", verbose_name="المادة/الصنف")
    quantity = models.DecimalField("الكمية المطلوبة", max_digits=30, decimal_places=12, validators=[MinValueValidator(Decimal("0.000000000001"))])
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="purchase_order_lines", verbose_name="وحدة الكمية")
    quantity_base = models.DecimalField("الكمية بالوحدة الأساسية", max_digits=30, decimal_places=12, default=0, editable=False)
    unit_price = models.DecimalField("سعر الوحدة (ج.م)", max_digits=18, decimal_places=4, validators=[MinValueValidator(Decimal("0"))])
    price_per_base_unit = models.DecimalField("السعر للوحدة الأساسية (ج.م)", max_digits=18, decimal_places=6, default=0, editable=False)
    line_total = models.DecimalField("إجمالي البند (ج.م)", max_digits=18, decimal_places=2, default=0, editable=False)
    sequence = models.PositiveSmallIntegerField("الترتيب", default=10)
    notes = models.CharField("ملاحظات", max_length=240, blank=True)

    class Meta:
        ordering = ["sequence", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["order", "sequence"], name="unique_purchase_order_line_sequence"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="purchase_order_line_quantity_positive"),
            models.CheckConstraint(condition=Q(quantity_base__gt=0), name="purchase_order_line_base_quantity_positive"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="purchase_order_line_unit_price_nonnegative"),
            models.CheckConstraint(condition=Q(price_per_base_unit__gte=0), name="purchase_order_line_base_price_nonnegative"),
            models.CheckConstraint(condition=Q(line_total__gte=0), name="purchase_order_line_total_nonnegative"),
        ]
        verbose_name = "بند أمر شراء"
        verbose_name_plural = "بنود أوامر الشراء"

    def clean(self):
        errors = {}
        if self.order_id and PurchaseOrder.objects.filter(pk=self.order_id).exclude(
            status=PurchaseOrder.Status.DRAFT
        ).exists():
            errors["order"] = "لا تتغير بنود أمر الشراء بعد تأكيده."
        if self.quantity is not None and self.quantity <= 0:
            errors["quantity"] = "الكمية يجب أن تكون أكبر من صفر."
        if self.unit_price is not None and self.unit_price < 0:
            errors["unit_price"] = "سعر الوحدة لا يكون سالبًا."
        if self.material_id and not self.material.active:
            errors["material"] = "اختر مادة/صنفًا نشطًا."
        if self.unit_id and not self.unit.active:
            errors["unit"] = "اختر وحدة نشطة."
        if self.material_id and self.unit_id and self.quantity is not None and self.unit_price is not None:
            from inventory.services import to_base_quantity

            try:
                base_factor = to_base_quantity(self.material, Decimal("1"), self.unit)
                self.quantity_base = to_base_quantity(self.material, self.quantity, self.unit)
            except ValidationError as exc:
                errors["unit"] = exc.messages
            else:
                if base_factor <= 0:
                    errors["unit"] = "عامل التحويل غير صالح."
                else:
                    with localcontext() as context:
                        context.prec = 80
                        self.price_per_base_unit = (self.unit_price / base_factor).quantize(
                            SIX_PLACES, rounding=ROUND_HALF_UP
                        )
                        self.line_total = (self.quantity * self.unit_price).quantize(
                            CENT, rounding=ROUND_HALF_UP
                        )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.pk and self.order_id and PurchaseOrder.objects.filter(pk=self.order_id).exclude(
            status=PurchaseOrder.Status.DRAFT
        ).exists():
            raise ValidationError("لا تحذف بنود أمر شراء بعد تأكيده.")
        return super().delete(*args, **kwargs)

    @property
    def received_quantity_base(self):
        return (
            self.receipt_lines.filter(movement__reversal__isnull=True).aggregate(total=Sum("quantity_base"))["total"]
            or Decimal("0")
        )

    @property
    def outstanding_quantity_base(self):
        return max(self.quantity_base - self.received_quantity_base, Decimal("0"))

    @property
    def is_fully_received(self):
        return self.received_quantity_base >= self.quantity_base

    def __str__(self):
        return f"{self.order.order_number} — {self.material.sku}"


class PurchaseReceipt(models.Model):
    """An immutable receiving document; corrections use inventory reversal."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="receipts", verbose_name="أمر الشراء")
    reference = models.CharField("مرجع الاستلام", max_length=100, unique=True)
    supplier_reference = models.CharField("مرجع إذن/فاتورة المورد", max_length=120, blank=True)
    received_date = models.DateField("تاريخ الاستلام", default=date.today)
    destination_location = models.ForeignKey(
        StorageLocation, on_delete=models.PROTECT, related_name="purchase_receipts", verbose_name="موقع الإيداع"
    )
    notes = models.TextField("ملاحظات", blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="purchase_receipts",
        verbose_name="سجل الاستلام",
    )
    created_at = models.DateTimeField("تاريخ التسجيل", auto_now_add=True)

    class Meta:
        ordering = ["-received_date", "-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=~Q(reference=""), name="purchase_receipt_reference_not_empty"),
        ]
        verbose_name = "استلام مشتريات"
        verbose_name_plural = "استلامات المشتريات"
        permissions = [
            ("create_receipt", "Can create purchase receipts"),
        ]

    def clean(self):
        self.reference = (self.reference or "").strip()
        self.supplier_reference = (self.supplier_reference or "").strip()
        errors = {}
        if not self.reference:
            errors["reference"] = "مرجع الاستلام مطلوب لمنع التكرار."
        if self.destination_location_id and (
            not self.destination_location.active or not self.destination_location.warehouse.active
        ):
            errors["destination_location"] = "اختر موقع تخزين نشطًا داخل مخزن نشط."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk and PurchaseReceipt.objects.filter(pk=self.pk).exists():
            raise ValidationError("استلامات المشتريات غير قابلة للتعديل؛ استخدم عكس حركة المخزون.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("استلامات المشتريات لا تُحذف؛ استخدم عكس حركة المخزون.")

    @property
    def total_value(self):
        return self.lines.aggregate(total=Sum("line_total"))["total"] or Decimal("0.00")

    @property
    def line_count(self):
        return self.lines.count()

    def __str__(self):
        return self.reference


class PurchaseReceiptLine(models.Model):
    receipt = models.ForeignKey(PurchaseReceipt, on_delete=models.CASCADE, related_name="lines", verbose_name="الاستلام")
    order_line = models.ForeignKey(
        PurchaseOrderLine, on_delete=models.PROTECT, related_name="receipt_lines", verbose_name="بند أمر الشراء"
    )
    movement = models.OneToOneField(
        StockMovement, on_delete=models.PROTECT, related_name="purchasing_receipt_line", verbose_name="حركة المخزون"
    )
    quantity = models.DecimalField("الكمية المستلمة", max_digits=30, decimal_places=12)
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="purchase_receipt_lines", verbose_name="وحدة الاستلام")
    quantity_base = models.DecimalField("الكمية بالوحدة الأساسية", max_digits=30, decimal_places=12)
    unit_price = models.DecimalField("سعر الوحدة المتفق عليه (ج.م)", max_digits=18, decimal_places=4)
    price_per_base_unit = models.DecimalField("السعر للوحدة الأساسية وقت الاستلام (ج.م)", max_digits=18, decimal_places=6)
    line_total = models.DecimalField("قيمة البند (ج.م)", max_digits=18, decimal_places=2)
    sequence = models.PositiveSmallIntegerField("الترتيب")

    class Meta:
        ordering = ["sequence", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["receipt", "order_line"], name="unique_purchase_receipt_order_line"),
            models.UniqueConstraint(fields=["receipt", "sequence"], name="unique_purchase_receipt_line_sequence"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="purchase_receipt_line_quantity_positive"),
            models.CheckConstraint(condition=Q(quantity_base__gt=0), name="purchase_receipt_line_base_quantity_positive"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="purchase_receipt_line_unit_price_nonnegative"),
            models.CheckConstraint(condition=Q(price_per_base_unit__gte=0), name="purchase_receipt_line_base_price_nonnegative"),
            models.CheckConstraint(condition=Q(line_total__gte=0), name="purchase_receipt_line_total_nonnegative"),
        ]
        verbose_name = "بند استلام مشتريات"
        verbose_name_plural = "بنود استلامات المشتريات"

    def clean(self):
        errors = {}
        if self.quantity is not None and self.quantity <= 0:
            errors["quantity"] = "الكمية المستلمة يجب أن تكون أكبر من صفر."
        if self.quantity_base is not None and self.quantity_base <= 0:
            errors["quantity_base"] = "الكمية بالوحدة الأساسية يجب أن تكون أكبر من صفر."
        if self.unit_price is not None and self.unit_price < 0:
            errors["unit_price"] = "سعر الوحدة لا يكون سالبًا."
        if self.price_per_base_unit is not None and self.price_per_base_unit < 0:
            errors["price_per_base_unit"] = "السعر للوحدة الأساسية لا يكون سالبًا."
        if self.line_total is not None and self.line_total < 0:
            errors["line_total"] = "قيمة البند لا تكون سالبة."
        if self.movement_id and self.order_line_id:
            if self.movement.product_id != self.order_line.material_id:
                errors["movement"] = "حركة المخزون لا تطابق مادة بند أمر الشراء."
            elif self.movement.quantity != self.quantity or self.movement.unit_id != self.unit_id:
                errors["movement"] = "حركة المخزون لا تطابق كمية ووحدة الاستلام."
            elif self.movement.base_quantity != self.quantity_base:
                errors["movement"] = "حركة المخزون لا تطابق الكمية بالوحدة الأساسية."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk and PurchaseReceiptLine.objects.filter(pk=self.pk).exists():
            raise ValidationError("بنود استلام المشتريات غير قابلة للتعديل.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("بنود استلام المشتريات لا تُحذف.")

    def __str__(self):
        return f"{self.receipt.reference} — {self.order_line.material.sku}"
