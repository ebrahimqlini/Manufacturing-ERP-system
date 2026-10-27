import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone

from catalog.models import Product, UnitOfMeasure
from inventory.models import StockMovement, StorageLocation


class ProtectedConfigurationQuerySet(models.QuerySet):
    """Require config writes to go through row-locked model saves."""

    def update(self, **kwargs):
        raise ValidationError("عدّل إعداد التصنيع عبر النموذج حتى تُحفظ أقفال الإعدادات.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("التعديل الجماعي لإعداد التصنيع غير مسموح.")

    def bulk_create(self, objs, **kwargs):
        objs = list(objs)
        model = self.model
        parent_field = "bom_id" if model is BOMComponent else "routing_id" if model is RoutingStep else None
        if parent_field:
            parent_model = BillOfMaterials if model is BOMComponent else Routing
            parents = _locked_config_rows(parent_model, [getattr(obj, parent_field) for obj in objs])
            for parent in parents:
                _ensure_config_unused(parent, "إعداد التصنيع")
        return super().bulk_create(objs, **kwargs)

    def delete(self):
        model = self.model
        parent_field = "bom_id" if model is BOMComponent else "routing_id" if model is RoutingStep else None
        if not parent_field:
            raise ValidationError("احذف إعداد التصنيع عبر النموذج حتى تُحفظ أقفال الإعدادات.")
        parent_model = BillOfMaterials if model is BOMComponent else Routing
        parent_ids = list(self.values_list(parent_field, flat=True).distinct())
        with transaction.atomic():
            parents = _locked_config_rows(parent_model, parent_ids)
            for parent in parents:
                _ensure_config_unused(parent, "إعداد التصنيع")
            return super().delete()


ProtectedConfigurationManager = models.Manager.from_queryset(ProtectedConfigurationQuerySet)


class PostedManufacturingQuerySet(models.QuerySet):
    """Reject ORM edits/deletes on posted manufacturing financial documents.

    A document is "posted" once it has been confirmed through the service layer
    (e.g., material issued, production recorded, goods received). Draft orders
    and their snapshots remain editable through supported workflows only.
    """

    def _assert_unposted(self, action_label):
        # Subclasses must implement _is_posted_filter to identify posted rows
        if self.filter(self._is_posted_filter()).exists():
            raise ValidationError(
                f"لا يمكن {action_label} لمستندات تصنيع مرحلة؛ استخدم خدمة العكس أو التصحيح المعتمدة."
            )

    def update(self, **kwargs):
        self._assert_unposted("تعديل")
        return super().update(**kwargs)

    def delete(self):
        self._assert_unposted("حذف")
        return super().delete()

    def bulk_update(self, objs, fields, batch_size=None):
        self._assert_unposted("تعديل جماعي")
        return super().bulk_update(objs, fields, batch_size)

    def _is_posted_filter(self):
        """Override in subclasses to define 'posted' condition."""
        raise NotImplementedError


class MaterialIssueQuerySet(PostedManufacturingQuerySet):
    def _is_posted_filter(self):
        # Every recorded issue is posted. Reversal is tracked on the linked
        # StockMovement (movement.reversal), never by editing the document row,
        # so an active issued document is just as immutable as a reversed one.
        return models.Q(pk__isnull=False)


class FinishedGoodsReceiptQuerySet(PostedManufacturingQuerySet):
    def _is_posted_filter(self):
        # Every recorded receipt is posted; reversal state lives on the movement.
        return models.Q(pk__isnull=False)


class ProductionResultQuerySet(PostedManufacturingQuerySet):
    def _is_posted_filter(self):
        # All recorded results are posted; they are never edited, only reversed via stock movement
        return models.Q(pk__isnull=False)


class OperationProgressQuerySet(PostedManufacturingQuerySet):
    def _is_posted_filter(self):
        # All recorded progress entries are posted
        return models.Q(pk__isnull=False)


class ProductionOrderQuerySet(PostedManufacturingQuerySet):
    def _is_posted_filter(self):
        # Posted when released (no longer DRAFT)
        return ~models.Q(status=ProductionOrder.Status.DRAFT)


class ProductionMaterialRequirementQuerySet(PostedManufacturingQuerySet):
    def _is_posted_filter(self):
        # Requirements are snapshots created at release; immutable once order is released
        return ~models.Q(order__status=ProductionOrder.Status.DRAFT)


class ProductionOrderOperationQuerySet(PostedManufacturingQuerySet):
    def _is_posted_filter(self):
        return ~models.Q(order__status=ProductionOrder.Status.DRAFT)


MaterialIssueManager = models.Manager.from_queryset(MaterialIssueQuerySet)
FinishedGoodsReceiptManager = models.Manager.from_queryset(FinishedGoodsReceiptQuerySet)
ProductionResultManager = models.Manager.from_queryset(ProductionResultQuerySet)
OperationProgressManager = models.Manager.from_queryset(OperationProgressQuerySet)
ProductionOrderManager = models.Manager.from_queryset(ProductionOrderQuerySet)
ProductionMaterialRequirementManager = models.Manager.from_queryset(ProductionMaterialRequirementQuerySet)
ProductionOrderOperationManager = models.Manager.from_queryset(ProductionOrderOperationQuerySet)


class ImmutableProductionRecordQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("مستند التصنيع المرحّل غير قابل للتعديل؛ استخدم خدمة التصحيح المعتمدة.")

    def delete(self):
        raise ValidationError("مستند التصنيع المرحّل لا يُحذف؛ استخدم خدمة العكس المعتمدة.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("التعديل الجماعي لمستند التصنيع المرحّل غير مسموح.")


ImmutableProductionRecordManager = models.Manager.from_queryset(ImmutableProductionRecordQuerySet)


def _locked_config_rows(model, ids):
    return list(model.objects.select_for_update(of=("self",)).filter(pk__in=sorted(set(ids))).order_by("pk"))


def _ensure_config_unused(config, label):
    if config.orders.exclude(status=ProductionOrder.Status.DRAFT).exists():
        raise ValidationError(f"لا يمكن تعديل {label} بعد إطلاق أمر يستخدمه؛ أنشئ إصدارًا جديدًا.")


class ProductionLine(models.Model):
    class CounterBehavior(models.TextChoices):
        UNCONFIRMED = "UNCONFIRMED", "غير مؤكد"
        COUNTS_ALL_OUTPUT = "COUNTS_ALL_OUTPUT", "يعد جميع المنتوج (الجيد والهالك)"
        COUNTS_GOOD_ONLY = "COUNTS_GOOD_ONLY", "يعد المنتوج الجيد فقط"

    code = models.SlugField("رمز خط الإنتاج", max_length=50, unique=True)
    name_ar = models.CharField("الاسم بالعربية", max_length=120)
    name_en = models.CharField("الاسم بالإنجليزية", max_length=120, blank=True)
    active = models.BooleanField("نشط", default=True)
    counter_behavior = models.CharField(
        "سلوك عدّاد الآلة",
        max_length=20,
        choices=CounterBehavior.choices,
        default=CounterBehavior.UNCONFIRMED,
        help_text=(
            "ما الذي يعده عدّاد الآلة؛ يبقى «غير مؤكد» حتى يؤكد المالك السلوك، "
            "وتُعرض قراءات العدّاد غير المؤكدة كقياس أولي لا كإنتاج جيد موثّق."
        ),
    )

    class Meta:
        ordering = ["code"]
        verbose_name = "خط إنتاج"
        verbose_name_plural = "خطوط الإنتاج"

    def __str__(self):
        return f"{self.code} — {self.name_ar}"


class ProductionOperation(models.Model):
    code = models.SlugField("رمز العملية", max_length=50, unique=True)
    name_ar = models.CharField("اسم العملية بالعربية", max_length=120)
    name_en = models.CharField("اسم العملية بالإنجليزية", max_length=120, blank=True)
    active = models.BooleanField("نشطة", default=True)

    class Meta:
        ordering = ["name_ar", "code"]
        verbose_name = "عملية إنتاج"
        verbose_name_plural = "عمليات الإنتاج"

    def __str__(self):
        return self.name_ar


class BillOfMaterials(models.Model):
    objects = ProtectedConfigurationManager()
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="bills_of_material")
    version = models.PositiveIntegerField("الإصدار")
    output_quantity = models.DecimalField("كمية المنتج الناتج", max_digits=30, decimal_places=12)
    output_unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="bom_outputs")
    active = models.BooleanField("نشطة", default=True)
    notes = models.TextField("ملاحظات", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_boms")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["product__sku", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["product", "version"], name="unique_bom_product_version"),
            models.CheckConstraint(condition=Q(version__gte=1), name="bom_version_positive"),
            models.CheckConstraint(condition=Q(output_quantity__gt=0), name="bom_output_quantity_positive"),
        ]
        verbose_name = "قائمة مواد"
        verbose_name_plural = "قوائم المواد"

    def __str__(self):
        return f"{self.product.sku} — BOM v{self.version}"

    def clean(self):
        if self.product_id and not self.product.active:
            raise ValidationError({"product": "اختر منتجًا نشطًا."})
        if self.pk and self.orders.exclude(status=ProductionOrder.Status.DRAFT).exists():
            raise ValidationError("قائمة المواد المستخدمة في أمر مطلق أو منفذ لا تتغير؛ أنشئ إصدارًا جديدًا.")
        if self.product_id and self.output_unit_id:
            from inventory.services import to_base_quantity
            try:
                to_base_quantity(self.product, self.output_quantity, self.output_unit)
            except ValidationError as exc:
                raise ValidationError({"output_unit": exc.messages}) from exc

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.pk:
                locked = _locked_config_rows(type(self), [self.pk])
                if locked:
                    _ensure_config_unused(locked[0], "قائمة المواد")
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            locked = _locked_config_rows(type(self), [self.pk])
            if locked:
                _ensure_config_unused(locked[0], "قائمة المواد")
            return super().delete(*args, **kwargs)


