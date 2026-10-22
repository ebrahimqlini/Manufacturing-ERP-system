from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext
import uuid

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from inventory.models import StockMovement
from inventory.services import post_movement, to_base_quantity

from .models import (
    CENT,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseReceipt,
    PurchaseReceiptLine,
)

from core.permissions import require_permission, require_staff


def _staff(actor):
    require_staff(actor)


def _purchase_order_number():
    return f"PO-{date.today():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"


def _money(value):
    with localcontext() as context:
        context.prec = 80
        return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _received_map(order):
    rows = (
        PurchaseReceiptLine.objects.filter(
            order_line__order=order, movement__reversal__isnull=True
        )
        .values("order_line_id")
        .annotate(total=Sum("quantity_base"))
    )
    return {row["order_line_id"]: row["total"] for row in rows}


def save_draft_order(*, actor, lines, order=None, **values):
    """Create or replace a draft purchase order and its lines atomically.

    Only confirmed documents are locked; draft lines may be replaced. A
    confirmed/cancelled order is never edited by this service.
    """
    require_permission(actor, "purchasing.confirm_purchase_order", "حفظ أمر شراء")
    require_staff(actor)
    if not lines:
        raise ValidationError("أضف بندًا واحدًا على الأقل لأمر الشراء.")

    with transaction.atomic():
        if order is None:
            order = PurchaseOrder(created_by=actor, order_number=_purchase_order_number())
        else:
            order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
            if order.status != PurchaseOrder.Status.DRAFT:
                raise ValidationError("لا يمكن تعديل أمر شراء بعد تأكيده أو إلغائه.")

        for field, value in values.items():
            setattr(order, field, value)
        order.full_clean()
        order.save()

        order.lines.all().delete()
        for index, payload in enumerate(lines, start=1):
            if not payload.get("material") or not payload.get("unit"):
                raise ValidationError("كل بند يحتاج مادة ووحدة.")
            line = PurchaseOrderLine(
                order=order,
                material=payload["material"],
                quantity=payload["quantity"],
                unit=payload["unit"],
                unit_price=payload["unit_price"],
                notes=(payload.get("notes") or "").strip(),
                sequence=index * 10,
            )
            line.save()
        return order


def confirm_order(*, order, actor):
    require_permission(actor, "purchasing.confirm_purchase_order", "تأكيد أمر الشراء")
    require_staff(actor)
    with transaction.atomic():
        order = PurchaseOrder.objects.select_for_update().select_related("supplier").get(pk=order.pk)
        if order.status != PurchaseOrder.Status.DRAFT:
            raise ValidationError("يمكن تأكيد أوامر الشراء التي ما زالت مسودة فقط.")
        if not order.supplier.active:
            raise ValidationError("لا يمكن تأكيد أمر شراء لمورد غير نشط.")
        lines = list(
            order.lines.select_related("material", "unit", "material__base_uom").order_by("sequence", "pk")
        )
        if not lines:
            raise ValidationError("أضف بندًا واحدًا على الأقل قبل تأكيد أمر الشراء.")
        for line in lines:
            line.full_clean()
        order.status = PurchaseOrder.Status.CONFIRMED
        order.confirmed_at = timezone.now()
        order.confirmed_by = actor
        order.save(update_fields=["status", "confirmed_at", "confirmed_by", "updated_at"])
        return order


def cancel_order(*, order, actor, reason):
    require_permission(actor, "purchasing.cancel_purchase_order", "إلغاء أمر الشراء")
    require_staff(actor)
    if not reason or not reason.strip():
        raise ValidationError({"reason": "سبب إلغاء أمر الشراء مطلوب."})
    with transaction.atomic():
        order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
        if order.status not in (PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.CONFIRMED):
            raise ValidationError("لا يمكن إلغاء أمر شراء بهذه الحالة.")
        # Any receipt document keeps the order open for audit, even if its
        # stock movement was later reversed. Received goods are not erased.
        if order.receipts.exists():
            raise ValidationError("لا يمكن إلغاء أمر شراء له استلامات مسجلة؛ أكمل الاستلام أو عالج الحركة بعكس مخزني.")
        order.status = PurchaseOrder.Status.CANCELLED
        order.cancelled_at = timezone.now()
        order.cancelled_by = actor
        order.cancel_reason = reason.strip()
        order.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason", "updated_at"])
        return order


