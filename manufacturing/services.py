from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import uuid

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from inventory.services import balance_for, post_movement, to_base_quantity
from inventory.models import StockMovement

from .models import (
    BillOfMaterials,
    BOMComponent,
    FinishedGoodsReceipt,
    MaterialIssue,
    OperationProgress,
    ProductionMaterialRequirement,
    ProductionOrder,
    ProductionOrderOperation,
    ProductionResult,
    Routing,
    RoutingStep,
    _locked_config_rows,
)

SCALE = Decimal("0.000000000001")
ACTIVE_ORDER_STATUSES = (ProductionOrder.Status.RELEASED, ProductionOrder.Status.IN_PROGRESS)


def _staff(actor):
    if not actor or not actor.is_authenticated or not actor.is_staff:
        raise ValidationError("هذا الإجراء متاح لمستخدمي النظام الداخليين فقط.")


def _order_number():
    return f"MO-{date.today():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"


def _exact_base(value, message):
    value = Decimal(value)
    with localcontext() as context:
        context.prec = 80
        try:
            rounded = value.quantize(SCALE)
        except InvalidOperation as exc:
            raise ValidationError(message) from exc
    if rounded != value or len(rounded.as_tuple().digits) > 30 or rounded.adjusted() >= 18:
        raise ValidationError(message)
    return rounded


def save_draft_order(*, actor, order=None, **values):
    _staff(actor)
    with transaction.atomic():
        if order is None:
            order = ProductionOrder(created_by=actor, order_number=_order_number())
        else:
            order = ProductionOrder.objects.select_for_update().get(pk=order.pk)
            if order.status != ProductionOrder.Status.DRAFT:
                raise ValidationError("لا يمكن تعديل أمر إنتاج بعد إطلاقه.")
        for field, value in values.items():
            setattr(order, field, value)
        order.full_clean(exclude=["planned_quantity_base"])
        order.save()
        return order


def release_order(*, order, actor):
    _staff(actor)
    with transaction.atomic():
        order = ProductionOrder.objects.select_for_update(of=("self",)).select_related(
            "product", "production_line", "bom", "routing", "planned_unit"
        ).get(pk=order.pk)
        if order.status != ProductionOrder.Status.DRAFT:
            raise ValidationError("يمكن إطلاق أوامر الإنتاج التي ما زالت مسودة فقط.")
        if not order.bom_id:
            raise ValidationError("قائمة المواد غير مضبوطة أو غير نشطة لهذا المنتج؛ لم يتم اختراع متطلبات.")
        if not order.routing_id:
            raise ValidationError("مسار الإنتاج غير مضبوط أو غير نشط لهذا المنتج والخط.")
        bom = BillOfMaterials.objects.select_for_update(of=("self",)).get(pk=order.bom_id)
        routing = Routing.objects.select_for_update(of=("self",)).get(pk=order.routing_id)
        if not bom.active:
            raise ValidationError("قائمة المواد غير مضبوطة أو غير نشطة لهذا المنتج؛ لم يتم اختراع متطلبات.")
        if not routing.active:
            raise ValidationError("مسار الإنتاج غير مضبوط أو غير نشط لهذا المنتج والخط.")
        if bom.product_id != order.product_id:
            raise ValidationError("قائمة المواد لا تخص المنتج المحدد.")
        if routing.product_id != order.product_id or routing.production_line_id != order.production_line_id:
            raise ValidationError("مسار الإنتاج لا يطابق المنتج وخط الإنتاج المحددين.")
        component_ids = BOMComponent.objects.filter(bom=bom).values_list("pk", flat=True)
        _locked_config_rows(BOMComponent, component_ids)
        _locked_config_rows(RoutingStep, RoutingStep.objects.filter(routing=routing).values_list("pk", flat=True))
        components = list(BOMComponent.objects.filter(bom=bom).select_related("material", "unit", "material__base_uom").order_by("sequence", "pk"))
        steps = list(RoutingStep.objects.filter(routing=routing).select_related("operation").order_by("sequence", "pk"))
        if not components:
            raise ValidationError("قائمة المواد لا تحتوي على مكونات؛ أضف بيانات المصنع قبل الإطلاق.")
        if not steps:
            raise ValidationError("مسار الإنتاج لا يحتوي على عمليات؛ أضف عمليات المنتج المطلوبة قبل الإطلاق.")

        bom_output_base = to_base_quantity(order.product, bom.output_quantity, bom.output_unit)
        requirements = []
        for component in components:
            if not component.material.active or not component.unit.active:
                raise ValidationError("تحتوي قائمة المواد على مكوّن أو وحدة غير نشطة.")
            component_base = to_base_quantity(component.material, component.quantity, component.unit)
            with localcontext() as context:
                context.prec = 80
                required = order.planned_quantity_base * component_base / bom_output_base
            required = _exact_base(required, "متطلب المواد يتجاوز دقة التخزين؛ لم يتم تقريبه.")
            requirements.append(ProductionMaterialRequirement(
                order=order,
                source_component=component,
                material=component.material,
                base_unit_snapshot=component.material.base_uom,
                sequence=component.sequence,
                planned_quantity_base=required,
            ))

        ProductionMaterialRequirement.objects.bulk_create(requirements)
        ProductionOrderOperation.objects.bulk_create([
            ProductionOrderOperation(
                order=order,
                source_step=step,
                operation=step.operation,
                sequence=step.sequence,
                operation_name=step.operation.name_ar,
                instructions=step.notes,
                is_optional=step.is_optional,
            )
            for step in steps
        ])
        order.status = ProductionOrder.Status.RELEASED
        order.base_unit_snapshot = order.product.base_uom
        order.released_at = timezone.now()
        order.save(update_fields=["status", "base_unit_snapshot", "released_at", "updated_at"])
        return order


