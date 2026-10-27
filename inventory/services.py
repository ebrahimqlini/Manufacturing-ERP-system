from decimal import Decimal, InvalidOperation, localcontext

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from catalog.models import Product, ProductUnitConversion
from .models import (
    InventoryLock,
    LastPurchaseCost,
    LastPurchaseCostHistory,
    StockLedgerEntry,
    StockMovement,
    StorageLocation,
)
from core.permissions import require_permission, require_staff

SCALE = Decimal("0.000000000001")


def to_base_quantity(product, quantity, unit):
    """Convert exactly; values that need rounding beyond 12 decimal places are rejected."""
    quantity = Decimal(quantity)
    if not quantity.is_finite() or quantity <= 0:
        raise ValidationError({"quantity": "الكمية يجب أن تكون أكبر من صفر."})
    if unit is None or not unit.pk or not unit.active:
        raise ValidationError({"unit": "اختر وحدة قياس نشطة."})
    with localcontext() as context:
        context.prec = 80
        try:
            if quantity.quantize(SCALE) != quantity:
                raise ValidationError({"quantity": "الكمية تتجاوز دقة 12 منزلة عشرية."})
        except InvalidOperation as exc:
            raise ValidationError({"quantity": "الكمية تتجاوز دقة التخزين المسموحة."}) from exc
    if unit.pk == product.base_uom_id:
        converted = quantity
    else:
        try:
            conversion = ProductUnitConversion.objects.get(product=product, unit=unit, active=True)
        except ProductUnitConversion.DoesNotExist as exc:
            raise ValidationError({"unit": "لا يوجد تحويل نشط لهذه الوحدة على الصنف."}) from exc
        if unit.dimension != product.base_uom.dimension:
            raise ValidationError({"unit": "لا يمكن التحويل بين وحدات ذات أبعاد مختلفة."})
        with localcontext() as context:
            context.prec = 80
            converted = quantity * conversion.factor
    with localcontext() as context:
        context.prec = 80
        try:
            if converted.quantize(SCALE) != converted or len(converted.quantize(SCALE).as_tuple().digits) > 30 or converted.adjusted() >= 18:
                raise ValidationError({"quantity": "ناتج التحويل يتجاوز دقة التخزين؛ لم يتم تقريبه."})
        except InvalidOperation as exc:
            raise ValidationError({"quantity": "ناتج التحويل يتجاوز دقة التخزين؛ لم يتم تقريبه."}) from exc
    return converted


def get_last_purchase_cost_history_as_of(product, as_of_datetime):
    """Latest append-only LPC change effective at or before ``as_of_datetime``.

    ``None`` means no purchase-price event existed then; a row whose price is
    ``None`` means the last valid purchase was reversed and no earlier valid
    purchase remains. Both are "no applicable price", never zero.
    """
    return (
        LastPurchaseCostHistory.objects.filter(
            product=product, effective_at__lte=as_of_datetime
        )
        .select_related("source_movement")
        .order_by("-effective_at", "-recorded_at", "-pk")
        .first()
    )


def get_last_purchase_cost_as_of(product, as_of_datetime):
    """Applicable last purchase cost per base unit at ``as_of_datetime``.

    There is deliberately no fallback to a later receipt and no fallback to the
    material's current last purchase cost: a historical event keeps the price
    that was effective when it happened.
    """
    entry = get_last_purchase_cost_history_as_of(product, as_of_datetime)
    if entry is None or entry.price_per_base_unit is None:
        return None
    return entry.price_per_base_unit


def _lock_item_locations(product, locations):
    """Serialize all stock changes for each item/location pair on PostgreSQL."""
    unique_locations = {loc.pk: loc for loc in locations if loc is not None}
    keys = sorted(unique_locations)
    for location_id in keys:
        try:
            InventoryLock.objects.get_or_create(product=product, location_id=location_id)
        except IntegrityError:
            # A concurrent first posting may create the same unique lock row.
            pass
    list(
        InventoryLock.objects.select_for_update()
        .filter(product=product, location_id__in=keys)
        .order_by("location_id")
    )


