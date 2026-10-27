"""Manual machine-counter readings (workers read the physical counter).

No device connectivity or polling exists by design: a worker enters the start
and end values shown on the machine. A reading is a measurement interval only —
it never posts stock movements and never feeds costing or profit directly, so
``ProductionResult`` stays the authoritative production record and editing a
later reading can never silently change an accepted or finalized period.

Corrections are never silent: the service requires a reason, archives the
superseded values in the reading's ``corrections`` audit list, and returns the
reading to a not-approved state so a supervisor can re-approve it.
"""

from decimal import Decimal, InvalidOperation, localcontext

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from catalog.models import ProductUnitConversion
from core.permissions import require_permission, require_staff
from inventory.services import to_base_quantity

from .models import (
    MachineCounterReading,
    ProductionLine,
    ProductionOrder,
)

STORAGE_SCALE = Decimal("0.000000000001")

ACTIVE_ORDER_STATUSES = (
    ProductionOrder.Status.RELEASED,
    ProductionOrder.Status.IN_PROGRESS,
)
# A corrected reading must be re-approved before it counts as approved again.
APPROVABLE_STATUSES = (
    MachineCounterReading.Status.COMPLETE,
    MachineCounterReading.Status.CORRECTED,
)


def _validate_convertible_unit(product, unit):
    """Reject units that cannot be converted to the order product's base UoM.

    Mirrors ``to_base_quantity``'s dimension/activation rules without needing a
    quantity (a counter reading may legitimately be zero at start).
    """
    if unit is None or not unit.pk or not unit.active:
        raise ValidationError({"unit": "اختر وحدة قياس نشطة للعدّ."})
    if unit.pk == product.base_uom_id:
        return
    if unit.dimension != product.base_uom.dimension:
        raise ValidationError(
            {"unit": "لا يمكن التحويل بين وحدات ذات أبعاد مختلفة؛ العدّاد لا يقيس بهذه الوحدة."}
        )
    if not ProductUnitConversion.objects.filter(product=product, unit=unit, active=True).exists():
        raise ValidationError({"unit": "لا يوجد تحويل نشط لهذه الوحدة على صنف أمر الإنتاج."})


def _base_or_zero(product, quantity, unit):
    """Convert to base units; zero converts to zero (to_base_quantity rejects ≤ 0).

    A 4-dp counter value times a 12-dp conversion factor can produce up to 16
    decimal digits, beyond the 12-dp storage precision. Quantizing to storage
    precision here is an exactness-preserving normalization (trailing zeros
    drop away), while any conversion whose value is not representable at 12 dp
    is rejected instead of silently rounded — the same protection inventory
    quantities get.
    """
    if quantity is None:
        return None
    quantity = Decimal(quantity)
    if quantity == 0:
        return Decimal("0")
    converted = to_base_quantity(product, quantity, unit)
    with localcontext() as context:
        context.prec = 80
        try:
            rounded = converted.quantize(STORAGE_SCALE)
        except InvalidOperation as exc:
            raise ValidationError(
                {"unit": "ناتج تحويل قراءة العدّاد يتجاوز دقة التخزين؛ لم يتم تقريبه."}
            ) from exc
    if rounded != converted:
        raise ValidationError(
            {"unit": "ناتج تحويل قراءة العدّاد لا يمكن تمثيله بدقة التخزين؛ لم يتم تقريبه."}
        )
    return rounded


def _reject_overlaps(*, production_line, order, started_at):
    """Reject overlapping intervals on the same line or the same order.

    An interval still open (no end) always conflicts. A closed interval
    conflicts when it ends at or after the new start: on one line production is
    sequential, so the next interval may only begin after the previous one ends.
    """
    conflict = (
        MachineCounterReading.objects.filter(Q(production_line=production_line) | Q(order=order))
        .filter(Q(ended_at__isnull=True) | Q(ended_at__gte=started_at))
        .order_by("-started_at")
        .first()
    )
    if conflict is not None:
        raise ValidationError(
            f"توجد قراءة عدّاد متداخلة على نفس الخط/الأمر "
            f"(بدأت {conflict.started_at:%Y-%m-%d %H:%M})؛ أنهِها أو اختر وقتًا غير متداخل."
        )