def _issued_base(requirement):
    return requirement.issues.filter(movement__reversal__isnull=True).aggregate(total=Sum("quantity_base"))["total"] or Decimal("0")


def requirement_status(requirement):
    issued = _issued_base(requirement)
    remaining = max(Decimal("0"), requirement.planned_quantity_base - issued)
    available = balance_for(requirement.material, requirement.order.source_location)
    shortage = max(Decimal("0"), remaining - available)
    return {
        "requirement": requirement,
        "planned": requirement.planned_quantity_base,
        "issued": issued,
        "remaining": remaining,
        "available": available,
        "shortage": shortage,
    }


def issue_material(*, order, requirement, quantity, unit, reference, actor):
    _staff(actor)
    if not reference or not reference.strip():
        raise ValidationError({"reference": "مرجع الصرف مطلوب لمنع التكرار."})
    with transaction.atomic():
        from costing.models import ensure_period_open

        posting_date = timezone.localdate()
        ensure_period_open(
            posting_date.year, posting_date.month, "صرف مواد لأمر إنتاج"
        )
        order = ProductionOrder.objects.select_for_update().select_related("product", "source_location").get(pk=order.pk)
        if order.status not in ACTIVE_ORDER_STATUSES:
            raise ValidationError("لا يمكن صرف مواد لأمر غير مطلق أو قيد التنفيذ.")
        requirement = ProductionMaterialRequirement.objects.select_for_update().select_related(
            "material", "material__base_uom", "order__source_location"
        ).get(pk=requirement.pk, order=order)
        if MaterialIssue.objects.filter(reference=reference.strip()).exists():
            raise ValidationError({"reference": "مرجع الصرف مستخدم من قبل."})
        quantity_base = to_base_quantity(requirement.material, quantity, unit)
        if requirement.material.base_uom_id != requirement.base_unit_snapshot_id:
            raise ValidationError("تغيرت وحدة المخزون الأساسية للمادة بعد إطلاق الأمر؛ راجع بيانات الصنف قبل الصرف.")
        remaining = max(Decimal("0"), requirement.planned_quantity_base - _issued_base(requirement))
        if quantity_base > remaining:
            raise ValidationError({"quantity": f"الكمية تتجاوز المتبقي لهذا المتطلب ({remaining})."})
        issue_id = uuid.uuid4()
        movement = post_movement(
            reference=f"MFG-I-{issue_id.hex}",
            movement_type=StockMovement.Type.ISSUE,
            product=requirement.material,
            quantity=quantity,
            unit=unit,
            actor=actor,
            source_location=order.source_location,
            external_reference=reference.strip(),
            source_reference_type="manufacturing.material_issue",
            source_reference_id=str(issue_id),
            reason=f"صرف لأمر الإنتاج {order.order_number}",
        )
        try:
            with transaction.atomic():
                issue = MaterialIssue.objects.create(
                    id=issue_id, order=order, requirement=requirement, reference=reference.strip(), movement=movement,
                    quantity=quantity, unit=unit, quantity_base=quantity_base, actor=actor,
                )
        except IntegrityError as exc:
            cause = exc.__cause__
            constraint = getattr(getattr(cause, "diag", None), "constraint_name", "")
            if constraint == "manufacturing_materialissue_reference_key" or "UNIQUE constraint failed: manufacturing_materialissue.reference" in str(cause):
                raise ValidationError({"reference": "مرجع الصرف مستخدم من قبل."}) from exc
            raise
        if order.status == ProductionOrder.Status.RELEASED:
            order.status = ProductionOrder.Status.IN_PROGRESS
            order.save(update_fields=["status", "updated_at"])
        return issue


