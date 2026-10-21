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


class Customer(models.Model):
    """Shared customer master data. No bank, tax, or payment details are stored."""

    code = models.CharField("رمز العميل", max_length=40, unique=True)
    name_ar = models.CharField("اسم العميل بالعربية", max_length=180)
    name_en = models.CharField("اسم العميل بالإنجليزية", max_length=180, blank=True)
    contact_person = models.CharField("مسؤول التواصل", max_length=120, blank=True)
    phone = models.CharField("الهاتف", max_length=40, blank=True)
    email = models.EmailField("البريد الإلكتروني", blank=True)
    address = models.TextField("العنوان", blank=True)
    notes = models.TextField("ملاحظات", blank=True)
    active = models.BooleanField("نشط", default=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="customer_account",
        verbose_name="مستخدم حساب العميل",
        help_text="حساب غير داخلي مرتبط بهذا العميل؛ لا يمنح صلاحية صفحات المبيعات الداخلية.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_customers",
        verbose_name="أنشئ بواسطة",
    )
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(condition=~Q(code=""), name="customer_code_not_empty"),
            models.CheckConstraint(condition=~Q(name_ar=""), name="customer_ar_name_not_empty"),
        ]
        verbose_name = "عميل"
        verbose_name_plural = "العملاء"

    def clean(self):
        self.code = (self.code or "").strip().upper()
        self.name_ar = (self.name_ar or "").strip()
        errors = {}
        if not self.code:
            errors["code"] = "رمز العميل مطلوب."
        if not self.name_ar:
            errors["name_ar"] = "اسم العميل بالعربية مطلوب."
        if self.user_id and self.user.is_staff:
            errors["user"] = "لا يمكن ربط حساب عميل بمستخدم داخلي (staff)."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.code} — {self.name_ar}"


class SalesOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "مسودة"
        CONFIRMED = "CONFIRMED", "مؤكد"
        COMPLETED = "COMPLETED", "مكتمل"
        CANCELLED = "CANCELLED", "ملغي"

    class ShipmentState(models.TextChoices):
        NOT_SHIPPED = "NOT_SHIPPED", "لم يُشحن"
        PARTIALLY_SHIPPED = "PARTIALLY_SHIPPED", "مشحون جزئيًا"
        SHIPPED = "SHIPPED", "مشحون بالكامل"

    order_number = models.CharField("رقم أمر البيع", max_length=40, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="sales_orders", verbose_name="العميل")
    order_date = models.DateField("تاريخ الأمر", default=date.today)
    requested_delivery_date = models.DateField("تاريخ التسليم المطلوب", null=True, blank=True)
    customer_reference = models.CharField("مرجع العميل", max_length=120, blank=True)
    status = models.CharField("الحالة", max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    notes = models.TextField("ملاحظات", blank=True)
    customer_submitted = models.BooleanField(
        "طلب عبر تطبيق العميل",
        default=False,
        editable=False,
        help_text="طلب مسودة أنشأه حساب عميل عبر واجهة API، وليس أمرًا مؤكدًا.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_sales_orders",
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
        related_name="confirmed_sales_orders",
        verbose_name="أكد بواسطة",
    )
    completed_at = models.DateTimeField("تاريخ الإكمال", null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="completed_sales_orders",
        verbose_name="أكمل بواسطة",
    )
    cancelled_at = models.DateTimeField("تاريخ الإلغاء", null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cancelled_sales_orders",
        verbose_name="ألغى بواسطة",
    )
    cancel_reason = models.CharField("سبب الإلغاء", max_length=240, blank=True)
    reopened_at = models.DateTimeField("تاريخ إعادة الفتح", null=True, blank=True)
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reopened_sales_orders",
        verbose_name="أعاد فتحه بواسطة",
    )
    reopen_reason = models.CharField("سبب إعادة الفتح", max_length=240, blank=True)

    class Meta:
        ordering = ["-order_date", "-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(requested_delivery_date__isnull=True) | Q(requested_delivery_date__gte=F("order_date")),
                name="sales_order_requested_not_before_order",
            ),
        ]
        verbose_name = "أمر بيع"
        verbose_name_plural = "أوامر البيع"

    def __str__(self):
        return self.order_number

    def clean(self):
        errors = {}
        if self.customer_id and not self.pk and not self.customer.active:
            errors["customer"] = "اختر عميلًا نشطًا لأمر بيع جديد."
        if self.order_date and self.requested_delivery_date and self.requested_delivery_date < self.order_date:
            errors["requested_delivery_date"] = "تاريخ التسليم المطلوب لا يسبق تاريخ الأمر."
        if errors:
            raise ValidationError(errors)

    @property
    def ordered_quantity_base(self):
        return self.lines.aggregate(total=Sum("quantity_base"))["total"] or Decimal("0")

    @property
    def shipped_quantity_base(self):
        return (
            ShipmentLine.objects.filter(
                shipment__order=self, movement__reversal__isnull=True
            ).aggregate(total=Sum("quantity_base"))["total"]
            or Decimal("0")
        )

    @property
    def outstanding_quantity_base(self):
        return max(self.ordered_quantity_base - self.shipped_quantity_base, Decimal("0"))

    @property
    def ordered_value(self):
        return self.lines.aggregate(total=Sum("line_total"))["total"] or Decimal("0.00")

    @property
    def has_unpriced_lines(self):
        """Whether any line still has the zero price used by a customer request."""
        return any(line.unit_price <= 0 for line in self.lines.all())

    @property
    def pricing_pending(self):
        """A customer-submitted order that still needs explicit staff pricing."""
        return bool(self.customer_submitted and self.has_unpriced_lines)

    @property
    def pricing_status(self):
        """Customer-visible pricing state, kept separate from the order status.

        A positive price on every line is the explicit review signal. A legacy
        customer-submitted order that was confirmed while unpriced is reported
        as ``UNPRICED`` rather than pretending it is still pending review.
        """
        if not self.customer_submitted:
            return "RECORDED"
        if self.pricing_pending:
            return "PENDING_REVIEW" if self.status == self.Status.DRAFT else "UNPRICED"
        return "RECORDED"

    @property
    def shipped_value(self):
        return (
            ShipmentLine.objects.filter(
                shipment__order=self, movement__reversal__isnull=True
            ).aggregate(total=Sum("line_total"))["total"]
            or Decimal("0.00")
        )

    @property
    def shipment_state(self):
        if not self.lines.exists():
            return self.ShipmentState.NOT_SHIPPED
        shipped = self.shipped_quantity_base
        if shipped <= 0:
            return self.ShipmentState.NOT_SHIPPED
        if shipped < self.ordered_quantity_base:
            return self.ShipmentState.PARTIALLY_SHIPPED
        return self.ShipmentState.SHIPPED

    @property
    def shipment_state_label(self):
        return dict(self.ShipmentState.choices)[self.shipment_state]

    @property
    def can_ship(self):
        return (
            self.status == self.Status.CONFIRMED
            and not self.pricing_pending
            and self.shipment_state != self.ShipmentState.SHIPPED
        )

    @property
    def can_complete(self):
        return self.status == self.Status.CONFIRMED and self.shipment_state == self.ShipmentState.SHIPPED

    @property
    def can_cancel(self):
        return self.status in (self.Status.DRAFT, self.Status.CONFIRMED) and not self.shipments.exists()

    @property
    def can_reopen(self):
        return self.status == self.Status.COMPLETED and self.outstanding_quantity_base > 0