def balance_for(product, location):
    return StockLedgerEntry.objects.filter(product=product, location=location).aggregate(total=Sum("quantity_base"))["total"] or Decimal("0")


def _ensure_sufficient_stock(product, location, quantity):
    balance = balance_for(product, location)
    if balance < quantity:
        raise ValidationError(
            f"الرصيد المتاح في الموقع {location.code} هو {balance}، ولا يكفي للكمية المطلوبة {quantity}."
        )


def post_movement(*, reference, movement_type, product, quantity, unit, actor,
                  source_location=None, destination_location=None, occurred_at=None,
                  external_reference="", source_reference_type="", source_reference_id="", reason="", notes="",
                  unit_cost_per_base_unit=None):
    """Post one immutable, single-item event and its signed ledger legs atomically.

    LPC is deliberately not updated here: the movement's source labels are
    caller-controlled. The purchasing service records LPC only after it has
    persisted and validated the matching PurchaseReceiptLine.
    """
    require_permission(actor, "inventory.post_movement", "ترحيل حركة مخزون")
    require_staff(actor)
    if not reference or not reference.strip():
        raise ValidationError({"reference": "المرجع الفريد مطلوب لمنع الترحيل المكرر."})
    if bool(source_reference_type) != bool(source_reference_id):
        raise ValidationError({"source_reference_id": "نوع المصدر ومعرّفه مطلوبان معًا."})
    if not product.active:
        raise ValidationError({"product": "لا يمكن إنشاء حركة لصنف غير نشط."})
    if movement_type not in StockMovement.Type.values or movement_type == StockMovement.Type.REVERSAL:
        raise ValidationError({"movement_type": "نوع الحركة غير صالح لهذا الإجراء."})
    expected = {
        StockMovement.Type.OPENING: (False, True),
        StockMovement.Type.RECEIPT: (False, True),
        StockMovement.Type.ISSUE: (True, False),
        StockMovement.Type.TRANSFER: (True, True),
        StockMovement.Type.ADJUSTMENT_IN: (False, True),
        StockMovement.Type.ADJUSTMENT_OUT: (True, False),
    }
    if movement_type not in expected:
        raise ValidationError({"movement_type": "نوع الحركة غير مدعوم."})
    needs_source, needs_destination = expected[movement_type]
    if bool(source_location) != needs_source or bool(destination_location) != needs_destination:
        raise ValidationError("مواقع المصدر والوجهة لا تتوافق مع نوع الحركة.")
    if movement_type == StockMovement.Type.TRANSFER and source_location == destination_location:
        raise ValidationError({"destination_location": "يجب أن يختلف موقع المصدر عن الوجهة."})
    if (movement_type in (StockMovement.Type.ADJUSTMENT_IN, StockMovement.Type.ADJUSTMENT_OUT) and not reason.strip()):
        raise ValidationError({"reason": "سبب التسوية مطلوب."})

    # Source-reference fields are caller-provided audit labels. They are not
    # proof that a receipt belongs to purchasing; LPC is recorded only after a
    # persisted PurchaseReceiptLine is validated by record_purchase_receipt_cost.

    locations = [loc for loc in (source_location, destination_location) if loc]
    for location in locations:
        if not location.active or not location.warehouse.active:
            raise ValidationError("لا يمكن استخدام موقع أو مخزن غير نشط في حركة جديدة.")
    base_quantity = to_base_quantity(product, quantity, unit)
    now = occurred_at or timezone.now()

    with transaction.atomic():
        _lock_item_locations(product, locations)
        if source_location:
            _ensure_sufficient_stock(product, source_location, base_quantity)
        if movement_type == StockMovement.Type.OPENING:
            if StockLedgerEntry.objects.filter(product=product, location=destination_location).exists():
                raise ValidationError("الرصيد الافتتاحي مسموح مرة واحدة فقط للصنف في الموقع قبل أي حركة أخرى.")
        try:
            movement = StockMovement.objects.create(
                reference=reference.strip(), movement_type=movement_type, product=product,
                quantity=quantity, unit=unit, base_quantity=base_quantity,
                source_location=source_location, destination_location=destination_location,
                occurred_at=now, actor=actor, external_reference=external_reference.strip(),
                source_reference_type=source_reference_type.strip(), source_reference_id=source_reference_id.strip(),
                reason=reason.strip(), notes=notes.strip(),
            )
        except IntegrityError as exc:
            raise ValidationError({"reference": "هذا المرجع سبق ترحيله."}) from exc
        if source_location:
            StockLedgerEntry.objects.create(movement=movement, product=product, location=source_location, quantity_base=-base_quantity)
        if destination_location:
            StockLedgerEntry.objects.create(movement=movement, product=product, location=destination_location, quantity_base=base_quantity)

    return movement