def _persist(reading):
    reading._allow_service_write = True
    try:
        reading.full_clean()
        reading.save()
    finally:
        # Never leak the bypass flag to callers holding the instance.
        reading.__dict__.pop("_allow_service_write", None)
    return reading


def start_counter_interval(
    *,
    actor,
    order,
    production_line,
    unit,
    start_reading,
    started_at=None,
    order_operation=None,
    material_issue=None,
    roll_reference="",
    notes="",
    continuation_of=None,
    reset_reason="",
):
    """Open a new counter interval on an active order's own production line."""
    require_permission(actor, "manufacturing.record_counter_reading", "بدء قراءة عدّاد")
    require_staff(actor)
    started_at = started_at or timezone.now()
    start_reading = Decimal(start_reading)
    if start_reading < 0:
        raise ValidationError({"start_reading": "قراءة العدّاد لا تقل عن صفر."})
    with transaction.atomic():
        order = (
            ProductionOrder.objects.select_for_update(of=("self",))
            .select_related("product", "production_line")
            .get(pk=order.pk)
        )
        if order.status not in ACTIVE_ORDER_STATUSES:
            raise ValidationError(
                "يمكن تسجيل قراءات العدّاد لأوامر الإنتاج المطلقة أو قيد التنفيذ فقط."
            )
        # All starts on a line share this lock before checking for overlaps.
        production_line = ProductionLine.objects.select_for_update().get(pk=production_line.pk)
        if not production_line.active:
            raise ValidationError("خط الإنتاج غير نشط؛ لا تُسجل قراءة عليه.")
        if production_line.pk != order.production_line_id:
            raise ValidationError(
                "خط الإنتاج المحدد لا يخص أمر الإنتاج؛ تُسجل قراءة العدّاد على خط الأمر فقط."
            )
        if order_operation is not None and order_operation.order_id != order.pk:
            raise ValidationError("مرحلة الإنتاج المحددة لا تنتمي لهذا الأمر.")
        if material_issue is not None and material_issue.order_id != order.pk:
            raise ValidationError("صرف المادة المحدد لا ينتمي لهذا الأمر.")
        _validate_convertible_unit(order.product, unit)
        _reject_overlaps(
            production_line=production_line, order=order, started_at=started_at
        )
        if continuation_of is not None:
            # Re-fetch under the row lock: the caller's instance may be stale
            # (e.g. closed through the service after the caller loaded it).
            continuation_of = MachineCounterReading.objects.select_for_update().get(
                pk=continuation_of.pk
            )
        # A counter that restarts below the last closed reading is only
        # acceptable when the worker documents the reset as a continuation.
        last_closed = (
            MachineCounterReading.objects.filter(
                production_line=production_line, end_reading__isnull=False
            )
            .order_by("-ended_at", "-pk")
            .first()
        )
        backwards = last_closed is not None and start_reading < last_closed.end_reading
        if backwards and (continuation_of is None or not (reset_reason or "").strip()):
            raise ValidationError(
                "العدّاد بدأ من رقم أقل من آخر قراءة مغلقة على هذا الخط؛ "
                "يجب توثيق الاستمرار: القراءة السابقة وسبب إعادة الضبط."
            )
        if continuation_of is not None:
            # The counter belongs to the machine (line), not the order: a
            # documented reset may continue from another order's reading on
            # this same line.
            if continuation_of.production_line_id != production_line.pk:
                raise ValidationError("القراءة السابقة التي تُتابعها لا تخص هذا الخط.")
            if not continuation_of.is_closed:
                raise ValidationError("لا يمكن متابعة قراءة لم تُغلق بعد.")
            if not (reset_reason or "").strip():
                raise ValidationError("سبب إعادة الضبط إلزامي عند متابعة قراءة سابقة.")
        reading = MachineCounterReading(
            order=order,
            order_operation=order_operation,
            production_line=production_line,
            material_issue=material_issue,
            roll_reference=(roll_reference or "").strip(),
            unit=unit,
            start_reading=start_reading,
            started_at=started_at,
            notes=(notes or "").strip(),
            entered_by=actor,
            continuation_of=continuation_of,
            reset_reason=(reset_reason or "").strip(),
        )
        return _persist(reading)