class SalesOrderLine(models.Model):
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name="lines", verbose_name="أمر البيع")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="sales_order_lines", verbose_name="الصنف")
    quantity = models.DecimalField("الكمية المطلوبة", max_digits=30, decimal_places=12, validators=[MinValueValidator(Decimal("0.000000000001"))])
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="sales_order_lines", verbose_name="وحدة البيع")
    quantity_base = models.DecimalField("الكمية بالوحدة الأساسية", max_digits=30, decimal_places=12, default=0, editable=False)
    unit_price = models.DecimalField("سعر البيع للوحدة (ج.م)", max_digits=18, decimal_places=4, validators=[MinValueValidator(Decimal("0"))])
    price_per_base_unit = models.DecimalField("السعر للوحدة الأساسية (ج.م)", max_digits=18, decimal_places=6, default=0, editable=False)
    line_total = models.DecimalField("إجمالي البند (ج.م)", max_digits=18, decimal_places=2, default=0, editable=False)
    sequence = models.PositiveSmallIntegerField("الترتيب", default=10)
    notes = models.CharField("ملاحظات", max_length=240, blank=True)

    class Meta:
        ordering = ["sequence", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["order", "sequence"], name="unique_sales_order_line_sequence"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="sales_order_line_quantity_positive"),
            models.CheckConstraint(condition=Q(quantity_base__gt=0), name="sales_order_line_base_quantity_positive"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="sales_order_line_unit_price_nonnegative"),
            models.CheckConstraint(condition=Q(price_per_base_unit__gte=0), name="sales_order_line_base_price_nonnegative"),
            models.CheckConstraint(condition=Q(line_total__gte=0), name="sales_order_line_total_nonnegative"),
        ]
        verbose_name = "بند أمر بيع"
        verbose_name_plural = "بنود أوامر البيع"

    def clean(self):
        errors = {}
        if self.order_id and SalesOrder.objects.filter(pk=self.order_id).exclude(
            status=SalesOrder.Status.DRAFT
        ).exists():
            errors["order"] = "لا تتغير بنود أمر البيع بعد تأكيده."
        if self.quantity is not None and self.quantity <= 0:
            errors["quantity"] = "الكمية يجب أن تكون أكبر من صفر."
        if self.unit_price is not None and self.unit_price < 0:
            errors["unit_price"] = "سعر البيع لا يكون سالبًا."
        if self.product_id and not self.product.active:
            errors["product"] = "اختر صنفًا نشطًا."
        if self.unit_id and not self.unit.active:
            errors["unit"] = "اختر وحدة نشطة."
        if self.product_id and self.unit_id and self.quantity is not None and self.unit_price is not None:
            from inventory.services import to_base_quantity

            try:
                base_factor = to_base_quantity(self.product, Decimal("1"), self.unit)
                self.quantity_base = to_base_quantity(self.product, self.quantity, self.unit)
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
        if self.pk and self.order_id and SalesOrder.objects.filter(pk=self.order_id).exclude(
            status=SalesOrder.Status.DRAFT
        ).exists():
            raise ValidationError("لا تحذف بنود أمر بيع بعد تأكيده.")
        return super().delete(*args, **kwargs)

    @property
    def shipped_quantity_base(self):
        return (
            self.shipment_lines.filter(movement__reversal__isnull=True).aggregate(total=Sum("quantity_base"))["total"]
            or Decimal("0")
        )

    @property
    def outstanding_quantity_base(self):
        return max(self.quantity_base - self.shipped_quantity_base, Decimal("0"))

    @property
    def is_fully_shipped(self):
        return self.shipped_quantity_base >= self.quantity_base

    def __str__(self):
        return f"{self.order.order_number} — {self.product.sku}"


class Shipment(models.Model):
    """An immutable shipment/fulfillment document; corrections use inventory reversal."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name="shipments", verbose_name="أمر البيع")
    reference = models.CharField("مرجع الشحن", max_length=100, unique=True)
    customer_reference = models.CharField("مرجع إذن/فاتورة العميل", max_length=120, blank=True)
    shipment_date = models.DateField("تاريخ الشحن", default=date.today)
    source_location = models.ForeignKey(
        StorageLocation, on_delete=models.PROTECT, related_name="sales_shipments", verbose_name="موقع الصرف"
    )
    notes = models.TextField("ملاحظات", blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sales_shipments",
        verbose_name="سجل الشحن",
    )
    created_at = models.DateTimeField("تاريخ التسجيل", auto_now_add=True)

    class Meta:
        ordering = ["-shipment_date", "-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=~Q(reference=""), name="shipment_reference_not_empty"),
        ]
        verbose_name = "شحن مبيعات"
        verbose_name_plural = "شحنات المبيعات"

    def clean(self):
        self.reference = (self.reference or "").strip()
        self.customer_reference = (self.customer_reference or "").strip()
        errors = {}
        if not self.reference:
            errors["reference"] = "مرجع الشحن مطلوب لمنع التكرار."
        if self.source_location_id and (
            not self.source_location.active or not self.source_location.warehouse.active
        ):
            errors["source_location"] = "اختر موقع مخزون نشطًا داخل مخزن نشط."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk and Shipment.objects.filter(pk=self.pk).exists():
            raise ValidationError("شحنات المبيعات غير قابلة للتعديل؛ استخدم عكس حركة المخزون.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("شحنات المبيعات لا تُحذف؛ استخدم عكس حركة المخزون.")

    @property
    def total_value(self):
        """Original document value; reversed lines remain part of the audit document."""
        return self.lines.aggregate(total=Sum("line_total"))["total"] or Decimal("0.00")

    @property
    def net_value(self):
        """Value of non-reversed lines; this is what contributes to the order's shipped value."""
        return (
            self.lines.filter(movement__reversal__isnull=True).aggregate(total=Sum("line_total"))["total"]
            or Decimal("0.00")
        )

    @property
    def reversed_line_count(self):
        return self.lines.filter(movement__reversal__isnull=False).count()

    @property
    def has_reversed_lines(self):
        return self.reversed_line_count > 0

    @property
    def is_fully_reversed(self):
        total_lines = self.lines.count()
        return total_lines > 0 and self.reversed_line_count == total_lines

    @property
    def line_count(self):
        return self.lines.count()

    def __str__(self):
        return self.reference