def record_purchase_receipt_cost(*, receipt_line, actor):
    """Record LPC only from a persisted, internally consistent purchase line."""
    require_permission(actor, "purchasing.create_receipt", "تسجيل تكلفة استلام مشتريات")
    require_staff(actor)
    from purchasing.models import PurchaseReceiptLine

    with transaction.atomic():
        line = PurchaseReceiptLine.objects.select_for_update().select_related(
            "receipt", "receipt__order", "order_line", "order_line__material", "movement"
        ).get(pk=receipt_line.pk)
        movement = line.movement
        if not _is_genuine_purchase_receipt_line(line):
            raise ValidationError("لا تطابق حركة المخزون استلام مشتريات موثقًا.")
        if LastPurchaseCostHistory.objects.filter(source_movement=movement).exists():
            raise ValidationError("سُجلت تكلفة حركة الاستلام من قبل.")
        _update_last_purchase_cost(line.order_line.material, movement, line.price_per_base_unit, actor)


def _is_genuine_purchase_receipt_line(line):
    receipt = line.receipt
    movement = line.movement
    return all((
        movement.movement_type == StockMovement.Type.RECEIPT,
        movement.product_id == line.order_line.material_id,
        movement.base_quantity == line.quantity_base,
        movement.quantity == line.quantity,
        movement.unit_id == line.unit_id,
        movement.external_reference == receipt.reference,
        movement.source_reference_type == "purchasing.purchase_receipt",
        movement.source_reference_id == f"{receipt.pk.hex}:{line.order_line_id}",
        receipt.order_id == line.order_line.order_id,
        receipt.order.status == "CONFIRMED",
        line.quantity_base > 0,
        line.price_per_base_unit > 0,
        not StockMovement.objects.filter(reversal_of=movement).exists(),
    ))


def _update_last_purchase_cost(product, movement, price_per_base_unit, actor):
    """Update the product's current LPC (if the receipt is the latest) + history.

    ``posted_at`` is the successful system posting time. A backdated document
    date cannot make a price available earlier in historical costing.
    """
    lpc = LastPurchaseCost.objects.select_for_update().select_related("source_movement").filter(product=product).first()
    is_newer_or_equal = (
        lpc is None
        or lpc.source_movement_id == movement.pk
        or lpc.source_movement.posted_at <= movement.posted_at
    )
    if lpc is None:
        LastPurchaseCost.objects.create(
            product=product,
            price_per_base_unit=price_per_base_unit,
            source_movement=movement,
            updated_by=actor,
        )
    elif is_newer_or_equal:
        lpc.price_per_base_unit = price_per_base_unit
        lpc.source_movement = movement
        lpc.updated_by = actor
        lpc.save(update_fields=["price_per_base_unit", "source_movement", "updated_by", "updated_at"])
    LastPurchaseCostHistory.objects.create(
        product=product,
        price_per_base_unit=price_per_base_unit,
        effective_at=movement.posted_at,
        source_movement=movement,
        change_reason=f"استلام: {movement.reference}",
    )