def close_counter_interval(*, reading, actor, end_reading, ended_at=None, rejected_quantity=None):
    """Close an open interval; output and rejection are converted exactly.

    The counter output is ``end - start``; the rejected quantity (when the
    counter does not exclude it automatically) may never exceed that output, so
    the accepted counter-good amount can never go negative.
    """
    require_permission(actor, "manufacturing.record_counter_reading", "إغلاق قراءة عدّاد")
    require_staff(actor)
    ended_at = ended_at or timezone.now()
    end_reading = Decimal(end_reading)
    with transaction.atomic():
        reading = (
            MachineCounterReading.objects.select_for_update()
            .select_related("order", "order__product")
            .get(pk=reading.pk)
        )
        if reading.status != MachineCounterReading.Status.PROVISIONAL:
            raise ValidationError(
                "القراءة مغلقة بالفعل؛ لتعديل قراءة مغلقة استخدم خدمة التصحيح الموثّقة."
            )
        if end_reading < reading.start_reading:
            raise ValidationError(
                {"end_reading": "قراءة النهاية لا تقل عن قراءة البداية؛ لا يُقبل عدّاد سالب."}
            )
        counter_output = end_reading - reading.start_reading
        if rejected_quantity is not None:
            rejected_quantity = Decimal(rejected_quantity)
            if rejected_quantity < 0:
                raise ValidationError(
                    {"rejected_quantity": "الكمية المرفوضة لا تقل عن صفر."}
                )
            if rejected_quantity > counter_output:
                raise ValidationError(
                    {
                        "rejected_quantity": (
                            "الكمية المرفوضة لا تتجاوز ناتج العدّاد؛ "
                            "الناتج المقبول لا يقل عن الصفر."
                        )
                    }
                )
        product = reading.order.product
        reading.end_reading = end_reading
        reading.ended_at = ended_at
        reading.counter_output = counter_output
        reading.counter_output_base = _base_or_zero(product, counter_output, reading.unit)
        reading.rejected_quantity = rejected_quantity
        reading.rejected_quantity_base = _base_or_zero(
            product, rejected_quantity, reading.unit
        )
        reading.closed_by = actor
        reading.closed_at = timezone.now()
        reading.status = MachineCounterReading.Status.COMPLETE
        return _persist(reading)


def approve_counter_reading(*, reading, actor):
    """Approve a complete (or corrected) reading so reports may rely on it."""
    require_permission(actor, "manufacturing.approve_counter_reading", "اعتماد قراءة العدّاد")
    require_staff(actor)
    with transaction.atomic():
        reading = MachineCounterReading.objects.select_for_update().get(pk=reading.pk)
        if reading.status not in APPROVABLE_STATUSES:
            raise ValidationError(
                "يمكن اعتماد القراءات المكتملة أو المصححة فقط؛ أغلق القراءة أولاً."
            )
        reading.status = MachineCounterReading.Status.APPROVED
        reading.approved_by = actor
        reading.approved_at = timezone.now()
        return _persist(reading)


