from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import uuid

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from inventory.models import StockMovement
from inventory.services import post_movement, to_base_quantity

from .models import (
    CENT,
    SalesOrder,
    SalesOrderLine,
    Shipment,
    ShipmentLine,
)


def _staff(actor):
    if not actor or not actor.is_authenticated or not actor.is_staff:
        raise ValidationError("هذا الإجراء متاح لمستخدمي النظام الداخليين فقط.")


def _sales_order_number():
    return f"SO-{date.today():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"


def _money(value):
    with localcontext() as context:
        context.prec = 80
        return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _shipped_map(order):
    rows = (
        ShipmentLine.objects.filter(
            order_line__order=order, movement__reversal__isnull=True
        )
        .values("order_line_id")
        .annotate(total=Sum("quantity_base"))
    )
    return {row["order_line_id"]: row["total"] for row in rows}


def _create_draft_order(*, actor, lines, order=None, customer_submitted=False, **values):
    """Shared atomic draft-order writer.

    Callers must enforce their own role policy before using this helper. Staff
    edits use ``save_draft_order``; customer API requests use
    ``submit_customer_order_request`` with a fixed customer and zero-price
    request lines.
    """
    if not lines:
        raise ValidationError("أضف بندًا واحدًا على الأقل لأمر البيع.")

    with transaction.atomic():
        if order is None:
            order = SalesOrder(
                created_by=actor,
                order_number=_sales_order_number(),
                customer_submitted=customer_submitted,
            )
        else:
            order = SalesOrder.objects.select_for_update().get(pk=order.pk)
            if order.status != SalesOrder.Status.DRAFT:
                raise ValidationError("لا يمكن تعديل أمر بيع بعد تأكيده أو إلغائه.")

        for field, value in values.items():
            setattr(order, field, value)
        order.full_clean()
        order.save()

        order.lines.all().delete()
        for index, payload in enumerate(lines, start=1):
            if not payload.get("product") or not payload.get("unit"):
                raise ValidationError("كل بند يحتاج صنفًا ووحدة.")
            line = SalesOrderLine(
                order=order,
                product=payload["product"],
                quantity=payload["quantity"],
                unit=payload["unit"],
                unit_price=payload["unit_price"],
                notes=(payload.get("notes") or "").strip(),
                sequence=index * 10,
            )
            line.save()
        return order


def save_draft_order(*, actor, lines, order=None, **values):
    """Create or replace a draft sales order and its lines atomically.

    Only confirmed documents are locked; draft lines may be replaced. A
    confirmed, completed, or cancelled order is never edited by this service.
    This staff-facing entry point keeps the existing web permission boundary.
    """
    _staff(actor)
    return _create_draft_order(actor=actor, lines=lines, order=order, **values)


CUSTOMER_ORDER_FIELDS = {"requested_delivery_date", "customer_reference", "notes"}
CUSTOMER_ORDER_LINE_FIELDS = {"product", "quantity", "unit", "notes"}


def submit_customer_order_request(*, actor, lines, **values):
    """Create a customer's draft order request without any stock or sales side effect.

    The authenticated non-staff user must be linked to an active customer. The
    customer and submitter are never accepted from the request payload. Lines
    are request quantities only: prices start at zero pending staff review, and
    the sales service does not confirm, reserve, ship, or issue inventory.
    """
    if (
        not actor
        or not actor.is_authenticated
        or not getattr(actor, "is_active", False)
        or getattr(actor, "is_staff", False)
    ):
        raise ValidationError("هذا الإجراء متاح لحسابات العملاء فقط.")
    try:
        customer = actor.customer_account
    except ObjectDoesNotExist as exc:
        raise ValidationError("لا يوجد حساب عميل مرتبط بهذا المستخدم.") from exc
    if not customer.active:
        raise ValidationError("حساب العميل غير نشط.")
    if not lines:
        raise ValidationError("أضف بندًا واحدًا على الأقل لطلب أمر البيع.")
    if len(lines) > 50:
        raise ValidationError("لا يمكن إرسال أكثر من 50 بندًا في الطلب الواحد.")
    unexpected = set(values) - CUSTOMER_ORDER_FIELDS
    if unexpected:
        raise ValidationError("حقول الطلب غير مسموح بها للعميل.")

    normalized = []
    for payload in lines:
        unexpected_line = set(payload) - CUSTOMER_ORDER_LINE_FIELDS
        if unexpected_line:
            raise ValidationError("حقول البند غير مسموح بها للعميل.")
        product = payload.get("product")
        unit = payload.get("unit")
        if not product or not unit:
            raise ValidationError("كل بند يحتاج صنفًا ووحدة.")
        if not product.active or not product.available_to_customers:
            raise ValidationError({"product": "الصنف غير متاح لطلبات العملاء."})
        if not unit.active:
            raise ValidationError({"unit": "اختر وحدة نشطة."})
        normalized.append({
            "product": product,
            "quantity": payload["quantity"],
            "unit": unit,
            "unit_price": Decimal("0"),
            "notes": payload.get("notes") or "",
        })

    order_date = date.today()
    requested_delivery_date = values.get("requested_delivery_date")
    if requested_delivery_date and requested_delivery_date < order_date:
        raise ValidationError({"requested_delivery_date": "تاريخ التسليم المطلوب لا يسبق تاريخ الطلب."})

    return _create_draft_order(
        actor=actor,
        lines=normalized,
        customer=customer,
        order_date=order_date,
        customer_reference=(values.get("customer_reference") or "").strip(),
        notes=(values.get("notes") or "").strip(),
        requested_delivery_date=requested_delivery_date,
        customer_submitted=True,
    )