def _ensure_finalized_period_open_for_movement(movement):
    """Block reversals that would silently rewrite a finalized financial month."""
    if movement.source_reference_type == "sales.shipment":
        from costing.models import ensure_profit_period_open
        from sales.models import Shipment

        shipment = (
            Shipment.objects.filter(reference=movement.external_reference)
            .only("shipment_date")
            .first()
        )
        if shipment is not None:
            ensure_profit_period_open(
                shipment.shipment_date.year,
                shipment.shipment_date.month,
                "عكس شحنة مبيعات",
            )
        return
    if movement.source_reference_type == "manufacturing.material_issue":
        from costing.models import ensure_period_open

        posting_date = timezone.localdate(movement.occurred_at)
        ensure_period_open(
            posting_date.year,
            posting_date.month,
            "عكس صرف مواد لأمر إنتاج",
        )


def reverse_movement(*, movement, reference, actor, reason):
    require_permission(actor, "inventory.reverse_movement", "عكس حركة مخزون")
    require_staff(actor)
    if not reason or not reason.strip():
        raise ValidationError({"reason": "سبب العكس مطلوب."})
    if movement.movement_type == StockMovement.Type.REVERSAL or movement.reversal_of_id:
        raise ValidationError("لا يمكن عكس حركة عكس؛ حركات العكس نهائية للحفاظ على سجل التدقيق.")
    original_entries = list(movement.ledger_entries.select_related("location", "location__warehouse"))
    with transaction.atomic():
        # Month-scoped advisory locks: serialize against finalization of every
        # financial month this reversal would change, before validating that
        # those months are still open. Row locks alone are not enough: locking
        # a period row with select_for_update() does nothing when the row does
        # not exist yet. A material-issue reversal changes the costing month of
        # the original movement; a shipment reversal changes the profit month
        # of the shipment date (which may differ from the movement timestamp
        # for a backdated shipment), so both dates are locked.
        from costing.models import (
            lock_costing_month,
            lock_profit_month,
        )

        posting_date = timezone.localdate(movement.occurred_at)
        lock_costing_month(posting_date.year, posting_date.month)
        profit_months = {(posting_date.year, posting_date.month)}
        if movement.source_reference_type == "sales.shipment":
            from sales.models import Shipment

            shipment = Shipment.objects.filter(
                reference=movement.external_reference
            ).only("shipment_date").first()
            if shipment is not None:
                profit_months.add((shipment.shipment_date.year, shipment.shipment_date.month))
        for year, month in sorted(profit_months):
            lock_profit_month(year, month)
        _ensure_finalized_period_open_for_movement(movement)
        _lock_item_locations(movement.product, [entry.location for entry in original_entries])
        if hasattr(movement, "reversal"):
            raise ValidationError("تم عكس هذه الحركة من قبل.")
        for entry in original_entries:
            reversal_delta = -entry.quantity_base
            if reversal_delta < 0:
                _ensure_sufficient_stock(movement.product, entry.location, -reversal_delta)
        # Validate provenance before the original movement acquires a reversal.
        from purchasing.models import PurchaseReceiptLine
        purchase_line = (
            PurchaseReceiptLine.objects.select_related("receipt__order", "order_line", "movement")
            .filter(movement=movement)
            .first()
        )
        is_genuine_purchase = bool(
            purchase_line and _is_genuine_purchase_receipt_line(purchase_line)
        )
        try:
            reversed_movement = StockMovement.objects.create(
                reference=reference.strip(), movement_type=StockMovement.Type.REVERSAL,
                product=movement.product, quantity=movement.quantity, unit=movement.unit,
                base_quantity=movement.base_quantity,
                source_location=movement.destination_location, destination_location=movement.source_location,
                occurred_at=timezone.now(), actor=actor, external_reference=movement.external_reference,
                reason=reason.strip(), notes=f"عكس الحركة {movement.reference}", reversal_of=movement,
            )
        except IntegrityError as exc:
            raise ValidationError({"reference": "المرجع مستخدم أو أن الحركة عُكست بالفعل."}) from exc
        for entry in original_entries:
            StockLedgerEntry.objects.create(
                movement=reversed_movement, product=movement.product, location=entry.location,
                quantity_base=-entry.quantity_base,
            )

        if movement.movement_type == StockMovement.Type.RECEIPT and is_genuine_purchase and LastPurchaseCostHistory.objects.filter(source_movement=movement).exists():
            _restore_last_purchase_cost_on_reversal(movement, reversed_movement, actor)

    return reversed_movement