class BOMComponent(models.Model):
    objects = ProtectedConfigurationManager()
    bom = models.ForeignKey(BillOfMaterials, on_delete=models.PROTECT, related_name="components")
    material = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="bom_usages")
    quantity = models.DecimalField("كمية المكوّن لأساس القائمة", max_digits=30, decimal_places=12)
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="bom_component_lines")
    sequence = models.PositiveSmallIntegerField("الترتيب", default=10)
    notes = models.CharField("ملاحظات", max_length=240, blank=True)

    class Meta:
        ordering = ["sequence", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["bom", "sequence"], name="unique_bom_component_sequence"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="bom_component_quantity_positive"),
        ]
        verbose_name = "مكوّن قائمة المواد"
        verbose_name_plural = "مكوّنات قائمة المواد"

    def clean(self):
        if self.bom_id and self.bom.orders.exclude(status=ProductionOrder.Status.DRAFT).exists():
            raise ValidationError("مكونات قائمة مستخدمة في أمر مطلق أو منفذ لا تتغير؛ أنشئ إصدارًا جديدًا.")
        if self.material_id and self.bom_id and self.material_id == self.bom.product_id:
            raise ValidationError({"material": "لا يمكن استخدام المنتج الناتج كمكوّن لنفس قائمة المواد."})
        if self.material_id and not self.material.active:
            raise ValidationError({"material": "اختر مكوّنًا نشطًا."})
        if self.material_id and self.unit_id:
            from inventory.services import to_base_quantity
            try:
                to_base_quantity(self.material, self.quantity, self.unit)
            except ValidationError as exc:
                raise ValidationError({"unit": exc.messages}) from exc

    def _lock_parent_boms(self):
        ids = {self.bom_id} if self.bom_id else set()
        if self.pk:
            old = type(self).objects.filter(pk=self.pk).values_list("bom_id", flat=True).first()
            if old:
                ids.add(old)
        parents = _locked_config_rows(BillOfMaterials, ids)
        for parent in parents:
            _ensure_config_unused(parent, "قائمة المواد")

    def save(self, *args, **kwargs):
        with transaction.atomic():
            self._lock_parent_boms()
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            self._lock_parent_boms()
            return super().delete(*args, **kwargs)