def confirm_order(*, order, actor):
    _staff(actor)
    with transaction.atomic():
        order = SalesOrder.objects.select_for_update().select_related("customer").get(pk=order.pk)
        if order.status != SalesOrder.Status.DRAFT:
            raise ValidationError("يمكن تأكيد أوامر البيع التي ما زالت مسودة فقط.")
        if not order.customer.active:
            raise ValidationError("لا يمكن تأكيد أمر بيع لعميل غير نشط.")
        lines = list(
            order.lines.select_related("product", "unit", "product__base_uom").order_by("sequence", "pk")
        )
        if not lines:
            raise ValidationError("أضف بندًا واحدًا على الأقل قبل تأكيد أمر البيع.")
        if order.customer_submitted and any(line.unit_price <= 0 for line in lines):
            raise ValidationError(
                "لا يمكن تأكيد طلب عميل قبل مراجعة الأسعار وتحديد سعر موجب لكل بند."
            )
        for line in lines:
            line.full_clean()
        order.status = SalesOrder.Status.CONFIRMED
        order.confirmed_at = timezone.now()
        order.confirmed_by = actor
        order.save(update_fields=["status", "confirmed_at", "confirmed_by", "updated_at"])
        return order


def cancel_order(*, order, actor, reason):
    _staff(actor)
    if not reason or not reason.strip():
        raise ValidationError({"reason": "سبب إلغاء أمر البيع مطلوب."})
    with transaction.atomic():
        order = SalesOrder.objects.select_for_update().get(pk=order.pk)
        if order.status not in (SalesOrder.Status.DRAFT, SalesOrder.Status.CONFIRMED):
            raise ValidationError("لا يمكن إلغاء أمر بيع بهذه الحالة.")
        # Any shipment document keeps the order open for audit, even if its
        # stock movement was later reversed. Shipped goods are not erased.
        if order.shipments.exists():
            raise ValidationError("لا يمكن إلغاء أمر بيع له شحنات مسجلة؛ أكمل التسليم أو عالج الحركة بعكس مخزني.")
        order.status = SalesOrder.Status.CANCELLED
        order.cancelled_at = timezone.now()
        order.cancelled_by = actor
        order.cancel_reason = reason.strip()
        order.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason", "updated_at"])
        return order


def complete_order(*, order, actor):
    _staff(actor)
    with transaction.atomic():
        order = SalesOrder.objects.select_for_update().get(pk=order.pk)
        if order.status != SalesOrder.Status.CONFIRMED:
            raise ValidationError("يمكن إكمال أمر بيع مؤكد فقط.")
        if not order.shipments.exists():
            raise ValidationError("سجّل شحنًا واحدًا على الأقل قبل إكمال أمر البيع.")
        if order.shipment_state != SalesOrder.ShipmentState.SHIPPED:
            raise ValidationError("اشحن كامل كميات أمر البيع قبل إكماله.")
        order.status = SalesOrder.Status.COMPLETED
        order.completed_at = timezone.now()
        order.completed_by = actor
        order.save(update_fields=["status", "completed_at", "completed_by", "updated_at"])
        return order


def reopen_completed_order(*, order, actor, reason):
    """Reopen a completed order only when a shipment reversal left quantity outstanding.

    The completed order remains the historical record; reopening returns it to
    CONFIRMED so the outstanding quantity can be shipped again and completed
    again. The reason is mandatory and stored with actor/time for audit.
    """
    _staff(actor)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "سبب إعادة فتح أمر البيع مطلوب."})
    max_reason_length = SalesOrder._meta.get_field("reopen_reason").max_length
    if max_reason_length and len(reason) > max_reason_length:
        raise ValidationError(
            {"reason": f"سبب إعادة فتح أمر البيع يجب ألا يتجاوز {max_reason_length} حرفًا."}
        )
    with transaction.atomic():
        order = SalesOrder.objects.select_for_update().get(pk=order.pk)
        if order.status != SalesOrder.Status.COMPLETED:
            raise ValidationError("يمكن إعادة فتح أمر بيع مكتمل فقط.")
        if order.outstanding_quantity_base <= 0:
            raise ValidationError("لا توجد كمية معلقة بعد عكس الشحن؛ لا حاجة لإعادة فتح الأمر.")
        order.status = SalesOrder.Status.CONFIRMED
        order.reopened_at = timezone.now()
        order.reopened_by = actor
        order.reopen_reason = reason
        order.save(update_fields=["status", "reopened_at", "reopened_by", "reopen_reason", "updated_at"])
        return order