def _restore_last_purchase_cost_on_reversal(reversed_movement, reversal_movement, actor):
    """Restore the applicable LPC after a purchase receipt is reversed.

    Only successfully posted, still-valid *purchase* receipts are eligible
    evidence. The restored price is the latest eligible receipt effective at or
    before the reversed receipt (never a later one). If none exists, the
    current LPC is cleared and the history records an explicit null price at
    the reversal event — never a fabricated 0.00 price.

    The history entry's ``effective_at`` is the reversal event time, not the
    original receipt time, so a historical lookup before the reversal still
    sees the price that was valid then.
    """
    from purchasing.models import PurchaseReceiptLine

    product = reversed_movement.product
    reversal_at = reversal_movement.posted_at

    # The active price after removing the reversed receipt is the latest still
    # valid purchase receipt effective at the reversal moment. This is not
    # always the receipt immediately before the reversed one: if a later valid
    # receipt exists, reversing an older receipt must leave that later price
    # active.
    eligible_receipts = (
        StockMovement.objects.filter(
            product=product,
            movement_type=StockMovement.Type.RECEIPT,
            purchasing_receipt_line__price_per_base_unit__gt=0,
            reversal__isnull=True,
            posted_at__lte=reversal_at,
        )
        .exclude(pk=reversed_movement.pk)
        .order_by("-posted_at", "-pk")
    )
    previous_receipt = None
    previous_price = None
    for candidate in eligible_receipts:
        try:
            candidate_line = candidate.purchasing_receipt_line
        except PurchaseReceiptLine.DoesNotExist:
            continue
        if not _is_genuine_purchase_receipt_line(candidate_line):
            continue
        history_entry = LastPurchaseCostHistory.objects.filter(
            product=product,
            source_movement=candidate,
            price_per_base_unit__isnull=False,
        ).order_by("-effective_at", "-recorded_at", "-pk").first()
        if history_entry is not None:
            previous_receipt = candidate
            previous_price = history_entry.price_per_base_unit
            break

    lpc = LastPurchaseCost.objects.select_for_update().select_related("source_movement").filter(product=product).first()
    if previous_receipt is not None:
        if lpc is None:
            LastPurchaseCost.objects.create(
                product=product,
                price_per_base_unit=previous_price,
                source_movement=previous_receipt,
                updated_by=actor,
            )
        else:
            lpc.price_per_base_unit = previous_price
            lpc.source_movement = previous_receipt
            lpc.updated_by = actor
            lpc.save(update_fields=["price_per_base_unit", "source_movement", "updated_by", "updated_at"])
        LastPurchaseCostHistory.objects.create(
            product=product,
            price_per_base_unit=previous_price,
            effective_at=reversal_at,
            source_movement=reversal_movement,
            change_reason=f"عكس استلام: {reversed_movement.reference} → استعادة السعر السابق",
        )
        return

    # No eligible earlier purchase price: clear the current value and record
    # an explicit "no valid price" marker (null, never zero).
    if lpc is not None:
        lpc.delete()
    LastPurchaseCostHistory.objects.create(
        product=product,
        price_per_base_unit=None,
        effective_at=reversal_at,
        source_movement=reversal_movement,
        change_reason=f"عكس استلام: {reversed_movement.reference} → لا يوجد سعر شراء سابق صالح",
    )


def ledger_balances(*, product=None, product_ids=None, location=None, location_ids=None, warehouse=None):
    qs = StockLedgerEntry.objects.select_related("product", "product__base_uom", "location", "location__warehouse")
    if product:
        qs = qs.filter(product=product)
    if product_ids is not None:
        qs = qs.filter(product_id__in=product_ids)
    if location:
        qs = qs.filter(location=location)
    if location_ids is not None:
        qs = qs.filter(location_id__in=location_ids)
    if warehouse:
        qs = qs.filter(location__warehouse=warehouse)
    return qs.values("product_id", "location_id").annotate(balance=Sum("quantity_base"))