class ShipmentLine(models.Model):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="lines", verbose_name="الشحن")
    order_line = models.ForeignKey(
        SalesOrderLine, on_delete=models.PROTECT, related_name="shipment_lines", verbose_name="بند أمر البيع"
    )
    movement = models.OneToOneField(
        StockMovement, on_delete=models.PROTECT, related_name="sales_shipment_line", verbose_name="حركة المخزون"
    )
    quantity = models.DecimalField("الكمية المشحونة", max_digits=30, decimal_places=12)
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="sales_shipment_lines", verbose_name="وحدة الشحن")
    quantity_base = models.DecimalField("الكمية بالوحدة الأساسية", max_digits=30, decimal_places=12)
    unit_price = models.DecimalField("سعر وحدة أمر البيع المتفق عليه (ج.م)", max_digits=18, decimal_places=4)
    price_per_base_unit = models.DecimalField("السعر للوحدة الأساسية وقت الشحن (ج.م)", max_digits=18, decimal_places=6)
    line_total = models.DecimalField("قيمة البند (ج.م)", max_digits=18, decimal_places=2)
    sequence = models.PositiveSmallIntegerField("الترتيب")

    class Meta:
        ordering = ["sequence", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["shipment", "order_line"], name="unique_shipment_order_line"),
            models.UniqueConstraint(fields=["shipment", "sequence"], name="unique_shipment_line_sequence"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="shipment_line_quantity_positive"),
            models.CheckConstraint(condition=Q(quantity_base__gt=0), name="shipment_line_base_quantity_positive"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="shipment_line_unit_price_nonnegative"),
            models.CheckConstraint(condition=Q(price_per_base_unit__gte=0), name="shipment_line_base_price_nonnegative"),
            models.CheckConstraint(condition=Q(line_total__gte=0), name="shipment_line_total_nonnegative"),
        ]
        verbose_name = "بند شحن مبيعات"
        verbose_name_plural = "بنود شحنات المبيعات"

    def clean(self):
        errors = {}
        if self.quantity is not None and self.quantity <= 0:
            errors["quantity"] = "الكمية المشحونة يجب أن تكون أكبر من صفر."
        if self.quantity_base is not None and self.quantity_base <= 0:
            errors["quantity_base"] = "الكمية بالوحدة الأساسية يجب أن تكون أكبر من صفر."
        if self.unit_price is not None and self.unit_price < 0:
            errors["unit_price"] = "سعر البيع لا يكون سالبًا."
        if self.price_per_base_unit is not None and self.price_per_base_unit < 0:
            errors["price_per_base_unit"] = "السعر للوحدة الأساسية لا يكون سالبًا."
        if self.line_total is not None and self.line_total < 0:
            errors["line_total"] = "قيمة البند لا تكون سالبة."
        if (
            self.quantity_base is not None
            and self.price_per_base_unit is not None
            and self.line_total is not None
        ):
            with localcontext() as context:
                context.prec = 80
                expected_total = (self.quantity_base * self.price_per_base_unit).quantize(
                    CENT, rounding=ROUND_HALF_UP
                )
            if self.line_total != expected_total:
                errors["line_total"] = "قيمة البند لا تطابق الكمية بالوحدة الأساسية والسعر للوحدة الأساسية."
        if self.movement_id and self.order_line_id:
            if self.movement.product_id != self.order_line.product_id:
                errors["movement"] = "حركة المخزون لا تطابق صنف بند أمر البيع."
            elif self.movement.quantity != self.quantity or self.movement.unit_id != self.unit_id:
                errors["movement"] = "حركة المخزون لا تطابق كمية ووحدة الشحن."
            elif self.movement.base_quantity != self.quantity_base:
                errors["movement"] = "حركة المخزون لا تطابق الكمية بالوحدة الأساسية."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk and ShipmentLine.objects.filter(pk=self.pk).exists():
            raise ValidationError("بنود شحنات المبيعات غير قابلة للتعديل.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("بنود شحنات المبيعات لا تُحذف.")

    def __str__(self):
        return f"{self.shipment.reference} — {self.order_line.product.sku}"
