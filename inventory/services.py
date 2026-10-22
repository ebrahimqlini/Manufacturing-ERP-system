from decimal import Decimal, InvalidOperation, localcontext

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from catalog.models import Product, ProductUnitConversion
from .models import InventoryLock, StockLedgerEntry, StockMovement, StorageLocation
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
                  external_reference="", source_reference_type="", source_reference_id="", reason="", notes=""):
    """Post one immutable, single-item event and its signed ledger legs atomically."""
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
    return reversed_movement


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