def _previous_non_skipped_step(order, step):
    return order.operations.filter(sequence__lt=step.sequence).exclude(
        status=ProductionOrderOperation.Status.SKIPPED
    ).order_by("-sequence").first()


def record_operation_progress(*, order, order_operation, quantity, unit, notes, actor, submission_key=None):
    _staff(actor)
    submission_key = submission_key or uuid.uuid4()
    with transaction.atomic():
        order = ProductionOrder.objects.select_for_update().select_related("product").get(pk=order.pk)
        existing = OperationProgress.objects.filter(order=order, submission_key=submission_key).first()
        if existing:
            same = (existing.order_operation_id == order_operation.pk and existing.quantity_processed == Decimal(quantity)
                    and existing.unit_id == unit.pk and existing.notes == notes.strip() and existing.actor_id == actor.pk)
            if same:
                return existing, False
            raise ValidationError("مفتاح الإرسال سبق استخدامه ببيانات مختلفة.")
        if order.status not in ACTIVE_ORDER_STATUSES:
            raise ValidationError("لا يمكن تسجيل تقدم لأمر غير نشط.")
        if order.product.base_uom_id != order.base_unit_snapshot_id:
            raise ValidationError("تغيرت وحدة المنتج الأساسية بعد إطلاق الأمر؛ راجع بيانات الصنف قبل تسجيل الإنتاج.")
        step = ProductionOrderOperation.objects.select_for_update().get(pk=order_operation.pk, order=order)
        if step.status in (ProductionOrderOperation.Status.COMPLETED, ProductionOrderOperation.Status.SKIPPED):
            raise ValidationError("العملية مكتملة أو تم تجاوزها.")
        prior_steps = order.operations.filter(sequence__lt=step.sequence).exclude(
            status__in=(ProductionOrderOperation.Status.COMPLETED, ProductionOrderOperation.Status.SKIPPED)
        )
        if prior_steps.exists():
            raise ValidationError("يجب إكمال العمليات السابقة أو تجاوزها أولًا.")
        quantity_base = to_base_quantity(order.product, quantity, unit)
        already_processed = step.progress_entries.aggregate(total=Sum("quantity_processed_base"))["total"] or Decimal("0")
        previous_step = _previous_non_skipped_step(order, step)
        previous_total = (previous_step.progress_entries.aggregate(total=Sum("quantity_processed_base"))["total"] or Decimal("0")) if previous_step else order.planned_quantity_base
        if already_processed + quantity_base > order.planned_quantity_base or already_processed + quantity_base > previous_total:
            raise ValidationError({"quantity": "إجمالي المنفذ لهذه العملية يتجاوز كمية الأمر المخططة."})
        progress = OperationProgress.objects.create(
            submission_key=submission_key,
            order=order, order_operation=step, quantity_processed=quantity, unit=unit,
            quantity_processed_base=quantity_base, notes=notes.strip(), actor=actor,
        )
        step.status = ProductionOrderOperation.Status.IN_PROGRESS
        step.save(update_fields=["status"])
        if order.status == ProductionOrder.Status.RELEASED:
            order.status = ProductionOrder.Status.IN_PROGRESS
            order.save(update_fields=["status", "updated_at"])
        return progress, True