def create_shipment(*, order, actor, reference, shipment_date, source_location, lines,
                    customer_reference="", notes=""):
    """Post one or more shipment lines through the shared inventory ledger.

    The shipment, its lines, and every stock movement are created in one
    transaction. Quantities are checked against the net shipped quantity
    (reversed shipments excluded), so duplicate or over-shipment cannot pass,
    and the inventory service rejects insufficient stock inside the same
    transaction.
    """
    _staff(actor)
    reference = (reference or "").strip()
    if not reference:
        raise ValidationError({"reference": "مرجع الشحن مطلوب لمنع التكرار."})
    if not lines:
        raise ValidationError("أدخل كمية مشحونة واحدة على الأقل.")
    if source_location is None:
        raise ValidationError({"source_location": "موقع الصرف مطلوب."})
    effective_date = shipment_date or date.today()
    # Revenue is recognized at shipment. A finalized monthly result must be
    # reopened before a late shipment can change its revenue and loss balance.
    from costing.models import ensure_profit_period_open

    ensure_profit_period_open(
        effective_date.year, effective_date.month, "تسجيل شحنة مبيعات"
    )

    with transaction.atomic():
        order = SalesOrder.objects.select_for_update().select_related("customer").get(pk=order.pk)
        if order.status != SalesOrder.Status.CONFIRMED:
            raise ValidationError("لا يمكن الشحن إلا لأمر بيع مؤكد.")
        if order.customer_submitted and order.has_unpriced_lines:
            raise ValidationError(
                "لا يمكن شحن طلب عميل قبل مراجعة الأسعار وتحديد سعر موجب لكل بند."
            )
        if not source_location.active or not source_location.warehouse.active:
            raise ValidationError({"source_location": "اختر موقع مخزون نشطًا داخل مخزن نشط."})
        if Shipment.objects.filter(reference=reference).exists():
            raise ValidationError({"reference": "مرجع الشحن مستخدم من قبل."})

        order_lines = {
            line.pk: line
            for line in SalesOrderLine.objects.select_for_update()
            .filter(order=order)
            .select_related("product", "product__base_uom", "unit")
        }
        shipped_map = _shipped_map(order)
        normalized = []
        seen_lines = set()
        for payload in lines:
            order_line = payload.get("order_line")
            if isinstance(order_line, int):
                order_line = order_lines.get(order_line)
            if order_line is None or order_line.pk not in order_lines:
                raise ValidationError("بند أمر البيع غير موجود أو لا يخص هذا الأمر.")
            if order_line.pk in seen_lines:
                raise ValidationError("لا تكرر بند أمر البيع في الشحن نفسه.")
            seen_lines.add(order_line.pk)

            try:
                quantity = Decimal(payload.get("quantity"))
            except (TypeError, InvalidOperation) as exc:
                raise ValidationError({"quantity": "الكمية المشحونة غير صالحة."}) from exc
            if not quantity.is_finite() or quantity <= 0:
                raise ValidationError({"quantity": "الكمية المشحونة يجب أن تكون أكبر من صفر."})
            unit = payload.get("unit")
            if unit is None or not unit.active:
                raise ValidationError({"unit": "اختر وحدة شحن نشطة."})
            if not order_line.product.active:
                raise ValidationError({"product": f"الصنف {order_line.product.sku} غير نشط؛ لا يمكن شحنه."})
            quantity_base = to_base_quantity(order_line.product, quantity, unit)
            already_shipped = shipped_map.get(order_line.pk, Decimal("0"))
            if already_shipped + quantity_base > order_line.quantity_base:
                outstanding = order_line.quantity_base - already_shipped
                raise ValidationError(
                    {"quantity": f"الكمية المشحونة تتجاوز المتبقي في البند {order_line.product.sku}: المتبقي {outstanding}."}
                )
            normalized.append({
                "order_line": order_line,
                "quantity": quantity,
                "unit": unit,
                "quantity_base": quantity_base,
            })

        shipment_id = uuid.uuid4()
        shipment = Shipment(
            id=shipment_id,
            order=order,
            reference=reference,
            customer_reference=(customer_reference or "").strip(),
            shipment_date=effective_date,
            source_location=source_location,
            notes=(notes or "").strip(),
            actor=actor,
        )
        try:
            shipment.save()
        except IntegrityError as exc:
            raise ValidationError({"reference": "مرجع الشحن مستخدم من قبل."}) from exc

        for index, payload in enumerate(normalized, start=1):
            order_line = payload["order_line"]
            movement = post_movement(
                reference=f"SAL-S-{shipment_id.hex}-{index}",
                movement_type=StockMovement.Type.ISSUE,
                product=order_line.product,
                quantity=payload["quantity"],
                unit=payload["unit"],
                actor=actor,
                source_location=source_location,
                external_reference=reference,
                source_reference_type="sales.shipment",
                source_reference_id=f"{shipment_id.hex}:{order_line.pk}",
                reason=f"شحن أمر بيع {order.order_number} إلى {order.customer.name_ar}",
            )
            shipment_line = ShipmentLine(
                shipment=shipment,
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
            shipment_line.save()
        return shipment