def correct_counter_reading(
    *,
    reading,
    actor,
    reason,
    start_reading=None,
    end_reading=None,
    rejected_quantity=None,
    started_at=None,
    ended_at=None,
    roll_reference=None,
    notes=None,
):
    """Audited correction: reason mandatory, superseded values archived.

    A closed reading returns to ``CORRECTED`` and must be re-approved before it
    counts as approved again; an open (provisional) reading stays open with the
    corrected start values. Approved readings are re-openable for correction —
    never silently edited — and the audit list grows by one entry per change.
    """
    require_permission(actor, "manufacturing.correct_counter_reading", "تصحيح قراءة العدّاد")
    require_staff(actor)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "سبب التصحيح إلزامي حفظًا لسجل التدقيق."})
    with transaction.atomic():
        reading = (
            MachineCounterReading.objects.select_for_update()
            .select_related("order", "order__product")
            .get(pk=reading.pk)
        )
        previous_status = reading.status
        was_closed = reading.is_closed
        corrections = list(reading.corrections or [])
        snapshot = {
            "corrected_at": timezone.now().isoformat(),
            "corrected_by": getattr(actor, "username", None) or str(actor.pk),
            "reason": reason,
            "status_before": previous_status,
            "previous": {
                "start_reading": str(reading.start_reading),
                "end_reading": (
                    str(reading.end_reading) if reading.end_reading is not None else None
                ),
                "counter_output": (
                    str(reading.counter_output)
                    if reading.counter_output is not None
                    else None
                ),
                "counter_output_base": (
                    str(reading.counter_output_base)
                    if reading.counter_output_base is not None
                    else None
                ),
                "rejected_quantity": (
                    str(reading.rejected_quantity)
                    if reading.rejected_quantity is not None
                    else None
                ),
                "rejected_quantity_base": (
                    str(reading.rejected_quantity_base)
                    if reading.rejected_quantity_base is not None
                    else None
                ),
                "started_at": reading.started_at.isoformat(),
                "ended_at": reading.ended_at.isoformat() if reading.ended_at else None,
                "roll_reference": reading.roll_reference,
                "notes": reading.notes,
            },
        }
        if start_reading is not None:
            start_reading = Decimal(start_reading)
            if start_reading < 0:
                raise ValidationError({"start_reading": "قراءة العدّاد لا تقل عن صفر."})
            reading.start_reading = start_reading
        if end_reading is not None:
            end_reading = Decimal(end_reading)
            if reading.start_reading is not None and end_reading < reading.start_reading:
                raise ValidationError(
                    {"end_reading": "قراءة النهاية لا تقل عن قراءة البداية؛ لا يُقبل عدّاد سالب."}
                )
            reading.end_reading = end_reading
            if reading.ended_at is None:
                reading.ended_at = timezone.now()
        if rejected_quantity is not None:
            rejected_quantity = Decimal(rejected_quantity)
            if rejected_quantity < 0:
                raise ValidationError({"rejected_quantity": "الكمية المرفوضة لا تقل عن صفر."})
            reading.rejected_quantity = rejected_quantity
        if started_at is not None:
            reading.started_at = started_at
        if ended_at is not None:
            reading.ended_at = ended_at
        if roll_reference is not None:
            reading.roll_reference = roll_reference.strip()
        if notes is not None:
            reading.notes = notes.strip()
        # Recompute derived values whenever a closed reading's inputs changed.
        if reading.is_closed:
            product = reading.order.product
            counter_output = reading.end_reading - reading.start_reading
            if reading.rejected_quantity is not None and reading.rejected_quantity > counter_output:
                raise ValidationError(
                    {
                        "rejected_quantity": (
                            "الكمية المرفوضة بعد التصحيح لا تتجاوز ناتج العدّاد؛ "
                            "الناتج المقبول لا يقل عن الصفر."
                        )
                    }
                )
            reading.counter_output = counter_output
            reading.counter_output_base = _base_or_zero(product, counter_output, reading.unit)
            reading.rejected_quantity_base = _base_or_zero(
                product, reading.rejected_quantity, reading.unit
            )
        reading.corrections = [*corrections, snapshot]
        reading.correction_reason = reason
        reading.corrected_by = actor
        reading.corrected_at = snapshot["corrected_at"]
        # Only a closed reading loses its approval; an open one stays open.
        if was_closed:
            reading.status = MachineCounterReading.Status.CORRECTED
        return _persist(reading)