def complete_operation(*, order, order_operation, actor, skip=False):
    _staff(actor)
    with transaction.atomic():
        order = ProductionOrder.objects.select_for_update().get(pk=order.pk)
        if order.status not in ACTIVE_ORDER_STATUSES:
            raise ValidationError("لا يمكن تغيير عملية في أمر غير نشط.")
        step = ProductionOrderOperation.objects.select_for_update().get(pk=order_operation.pk, order=order)
        if step.status not in (ProductionOrderOperation.Status.PENDING, ProductionOrderOperation.Status.IN_PROGRESS):
            raise ValidationError("حالة العملية لا تسمح بهذا الإجراء.")
        if order.operations.filter(sequence__lt=step.sequence).exclude(
            status__in=(ProductionOrderOperation.Status.COMPLETED, ProductionOrderOperation.Status.SKIPPED)
        ).exists():
            raise ValidationError("يجب معالجة العمليات السابقة أولًا.")
        if skip:
            if not step.is_optional:
                raise ValidationError("لا يمكن تجاوز عملية مطلوبة في مسار الإنتاج.")
            if step.status != ProductionOrderOperation.Status.PENDING or step.progress_entries.exists():
                raise ValidationError("لا يمكن تجاوز عملية بدأ تسجيل تقدمها.")
            step.status = ProductionOrderOperation.Status.SKIPPED
        else:
            if not step.progress_entries.exists():
                raise ValidationError("سجّل كمية منفذة قبل إكمال العملية.")
            total = step.progress_entries.aggregate(total=Sum("quantity_processed_base"))["total"] or Decimal("0")
            previous_step = _previous_non_skipped_step(order, step)
            previous_total = (previous_step.progress_entries.aggregate(total=Sum("quantity_processed_base"))["total"] or Decimal("0")) if previous_step else order.planned_quantity_base
            if total > previous_total:
                raise ValidationError("كمية العملية تتجاوز الكمية التي أكملتها العملية السابقة.")
            step.status = ProductionOrderOperation.Status.COMPLETED
        step.save(update_fields=["status"])
        if order.status == ProductionOrder.Status.RELEASED:
            order.status = ProductionOrder.Status.IN_PROGRESS
            order.save(update_fields=["status", "updated_at"])
        return step


def record_production_result(*, order, good_quantity, scrap_quantity, unit, notes, actor, submission_key=None):
    _staff(actor)
    submission_key = submission_key or uuid.uuid4()
    good_quantity = Decimal(good_quantity or 0)
    scrap_quantity = Decimal(scrap_quantity or 0)
    if good_quantity < 0 or scrap_quantity < 0 or good_quantity + scrap_quantity <= 0:
        raise ValidationError("أدخل كمية جيدة أو هالك موجبة؛ لا يمكن إدخال كميات سالبة.")
    with transaction.atomic():
        order = ProductionOrder.objects.select_for_update().select_related("product").get(pk=order.pk)
        existing = ProductionResult.objects.filter(order=order, submission_key=submission_key).first()
        if existing:
            same = (existing.good_quantity == good_quantity and existing.scrap_quantity == scrap_quantity
                    and existing.unit_id == unit.pk and existing.notes == notes.strip() and existing.actor_id == actor.pk)
            if same:
                return existing, False
            raise ValidationError("مفتاح الإرسال سبق استخدامه ببيانات مختلفة.")
        from costing.models import ensure_period_open

        posting_date = timezone.localdate()
        ensure_period_open(
            posting_date.year, posting_date.month, "تسجيل نتيجة إنتاج"
        )
        if order.status not in ACTIVE_ORDER_STATUSES:
            raise ValidationError("لا يمكن تسجيل إنتاج لأمر غير نشط.")
        if order.product.base_uom_id != order.base_unit_snapshot_id:
            raise ValidationError("تغيرت وحدة المنتج الأساسية بعد إطلاق الأمر؛ راجع بيانات الصنف قبل تسجيل الإنتاج.")
        good_base = to_base_quantity(order.product, good_quantity, unit) if good_quantity else Decimal("0")
        scrap_base = to_base_quantity(order.product, scrap_quantity, unit) if scrap_quantity else Decimal("0")
        previous = order.results.aggregate(
            good=Sum("good_quantity_base"), scrap=Sum("scrap_quantity_base")
        )
        produced = (previous["good"] or Decimal("0")) + (previous["scrap"] or Decimal("0"))
        if produced + good_base + scrap_base > order.planned_quantity_base:
            raise ValidationError("إجمالي المنتج الجيد والهالك يتجاوز الكمية المخططة للأمر.")
        result = ProductionResult.objects.create(
            submission_key=submission_key,
            order=order, good_quantity=good_quantity, scrap_quantity=scrap_quantity, unit=unit,
            good_quantity_base=good_base, scrap_quantity_base=scrap_base, notes=notes.strip(), actor=actor,
        )
        if order.status == ProductionOrder.Status.RELEASED:
            order.status = ProductionOrder.Status.IN_PROGRESS
            order.save(update_fields=["status", "updated_at"])
        return result, True