class Routing(models.Model):
    objects = ProtectedConfigurationManager()
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="production_routings")
    production_line = models.ForeignKey(ProductionLine, on_delete=models.PROTECT, related_name="routings")
    version = models.PositiveIntegerField("الإصدار")
    active = models.BooleanField("نشط", default=True)
    notes = models.TextField("ملاحظات", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_routings")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["product__sku", "production_line__code", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["product", "production_line", "version"], name="unique_route_product_line_version"),
            models.CheckConstraint(condition=Q(version__gte=1), name="route_version_positive"),
        ]
        verbose_name = "مسار إنتاج"
        verbose_name_plural = "مسارات الإنتاج"

    def __str__(self):
        return f"{self.product.sku} — {self.production_line.code} — v{self.version}"

    def clean(self):
        if self.product_id and not self.product.active:
            raise ValidationError({"product": "اختر منتجًا نشطًا."})
        if self.production_line_id and not self.production_line.active:
            raise ValidationError({"production_line": "اختر خط إنتاج نشطًا."})
        if self.pk and self.orders.exclude(status=ProductionOrder.Status.DRAFT).exists():
            raise ValidationError("المسار المستخدم في أمر مطلق أو منفذ لا يتغير؛ أنشئ إصدارًا جديدًا.")

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.pk:
                locked = _locked_config_rows(type(self), [self.pk])
                if locked:
                    _ensure_config_unused(locked[0], "مسار الإنتاج")
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            locked = _locked_config_rows(type(self), [self.pk])
            if locked:
                _ensure_config_unused(locked[0], "مسار الإنتاج")
            return super().delete(*args, **kwargs)


class RoutingStep(models.Model):
    objects = ProtectedConfigurationManager()
    routing = models.ForeignKey(Routing, on_delete=models.PROTECT, related_name="steps")
    operation = models.ForeignKey(ProductionOperation, on_delete=models.PROTECT, related_name="routing_steps")
    sequence = models.PositiveSmallIntegerField("ترتيب العملية")
    notes = models.CharField("تعليمات العملية", max_length=240, blank=True)
    is_optional = models.BooleanField("عملية اختيارية", default=False)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(fields=["routing", "sequence"], name="unique_routing_step_sequence"),
            models.UniqueConstraint(fields=["routing", "operation"], name="unique_routing_operation"),
            models.CheckConstraint(condition=Q(sequence__gte=1), name="routing_sequence_positive"),
        ]
        verbose_name = "خطوة مسار"
        verbose_name_plural = "خطوات المسار"

    def clean(self):
        if self.routing_id and self.routing.orders.exclude(status=ProductionOrder.Status.DRAFT).exists():
            raise ValidationError("خطوات مسار مستخدم في أمر مطلق أو منفذ لا تتغير؛ أنشئ إصدارًا جديدًا.")
        if self.operation_id and not self.operation.active:
            raise ValidationError({"operation": "اختر عملية نشطة."})

    def _lock_parent_routings(self):
        ids = {self.routing_id} if self.routing_id else set()
        if self.pk:
            old = type(self).objects.filter(pk=self.pk).values_list("routing_id", flat=True).first()
            if old:
                ids.add(old)
        parents = _locked_config_rows(Routing, ids)
        for parent in parents:
            _ensure_config_unused(parent, "مسار الإنتاج")

    def save(self, *args, **kwargs):
        with transaction.atomic():
            self._lock_parent_routings()
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            self._lock_parent_routings()
            return super().delete(*args, **kwargs)