def create_receipt(*, order, actor, reference, received_date, destination_location, lines,
                   supplier_reference="", notes=""):
    """Post one or more receipt lines through the shared inventory ledger.

    The receipt, its lines, and every stock movement are created in one
    transaction. Quantities are checked against the net received quantity
    (reversed receipts excluded), so duplicate or over-receipt cannot pass.
    """
    require_permission(actor, "purchasing.create_receipt", "تسجيل استلام مشتريات")
    require_staff(actor)
    reference = (reference or "").strip()
    if not reference:
        raise ValidationError({"reference": "مرجع الاستلام مطلوب لمنع التكرار."})
    if not lines:
        raise ValidationError("أدخل كمية مستلمة واحدة على الأقل.")
    if destination_location is None:
        raise ValidationError({"destination_location": "موقع الإيداع مطلوب."})

    with transaction.atomic():
        order = PurchaseOrder.objects.select_for_update().select_related("supplier").get(pk=order.pk)
        if order.status != PurchaseOrder.Status.CONFIRMED:
            raise ValidationError("لا يمكن الاستلام إلا لأمر شراء مؤكد.")
        if not destination_location.active or not destination_location.warehouse.active:
            raise ValidationError({"destination_location": "اختر موقع تخزين نشطًا داخل مخزن نشط."})
        if PurchaseReceipt.objects.filter(reference=reference).exists():
            raise ValidationError({"reference": "مرجع الاستلام مستخدم من قبل."})

        order_lines = {
            line.pk: line
            for line in PurchaseOrderLine.objects.select_for_update()
            .filter(order=order)
            .select_related("material", "material__base_uom", "unit")
        }
        received_map = _received_map(order)
        normalized = []
        seen_lines = set()
        for payload in lines:
            order_line = payload.get("order_line")
            if isinstance(order_line, int):
                order_line = order_lines.get(order_line)
            if order_line is None or order_line.pk not in order_lines:
                raise ValidationError("بند أمر الشراء غير موجود أو لا يخص هذا الأمر.")
            if order_line.pk in seen_lines:
                raise ValidationError("لا تكرر بند أمر الشراء في الاستلام نفسه.")
            seen_lines.add(order_line.pk)

            quantity = Decimal(payload.get("quantity"))
            if quantity <= 0:
                raise ValidationError({"quantity": "الكمية المستلمة يجب أن تكون أكبر من صفر."})
            unit = payload.get("unit")
            if unit is None or not unit.active:
                raise ValidationError({"unit": "اختر وحدة استلام نشطة."})
            if not order_line.material.active:
                raise ValidationError({"material": f"المادة {order_line.material.sku} غير نشطة؛ لا يمكن استلامها."})
            quantity_base = to_base_quantity(order_line.material, quantity, unit)
            already_received = received_map.get(order_line.pk, Decimal("0"))
            if already_received + quantity_base > order_line.quantity_base:
                outstanding = order_line.quantity_base - already_received
                raise ValidationError(
                    {"quantity": f"الكمية المستلمة تتجاوز المتبقي في البند {order_line.material.sku}: المتبقي {outstanding}."}
                )
            normalized.append({
                "order_line": order_line,
                "quantity": quantity,
                "unit": unit,
                "quantity_base": quantity_base,
            })

        receipt_id = uuid.uuid4()
        receipt = PurchaseReceipt(
            id=receipt_id,
            order=order,
            reference=reference,
            supplier_reference=(supplier_reference or "").strip(),
            received_date=received_date or date.today(),
            destination_location=destination_location,
            notes=(notes or "").strip(),
            actor=actor,
        )
        try:
            receipt.save()
        except IntegrityError as exc:
            raise ValidationError({"reference": "مرجع الاستلام مستخدم من قبل."}) from exc

        for index, payload in enumerate(normalized, start=1):
            order_line = payload["order_line"]
            movement = post_movement(
                reference=f"PUR-R-{receipt_id.hex}-{index}",
                movement_type=StockMovement.Type.RECEIPT,
                product=order_line.material,
                quantity=payload["quantity"],
                unit=payload["unit"],
                actor=actor,
                destination_location=destination_location,
                external_reference=reference,
                source_reference_type="purchasing.purchase_receipt",
                source_reference_id=f"{receipt_id.hex}:{order_line.pk}",
                reason=f"استلام أمر شراء {order.order_number} من {order.supplier.name_ar}",
            )
            receipt_line = PurchaseReceiptLine(
                receipt=receipt,
                order_line=order_line,
                movement=movement,
                quantity=payload["quantity"],
                unit=payload["unit"],
                quantity_base=movement.base_quantity,
                unit_price=order_line.unit_price,
                price_per_base_unit=order_line.price_per_base_unit,
                line_total=_money(movement.base_quantity * order_line.price_per_base_unit),
                sequence=index,
            )
            receipt_line.save()
        return receipt