def receive_finished_goods(*, order, quantity, unit, destination_location, reference, actor):
    return _receive_finished_goods(order=order, quantity=quantity, unit=unit,
                                   destination_location=destination_location, reference=reference,
                                   actor=actor, replacement_for=None)


def receive_replacement_finished_goods(*, order, original_receipt, quantity, unit, destination_location, reference, actor):
    return _receive_finished_goods(order=order, quantity=quantity, unit=unit,
                                   destination_location=destination_location, reference=reference,
                                   actor=actor, replacement_for=original_receipt)


def _receive_finished_goods(*, order, quantity, unit, destination_location, reference, actor, replacement_for):
    _staff(actor)
    if not reference or not reference.strip():
        raise ValidationError({"reference": "مرجع الاستلام مطلوب لمنع التكرار."})
    with transaction.atomic():
        order = ProductionOrder.objects.select_for_update().select_related("product").get(pk=order.pk)
        if replacement_for is None:
            if order.status not in ACTIVE_ORDER_STATUSES:
                raise ValidationError("لا يمكن استلام إنتاج لأمر غير نشط؛ الاستلام التعويضي يتطلب مساره الصريح.")
        else:
            original = FinishedGoodsReceipt.objects.select_for_update().get(pk=replacement_for.pk, order=order)
            if (order.status != ProductionOrder.Status.COMPLETED or original.replacement_for_id
                    or not StockMovement.objects.filter(reversal_of=original.movement).exists()):
                raise ValidationError("الاستلام التعويضي يتطلب استلامًا أصليًا معكوسًا لأمر مكتمل.")
        if order.product.base_uom_id != order.base_unit_snapshot_id:
            raise ValidationError("تغيرت وحدة المنتج الأساسية بعد إطلاق الأمر؛ راجع بيانات الصنف قبل الاستلام.")
        if FinishedGoodsReceipt.objects.filter(reference=reference.strip()).exists():
            raise ValidationError({"reference": "مرجع استلام المنتج مستخدم من قبل."})
        quantity_base = to_base_quantity(order.product, quantity, unit)
        good_total = order.results.aggregate(total=Sum("good_quantity_base"))["total"] or Decimal("0")
        already_received = order.finished_receipts.filter(movement__reversal__isnull=True).aggregate(
            total=Sum("quantity_base")
        )["total"] or Decimal("0")
        if already_received + quantity_base > good_total:
            raise ValidationError({"quantity": "الكمية المستلمة تتجاوز الإنتاج الجيد غير المستلم."})
        if replacement_for and quantity_base > original.quantity_base:
            raise ValidationError({"quantity": "الاستلام التعويضي لا يتجاوز كمية الاستلام الأصلي المعكوس."})
        if replacement_for:
            replacements = FinishedGoodsReceipt.objects.filter(
                replacement_for=original, movement__reversal__isnull=True
            ).aggregate(total=Sum("quantity_base"))["total"] or Decimal("0")
            if replacements + quantity_base > original.quantity_base:
                raise ValidationError({"quantity": "إجمالي التعويضات يتجاوز كمية الاستلام الأصلي المعكوس."})
        receipt_id = uuid.uuid4()
        movement = post_movement(
            reference=f"MFG-R-{receipt_id.hex}", movement_type=StockMovement.Type.RECEIPT,
            product=order.product, quantity=quantity, unit=unit, actor=actor,
            destination_location=destination_location, external_reference=reference.strip(),
            source_reference_type="manufacturing.finished_receipt", source_reference_id=str(receipt_id),
            reason=f"استلام إنتاج أمر {order.order_number}",
        )
        try:
            with transaction.atomic():
                receipt = FinishedGoodsReceipt.objects.create(
                    id=receipt_id, order=order, reference=reference.strip(), movement=movement,
                    quantity=quantity, unit=unit, quantity_base=quantity_base,
                    destination_location=destination_location, actor=actor, replacement_for=replacement_for,
                )
        except IntegrityError as exc:
            cause = exc.__cause__
            constraint = getattr(getattr(cause, "diag", None), "constraint_name", "")
            if constraint == "manufacturing_finishedgoodsreceipt_reference_key" or "UNIQUE constraint failed: manufacturing_finishedgoodsreceipt.reference" in str(cause):
                raise ValidationError({"reference": "مرجع استلام المنتج مستخدم من قبل."}) from exc
            raise
        if order.status == ProductionOrder.Status.RELEASED:
            order.status = ProductionOrder.Status.IN_PROGRESS
            order.save(update_fields=["status", "updated_at"])
        return receipt