class ProductionOrder(models.Model):
    objects = ProductionOrderManager()

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "مسودة"
        RELEASED = "RELEASED", "مطلق"
        IN_PROGRESS = "IN_PROGRESS", "قيد التنفيذ"
        COMPLETED = "COMPLETED", "مكتمل"
        CANCELLED = "CANCELLED", "ملغي"

    order_number = models.CharField("رقم أمر الإنتاج", max_length=40, unique=True)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="production_orders")
    production_line = models.ForeignKey(ProductionLine, on_delete=models.PROTECT, related_name="orders")
    bom = models.ForeignKey(BillOfMaterials, on_delete=models.PROTECT, null=True, blank=True, related_name="orders")
    routing = models.ForeignKey(Routing, on_delete=models.PROTECT, null=True, blank=True, related_name="orders")
    source_location = models.ForeignKey(StorageLocation, on_delete=models.PROTECT, related_name="production_source_orders")
    planned_quantity = models.DecimalField("الكمية المخططة", max_digits=30, decimal_places=12)
    planned_unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="planned_production_orders")
    planned_quantity_base = models.DecimalField("المخطط بالوحدة الأساسية", max_digits=30, decimal_places=12)
    base_unit_snapshot = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, null=True, blank=True, related_name="production_order_base_snapshots")
    status = models.CharField("الحالة", max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    notes = models.TextField("ملاحظات", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_production_orders")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    released_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [models.CheckConstraint(condition=Q(planned_quantity__gt=0) & Q(planned_quantity_base__gt=0), name="production_order_quantity_positive")]
        verbose_name = "أمر إنتاج"
        verbose_name_plural = "أوامر الإنتاج"
        permissions = [
            ("issue_material", "Can issue material for production orders"),
            ("record_production_result", "Can record production results"),
            ("receive_finished_goods", "Can receive finished goods"),
            ("record_operation_progress", "Can record operation progress"),
            ("complete_operation", "Can complete production operations"),
            ("release_order", "Can release production orders"),
            ("cancel_order", "Can cancel production orders"),
            ("complete_order", "Can complete production orders"),
        ]

    def __str__(self):
        return self.order_number

    def clean(self):
        errors = {}
        if self.product_id and not self.product.active:
            errors["product"] = "اختر منتجًا نشطًا."
        if self.production_line_id and not self.production_line.active:
            errors["production_line"] = "اختر خط إنتاج نشطًا."
        if self.bom_id and self.product_id and self.bom.product_id != self.product_id:
            errors["bom"] = "قائمة المواد لا تخص المنتج المحدد."
        if self.routing_id and self.product_id and self.routing.product_id != self.product_id:
            errors["routing"] = "مسار الإنتاج لا يخص المنتج المحدد."
        if self.routing_id and self.production_line_id and self.routing.production_line_id != self.production_line_id:
            errors["routing"] = "مسار الإنتاج لا يخص خط الإنتاج المحدد."
        if self.source_location_id and (not self.source_location.active or not self.source_location.warehouse.active):
            errors["source_location"] = "اختر موقع مخزون نشطًا لصرف المواد."
        if self.product_id and self.planned_unit_id and self.planned_quantity:
            from inventory.services import to_base_quantity
            try:
                self.planned_quantity_base = to_base_quantity(self.product, self.planned_quantity, self.planned_unit)
            except ValidationError as exc:
                errors["planned_unit"] = exc.messages
        if errors:
            raise ValidationError(errors)

    @property
    def actual_good_base(self):
        return self.results.filter(correction__isnull=True).aggregate(total=models.Sum("good_quantity_base"))["total"] or 0

    @property
    def actual_scrap_base(self):
        return self.results.filter(correction__isnull=True).aggregate(total=models.Sum("scrap_quantity_base"))["total"] or 0

    @property
    def received_good_base(self):
        return self.finished_receipts.filter(movement__reversal__isnull=True).aggregate(total=models.Sum("quantity_base"))["total"] or 0

    def _is_posted(self):
        return self.status != self.Status.DRAFT

    def save(self, *args, **kwargs):
        if self.pk and self._is_posted():
            # Allow status transitions through services only
            original = ProductionOrder.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if original and original != self.Status.DRAFT and self.status != original:
                allowed = getattr(self, "_allow_status_transition", False)
                if not allowed:
                    raise ValidationError("حالة أمر الإنتاج لا تتغير مباشرة؛ استخدم الخدمات المخصصة (إطلاق، إكمال، إلغاء).")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._is_posted():
            raise ValidationError("أمر الإنتاج المطلق أو المنفذ لا يُحذف؛ استخدم الإلغاء الموثق.")
        return super().delete(*args, **kwargs)


class ProductionMaterialRequirement(models.Model):
    objects = ProductionMaterialRequirementManager()

    order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="requirements")
    source_component = models.ForeignKey(BOMComponent, on_delete=models.PROTECT, related_name="order_snapshots")
    material = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="production_requirements")
    base_unit_snapshot = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, null=True, blank=True, related_name="production_requirement_base_snapshots")
    sequence = models.PositiveSmallIntegerField("الترتيب")
    planned_quantity_base = models.DecimalField("الكمية المخططة بالوحدة الأساسية", max_digits=30, decimal_places=12)

    class Meta:
        ordering = ["sequence", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["order", "source_component"], name="unique_order_bom_requirement"),
            models.CheckConstraint(condition=Q(planned_quantity_base__gt=0), name="production_requirement_positive"),
        ]

    def _is_posted(self):
        return self.order_id and self.order.status != ProductionOrder.Status.DRAFT

    def save(self, *args, **kwargs):
        if self.pk and self._is_posted():
            raise ValidationError("متطلب المواد لأمر مطلق لا يُعدل.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._is_posted():
            raise ValidationError("متطلب المواد لأمر مطلق لا يُحذف.")
        return super().delete(*args, **kwargs)


class ProductionOrderOperation(models.Model):
    objects = ProductionOrderOperationManager()

    class Status(models.TextChoices):
        PENDING = "PENDING", "لم تبدأ"
        IN_PROGRESS = "IN_PROGRESS", "قيد التنفيذ"
        COMPLETED = "COMPLETED", "مكتملة"
        SKIPPED = "SKIPPED", "تم تجاوزها"

    order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="operations")
    source_step = models.ForeignKey(RoutingStep, on_delete=models.PROTECT, related_name="order_snapshots")
    operation = models.ForeignKey(ProductionOperation, on_delete=models.PROTECT, related_name="order_snapshots")
    sequence = models.PositiveSmallIntegerField("الترتيب")
    operation_name = models.CharField("اسم العملية وقت الإطلاق", max_length=120)
    instructions = models.CharField("التعليمات وقت الإطلاق", max_length=240, blank=True)
    is_optional = models.BooleanField("عملية اختيارية وقت الإطلاق", default=False)
    status = models.CharField("حالة العملية", max_length=20, choices=Status.choices, default=Status.PENDING)

    class Meta:
        ordering = ["sequence"]
        constraints = [models.UniqueConstraint(fields=["order", "sequence"], name="unique_order_operation_sequence")]

    def _is_posted(self):
        return self.order_id and self.order.status != ProductionOrder.Status.DRAFT

    def save(self, *args, **kwargs):
        if self.pk and self._is_posted():
            allowed = getattr(self, "_allow_status_transition", False)
            if not allowed:
                raise ValidationError("عملية أمر الإنتاج المطلق لا تُعدل.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._is_posted():
            raise ValidationError("عملية أمر الإنتاج المطلق لا تُحذف.")
        return super().delete(*args, **kwargs)


class MaterialIssue(models.Model):
    objects = MaterialIssueManager()

    class CostSource(models.TextChoices):
        LAST_PURCHASE_COST = "LAST_PURCHASE_COST", "آخر سعر شراء"
        STANDARD_PRICE_FALLBACK = "STANDARD_PRICE_FALLBACK", "سعر قياسي احتياطي (يدوي)"
        UNAVAILABLE = "UNAVAILABLE", "غير متاح"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="material_issues")
    requirement = models.ForeignKey(ProductionMaterialRequirement, on_delete=models.PROTECT, related_name="issues")
    reference = models.CharField("مرجع الصرف", max_length=100, unique=True)
    roll_reference = models.CharField(
        "مرجع الرول",
        max_length=120,
        blank=True,
        help_text=(
            "رقم/وسم الرول الفعلي وقت الصرف؛ يُدخل عند إنشاء الصرف فقط ويظل "
            "مرجعًا وثائقيًا دون احتساب تلقائي للكميات."
        ),
    )
    movement = models.OneToOneField(StockMovement, on_delete=models.PROTECT, related_name="production_material_issue")
    quantity = models.DecimalField("الكمية المصروفة", max_digits=30, decimal_places=12)
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="production_material_issues")
    quantity_base = models.DecimalField("المصروف بالوحدة الأساسية", max_digits=30, decimal_places=12)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="production_material_issues")
    created_at = models.DateTimeField(auto_now_add=True)

    # Cost snapshot at issue time
    unit_cost_snapshot = models.DecimalField(
        "سعر الوحدة الأساسية وقت الصرف (ج.م)", max_digits=18, decimal_places=6,
        null=True, blank=True,
        help_text="لقطة من آخر سعر شراء (أو السعر القياسي الاحتياطي) وقت صرف المادة؛ null يعني غير متاح."
    )
    cost_source = models.CharField(
        "مصدر تكلفة الصرف", max_length=30, choices=CostSource.choices,
        null=True, blank=True,
        help_text="من أين أُخذت تكلفة الوحدة: آخر سعر شراء، سعر قياسي احتياطي، أو غير متاح."
    )

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0) & Q(quantity_base__gt=0), name="material_issue_quantity_positive")]

    def _is_posted(self):
        # Every recorded issue is posted. There is no status or reversal field on
        # the document itself: reversal is recorded on the linked StockMovement
        # (movement.reversal), so this row is create-once and immutable.
        return self.pk is not None

    def save(self, *args, **kwargs):
        # The UUID primary key is pre-assigned by its default, so a bare `self.pk`
        # check would also block the legitimate initial INSERT. `_state.adding` is
        # True only while inserting for the first time, which cleanly separates
        # document creation (allowed) from any later edit (rejected).
        if not self._state.adding and self._is_posted():
            raise ValidationError("صرف المواد المرحلة غير قابل للتعديل؛ استخدم خدمة العكس.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._is_posted():
            raise ValidationError("صرف المواد المرحلة لا يُحذف؛ استخدم خدمة العكس.")
        return super().delete(*args, **kwargs)


class OperationProgress(models.Model):
    objects = OperationProgressManager()

    submission_key = models.UUIDField(default=uuid.uuid4, editable=False)
    order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="operation_progress")
    order_operation = models.ForeignKey(ProductionOrderOperation, on_delete=models.PROTECT, related_name="progress_entries")
    quantity_processed = models.DecimalField("الكمية المنفذة", max_digits=30, decimal_places=12)
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="operation_progress")
    quantity_processed_base = models.DecimalField("المنفذ بالوحدة الأساسية", max_digits=30, decimal_places=12)
    notes = models.CharField("ملاحظات", max_length=240, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="operation_progress_entries")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [models.CheckConstraint(condition=Q(quantity_processed__gt=0) & Q(quantity_processed_base__gt=0), name="operation_progress_quantity_positive"), models.UniqueConstraint(fields=["order", "submission_key"], name="unique_operation_progress_submission")]

    def _is_posted(self):
        return self.pk is not None

    def save(self, *args, **kwargs):
        if self.pk and self._is_posted():
            raise ValidationError("تقدم العملية المسجل غير قابل للتعديل.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._is_posted():
            raise ValidationError("تقدم العملية لا يُحذف.")
        return super().delete(*args, **kwargs)


class ProductionResult(models.Model):
    objects = ProductionResultManager()

    submission_key = models.UUIDField(default=uuid.uuid4, editable=False)
    order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="results")
    good_quantity = models.DecimalField("الكمية الجيدة", max_digits=30, decimal_places=12, default=0)
    scrap_quantity = models.DecimalField("كمية الهالك", max_digits=30, decimal_places=12, default=0)
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="production_results")
    good_quantity_base = models.DecimalField("الجيد بالوحدة الأساسية", max_digits=30, decimal_places=12, default=0)
    scrap_quantity_base = models.DecimalField("الهالك بالوحدة الأساسية", max_digits=30, decimal_places=12, default=0)
    notes = models.CharField("ملاحظات", max_length=240, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="production_results")
    occurred_at = models.DateTimeField("تاريخ الإنتاج")
    posted_at = models.DateTimeField(
        "وقت تسجيل النظام", default=timezone.now, editable=False, blank=True, null=True
    )

    class Meta:
        ordering = ["occurred_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(good_quantity__gte=0) & Q(scrap_quantity__gte=0), name="production_result_quantities_nonnegative"),
            models.CheckConstraint(condition=Q(good_quantity_base__gte=0) & Q(scrap_quantity_base__gte=0), name="production_result_bases_nonnegative"),
            models.CheckConstraint(condition=Q(good_quantity__gt=0) | Q(scrap_quantity__gt=0), name="production_result_nonzero"),
            models.UniqueConstraint(fields=["order", "submission_key"], name="unique_production_result_submission"),
        ]

    def _is_posted(self):
        # All recorded results are considered posted; they cannot be edited directly
        return self.pk is not None

    def save(self, *args, **kwargs):
        if self.pk and self._is_posted():
            raise ValidationError("نتيجة الإنتاج المسجلة غير قابلة للتعديل؛ استخدم خدمة العكس للحركة المخزنية المرتبطة.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._is_posted():
            raise ValidationError("نتيجة الإنتاج لا تُحذف؛ صحح سجل الإنتاج عبر الحركة المخزنية.")
        return super().delete(*args, **kwargs)

    @property
    def yield_percent(self):
        total = self.good_quantity_base + self.scrap_quantity_base
        return (self.good_quantity_base * 100 / total) if total else None


class ProductionMaterialWaste(models.Model):
    """Immutable measured raw-material waste, valued at its posting instant."""

    objects = ImmutableProductionRecordManager()

    class CostSource(models.TextChoices):
        LAST_PURCHASE_COST = "LAST_PURCHASE_COST", "آخر سعر شراء وقت الترحيل"
        STANDARD_PRICE_FALLBACK = "STANDARD_PRICE_FALLBACK", "سعر قياسي احتياطي مؤرخ"
        UNAVAILABLE = "UNAVAILABLE", "غير متاح وقت الترحيل"

    order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="material_waste")
    result = models.ForeignKey(ProductionResult, on_delete=models.PROTECT, related_name="material_waste")
    material = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="production_waste")
    material_issue = models.ForeignKey(
        "MaterialIssue", on_delete=models.PROTECT, null=True, blank=True, related_name="waste_records"
    )
    roll_reference = models.CharField("مرجع الرول", max_length=120, blank=True)
    quantity_base = models.DecimalField("الكمية التالفة بالوحدة الأساسية", max_digits=30, decimal_places=12)
    unit_cost_snapshot = models.DecimalField("تكلفة الوحدة وقت الترحيل", max_digits=18, decimal_places=6, null=True, blank=True)
    total_cost_snapshot = models.DecimalField("تكلفة الهالك وقت الترحيل", max_digits=18, decimal_places=2, null=True, blank=True)
    cost_source = models.CharField(max_length=30, choices=CostSource.choices)
    posted_at = models.DateTimeField("وقت الترحيل", auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="posted_production_waste")
    reversal_of = models.OneToOneField("self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversal")
    correction_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["posted_at", "pk"]
        constraints = [models.CheckConstraint(condition=Q(quantity_base__gt=0), name="production_material_waste_positive")]
        permissions = [("record_production_waste", "Can record measured production material waste")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("سجل هالك المواد مرحّل وغير قابل للتعديل؛ استخدم العكس الموثق.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("سجل هالك المواد لا يُحذف؛ استخدم العكس الموثق.")


class ProductionWasteReview(models.Model):
    """Explicit evidence that a result's material damage review is complete."""

    objects = ImmutableProductionRecordManager()

    result = models.OneToOneField(ProductionResult, on_delete=models.PROTECT, related_name="waste_review")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="production_waste_reviews")
    reviewed_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("مراجعة هالك المواد سجل ثابت.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("مراجعة هالك المواد لا تُحذف.")


class ProductionResultReversal(models.Model):
    """Audited full reversal; a corrected result is posted as a new result."""

    objects = ImmutableProductionRecordManager()

    result = models.OneToOneField(ProductionResult, on_delete=models.PROTECT, related_name="correction")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="production_result_reversals")
    reversed_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=255)

    class Meta:
        permissions = [("correct_production_result", "Can correct or reverse production results")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("عكس نتيجة الإنتاج سجل تدقيق غير قابل للتعديل.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("سجل عكس نتيجة الإنتاج لا يُحذف.")


class FinishedGoodsReceipt(models.Model):
    objects = FinishedGoodsReceiptManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="finished_receipts")
    reference = models.CharField("مرجع استلام المنتج", max_length=100, unique=True)
    movement = models.OneToOneField(StockMovement, on_delete=models.PROTECT, related_name="manufacturing_finished_receipt")
    quantity = models.DecimalField("الكمية المستلمة", max_digits=30, decimal_places=12)
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="finished_goods_receipts")
    quantity_base = models.DecimalField("المستلم بالوحدة الأساسية", max_digits=30, decimal_places=12)
    destination_location = models.ForeignKey(StorageLocation, on_delete=models.PROTECT, related_name="production_receipts")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="finished_goods_receipts")
    replacement_for = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="replacement_receipts")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0) & Q(quantity_base__gt=0), name="finished_receipt_quantity_positive")]

    def _is_posted(self):
        # Every recorded receipt is posted. Reversal is recorded on the linked
        # StockMovement, not on the document row, so this row is create-once.
        return self.pk is not None

    def save(self, *args, **kwargs):
        # UUID primary key is pre-assigned by its default, so gate on
        # `_state.adding` to allow the initial INSERT but reject later edits.
        if not self._state.adding and self._is_posted():
            raise ValidationError("استلام المنتج النهائي غير قابل للتعديل؛ استخدم خدمة العكس.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._is_posted():
            raise ValidationError("استلام المنتج النهائي لا يُحذف؛ استخدم خدمة العكس.")
        return super().delete(*args, **kwargs)


class MachineCounterReading(models.Model):
    """Manual machine-counter interval entered by a worker.

    The counter is read physically; no device integration exists. One record is
    one measurement interval: ``counter_output = end_reading - start_reading``.
    Readings never post stock movements or feed costing directly — the
    authoritative production record remains ``ProductionResult`` — so editing a
    reading can never silently change a finalized costing or profit period.
    Corrections are allowed only through the audited service, which requires a
    reason and stores the superseded values.
    """

    class Status(models.TextChoices):
        PROVISIONAL = "PROVISIONAL", "مبدئي (بانتظار الإغلاق)"
        COMPLETE = "COMPLETE", "مكتمل (بانتظار الاعتماد)"
        APPROVED = "APPROVED", "معتمد"
        CORRECTED = "CORRECTED", "مصحح (مجدول للاعتماد)"

    order = models.ForeignKey(
        ProductionOrder,
        on_delete=models.PROTECT,
        related_name="counter_readings",
        verbose_name="أمر الإنتاج",
    )
    order_operation = models.ForeignKey(
        ProductionOrderOperation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="counter_readings",
        verbose_name="مرحلة الإنتاج",
    )
    production_line = models.ForeignKey(
        ProductionLine,
        on_delete=models.PROTECT,
        related_name="counter_readings",
        verbose_name="خط الإنتاج / الآلة",
    )
    material_issue = models.ForeignKey(
        MaterialIssue,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="counter_readings",
        verbose_name="صرف المادة المرتبط",
        help_text="الصرف الذي وُزِّعت منه رولات هذه الفترة إن وُجد؛ للتتبع الجزئي للرول.",
    )
    roll_reference = models.CharField(
        "مرجع الرول",
        max_length=120,
        blank=True,
        help_text="وسم الرول المستخدم أثناء الفترة كما قرأه العامل.",
    )
    unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        related_name="machine_counter_readings",
        verbose_name="وحدة العدّ",
    )
    start_reading = models.DecimalField(
        "قراءة العدّاد عند البداية",
        max_digits=20,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0"))],
    )
    end_reading = models.DecimalField(
        "قراءة العدّاد عند النهاية",
        max_digits=20,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    counter_output = models.DecimalField(
        "ناتج العدّاد (النهاية − البداية)",
        max_digits=20,
        decimal_places=4,
        null=True,
        blank=True,
    )
    counter_output_base = models.DecimalField(
        "ناتج العدّاد بالوحدة الأساسية",
        max_digits=30,
        decimal_places=12,
        null=True,
        blank=True,
    )
    rejected_quantity = models.DecimalField(
        "الكمية المرفوضة المسجلة",
        max_digits=20,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="تُملأ عند الإغلاق إن لم يستبعدها العدّاد تلقائيًا؛ لا تتجاوز ناتج العدّاد.",
    )
    rejected_quantity_base = models.DecimalField(
        "المرفوض بالوحدة الأساسية",
        max_digits=30,
        decimal_places=12,
        null=True,
        blank=True,
    )
    started_at = models.DateTimeField("وقت بدء القراءة")
    ended_at = models.DateTimeField("وقت إغلاق القراءة", null=True, blank=True)
    status = models.CharField(
        "حالة القراءة",
        max_length=20,
        choices=Status.choices,
        default=Status.PROVISIONAL,
        db_index=True,
    )
    notes = models.CharField("ملاحظات", max_length=240, blank=True)
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="counter_readings_entered",
        verbose_name="أدخل القراءة",
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="counter_readings_closed",
        verbose_name="أغلق القراءة",
    )
    closed_at = models.DateTimeField("وقت الإغلاق", null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="counter_readings_approved",
        verbose_name="اعتمد القراءة",
    )
    approved_at = models.DateTimeField("وقت الاعتماد", null=True, blank=True)
    correction_reason = models.CharField(
        "آخر سبب تصحيح",
        max_length=240,
        blank=True,
    )
    corrected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="counter_readings_corrected",
        verbose_name="آخر مصحِّح",
    )
    corrected_at = models.DateTimeField("وقت آخر تصحيح", null=True, blank=True)
    corrections = models.JSONField(
        "سجل التصحيحات",
        default=list,
        blank=True,
        help_text="قوائم التصحيحات السابقة: من/متى/سبب/القيم المستبدلة.",
    )
    continuation_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="continuations",
        verbose_name="متابعة لقراءة سابقة",
        help_text="يُملأ عند استمرار العدّاد بعد إعادة ضبط أو استبدال.",
    )
    reset_reason = models.CharField(
        "سبب إعادة الضبط / الاستمرار",
        max_length=240,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(start_reading__gte=0),
                name="counter_start_reading_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(end_reading__isnull=True) | Q(end_reading__gte=0),
                name="counter_end_reading_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(rejected_quantity__isnull=True) | Q(rejected_quantity__gte=0),
                name="counter_rejected_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(end_reading__isnull=True) | Q(end_reading__gte=F("start_reading")),
                name="counter_output_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(rejected_quantity__isnull=True)
                | Q(counter_output__isnull=True)
                | Q(rejected_quantity__lte=F("counter_output")),
                name="counter_rejected_within_output",
            ),
            models.CheckConstraint(
                condition=Q(continuation_of__isnull=True) | ~Q(reset_reason=""),
                name="counter_continuation_requires_reset_reason",
            ),
        ]
        permissions = [
            ("record_counter_reading", "Can record machine counter readings"),
            ("approve_counter_reading", "Can approve machine counter readings"),
            ("correct_counter_reading", "Can correct machine counter readings with a reason"),
        ]
        verbose_name = "قراءة عدّاد آلة"
        verbose_name_plural = "قراءات عدّاد الآلة"

    def _is_recorded(self):
        return self.pk is not None

    def save(self, *args, **kwargs):
        # Recorded readings change only through the audited correction service,
        # which sets `_allow_service_write` after validating the transition.
        if self._is_recorded() and not getattr(self, "_allow_service_write", False):
            raise ValidationError(
                "قراءة العدّاد المسجلة لا تُعدَّل مباشرة؛ استخدم خدمة التصحيح "
                "الإلزامية بسبب السبب والتوقيع الزمني."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._is_recorded():
            raise ValidationError(
                "قراءات العدّاد لا تُحذف للحفاظ على سجل التدقيق؛ صحح عبر الخدمة الموثّقة."
            )
        return super().delete(*args, **kwargs)

    @property
    def is_closed(self):
        return self.end_reading is not None and self.ended_at is not None

    @property
    def accepted_good_base(self):
        """Accepted output per this interval: counter output minus rejected.

        Only meaningful when the line's counter behavior confirms what the
        counter counts; otherwise callers must label it provisional.
        """
        if self.counter_output_base is None:
            return None
        rejected = self.rejected_quantity_base or Decimal("0")
        return self.counter_output_base - rejected

    def __str__(self):
        return (
            f"عدّاد {self.order.order_number}: {self.start_reading}→"
            f"{self.end_reading or '…'} ({self.get_status_display()})"
        )