def complete_order(*, order, actor):
    _staff(actor)
    with transaction.atomic():
        order = ProductionOrder.objects.select_for_update().get(pk=order.pk)
        if order.status not in ACTIVE_ORDER_STATUSES:
            raise ValidationError("يمكن إكمال أمر مطلق أو قيد التنفيذ فقط.")
        totals = order.results.aggregate(good=Sum("good_quantity_base"), scrap=Sum("scrap_quantity_base"))
        good = totals["good"] or Decimal("0")
        scrap = totals["scrap"] or Decimal("0")
        if good + scrap <= 0:
            raise ValidationError("لا يمكن إكمال أمر دون تسجيل إنتاج جيد أو هالك.")
        received = order.finished_receipts.filter(movement__reversal__isnull=True).aggregate(
            total=Sum("quantity_base")
        )["total"] or Decimal("0")
        if received != good:
            raise ValidationError("استلم كامل الإنتاج الجيد أو صحح سجل الإنتاج قبل إكمال الأمر.")
        if order.operations.exclude(status__in=(ProductionOrderOperation.Status.COMPLETED, ProductionOrderOperation.Status.SKIPPED)).exists():
            raise ValidationError("أكمل عمليات المسار أو تجاوز العمليات غير المطلوبة قبل إغلاق الأمر.")
        order.status = ProductionOrder.Status.COMPLETED
        order.completed_at = timezone.now()
        order.save(update_fields=["status", "completed_at", "updated_at"])
        return order


def cancel_order(*, order, actor):
    _staff(actor)
    with transaction.atomic():
        order = ProductionOrder.objects.select_for_update().get(pk=order.pk)
        if order.status in (ProductionOrder.Status.COMPLETED, ProductionOrder.Status.CANCELLED):
            raise ValidationError("لا يمكن إلغاء أمر مكتمل أو ملغي.")
        order.status = ProductionOrder.Status.CANCELLED
        order.cancelled_at = timezone.now()
        order.save(update_fields=["status", "cancelled_at", "updated_at"])
        return order


def order_summary(order):
    good = order.results.aggregate(total=Sum("good_quantity_base"))["total"] or Decimal("0")
    scrap = order.results.aggregate(total=Sum("scrap_quantity_base"))["total"] or Decimal("0")
    received = order.finished_receipts.filter(movement__reversal__isnull=True).aggregate(
        total=Sum("quantity_base")
    )["total"] or Decimal("0")
    total = good + scrap
    return {
        "good": good,
        "scrap": scrap,
        "received": received,
        "remaining_production": max(Decimal("0"), order.planned_quantity_base - total),
        "yield_percent": (good * Decimal("100") / total) if total else None,
        "costing_configured": False,
    }
