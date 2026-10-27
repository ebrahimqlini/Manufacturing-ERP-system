import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from inventory.services import get_last_purchase_cost_as_of, to_base_quantity
from manufacturing.models import MaterialIssue, ProductionLine, ProductionResult, ProductionMaterialWaste

from .models import (
    CostingPeriod,
    MonthlyExpense,
    MonthlyProfitCOGSLine,
    MonthlyProfitPeriod,
    ProductCostingEntry,
    ProductSellingPrice,
    ProductionOrderCostingEntry,
    RawMaterialPrice,
    RollYieldStandard,
    lock_costing_month,
    lock_profit_month,
)

from core.permissions import require_permission, require_staff

CENT = Decimal("0.01")
FOUR_DECIMALS = Decimal("0.0001")

# Material valuation method used by this module. Posted purchase receipts now
# carry an immutable base-unit cost into the shared ledger, so actual material
# issues are valued with the latest posted purchase cost (Last Purchase Cost).
# Owner-entered prices remain only as a fallback for materials without any
# posted purchase cost yet, and figures stay provisional until the factory
# accountant approves a valuation method (see DECISIONS.md).
MATERIAL_VALUATION_METHOD = "LAST_PURCHASE_COST"


def get_price_record(material, as_of_date=None):
    """Latest owner-entered price effective on or before the as-of date.

    There is intentionally no fallback to a later effective date: a
    future-dated price must never be silently applied to an earlier period.
    """
    as_of = as_of_date or timezone.now().date()
    return (
        RawMaterialPrice.objects.filter(material=material, effective_date__lte=as_of)
        .order_by("-effective_date", "-created_at")
        .first()
    )


def get_price_record_info(material, as_of_date=None):
    """Return ``(record, note)`` where ``record`` is None when no price applies.

    The note distinguishes "no price at all" from "a price exists but is only
    effective after this period", so nothing is hidden from the owner.
    """
    record = get_price_record(material, as_of_date)
    if record:
        return record, None
    future = RawMaterialPrice.objects.filter(material=material).order_by("-effective_date").first()
    if future:
        return None, (
            f"سعر مسجل لهذه المادة بتاريخ سريان {future.effective_date:%Y-%m-%d} وهو بعد فترة الاحتساب؛ "
            f"لم يُستخدم: {material.sku} ({material.name_ar})."
        )
    return None, None


def get_material_price(material, as_of_date=None):
    """Return the effective base-unit price (Decimal) or None when unset."""
    record = get_price_record(material, as_of_date)
    return record.price_per_base_unit if record else None


def get_month_date_range(year, month):
    """Return ``(start_date, end_date)`` for a calendar month."""
    _, last_day = calendar.monthrange(year, month)
    return date(year, month, 1), date(year, month, last_day)


def _cents(value):
    return (value or Decimal("0")).quantize(CENT, rounding=ROUND_HALF_UP)


def _reconcile_allocations(allocations, total_target, key):
    """Adjust cent rounding on the largest row so sum(row[key]) == total_target."""
    allocations = [item for item in allocations if item[key] is not None]
    if not allocations:
        return
    diff = total_target - sum(item[key] for item in allocations)
    if diff != Decimal("0.00"):
        max(allocations, key=lambda item: item[key])[key] += diff


def _distribute_bucket(assignments, totals, field_map, temp_prefix):
    """Allocate one expense bucket (line-scoped or factory-shared) to targets.

    ``assignments`` is a list of ``(target, ratio)`` pairs. Each bucket total is
    split by ratio, rounded to cents, reconciled against its own total only, and
    finally merged into the running-total field. Reconciling in isolation is
    essential: a bucket with a zero or different total must never rewrite
    amounts contributed by another bucket (for example a line-scoped expense
    followed by factory-wide shared expenses).
    """
    if not assignments:
        return
    targets = [target for target, _ in assignments]
    for target, ratio in assignments:
        for bucket_key, field in field_map.items():
            target[f"{temp_prefix}_{field}"] = _cents(totals.get(bucket_key, Decimal("0.00")) * ratio)
    for bucket_key, field in field_map.items():
        temp_key = f"{temp_prefix}_{field}"
        _reconcile_allocations(targets, _cents(totals.get(bucket_key, Decimal("0.00"))), temp_key)
        for target in targets:
            target[field] = target.get(field, Decimal("0.00")) + target[temp_key]
            del target[temp_key]


def _staff(actor):
    require_staff(actor)


def _select_yield_standard(product, production_line, notes):
    """Pick the expected-yield standard for the product and its production line.

    A line-specific standard wins; a generic (line-less) standard is the
    fallback. Multiple matches are treated as a conflict and reported instead
    of silently choosing one.
    """
    standards = RollYieldStandard.objects.filter(product=product, active=True).select_related(
        "raw_material", "roll_unit", "carton_unit"
    )
    if production_line:
        line_matches = list(standards.filter(production_line_id=production_line))
        if len(line_matches) > 1:
            notes.append(
                f"يوجد أكثر من معيار إنتاجية نشط للمنتج {product.sku} على نفس خط الإنتاج؛ لم يُحتسب تحليل العائد."
            )
            return None
        if line_matches:
            return line_matches[0]
    generic = list(standards.filter(production_line__isnull=True))
    if len(generic) > 1:
        notes.append(
            f"يوجد أكثر من معيار إنتاجية عام نشط للمنتج {product.sku}؛ لم يُحتسب تحليل العائد."
        )
        return None
    if generic:
        return generic[0]
    line_label = f" على خط الإنتاج {production_line.name_ar}" if production_line else ""
    notes.append(f"لا يوجد معيار إنتاجية رول نشط للمنتج {product.sku} ({product.name_ar}){line_label}.")
    return None


def _yield_analysis(product, production_line, rolls_raw_base, good_units_base, notes):
    """Compute rolls consumed, expected cartons, variance, and shortfall %."""
    standard = _select_yield_standard(product, production_line, notes)
    analysis = {
        "rolls_consumed": Decimal("0"),
        "expected_cartons": Decimal("0"),
        "actual_good_cartons": good_units_base,
        "yield_variance_cartons": Decimal("0"),
        "yield_shortfall_percent": None,
        # Exposed so callers can snapshot the exact standard used at calculation
        # time (revision + approved thresholds) on the costing entry.
        "standard": standard,
    }
    if not standard:
        return analysis

    raw_material = standard.raw_material
    raw_qty = rolls_raw_base.get(raw_material.pk, Decimal("0"))
    if raw_qty <= 0:
        notes.append(
            f"لم يُسجل استهلاك للمادة الخام {raw_material.sku} خلال الفترة؛ لم يُحتسب تحليل العائد للمنتج {product.sku}."
        )
        return analysis

    if standard.roll_unit_id == raw_material.base_uom_id:
        rolls_consumed = raw_qty
    else:
        try:
            base_per_roll = to_base_quantity(raw_material, Decimal("1.0"), standard.roll_unit)
            rolls_consumed = raw_qty / base_per_roll if base_per_roll > 0 else raw_qty
        except ValidationError:
            rolls_consumed = raw_qty

    if standard.carton_unit_id == product.base_uom_id:
        actual_good_cartons = good_units_base
    else:
        try:
            base_per_carton = to_base_quantity(product, Decimal("1.0"), standard.carton_unit)
            actual_good_cartons = good_units_base / base_per_carton if base_per_carton > 0 else good_units_base
        except ValidationError:
            actual_good_cartons = good_units_base

    expected_cartons = (rolls_consumed * standard.expected_cartons_per_roll).quantize(FOUR_DECIMALS, rounding=ROUND_HALF_UP)
    variance = (actual_good_cartons - expected_cartons).quantize(FOUR_DECIMALS, rounding=ROUND_HALF_UP)
    # Shortfall is a production-yield measure only; it is never treated as a
    # measured waste quantity and is never negative.
    if expected_cartons > 0 and actual_good_cartons < expected_cartons:
        shortfall = expected_cartons - actual_good_cartons
        shortfall_percent = ((shortfall / expected_cartons) * Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)
    elif expected_cartons > 0:
        shortfall_percent = Decimal("0.00")
    else:
        shortfall_percent = None

    analysis.update(
        {
            "rolls_consumed": rolls_consumed.quantize(FOUR_DECIMALS, rounding=ROUND_HALF_UP),
            "expected_cartons": expected_cartons,
            "actual_good_cartons": actual_good_cartons.quantize(FOUR_DECIMALS, rounding=ROUND_HALF_UP),
            "yield_variance_cartons": variance,
            "yield_shortfall_percent": shortfall_percent,
        }
    )
    return analysis


def _release_draft_cogs_source_links(period):
    """Detach provisional COGS rows before replacing this period's cost entries.

    A reopened monthly result keeps its old COGS detail for audit until it is
    recalculated. Those draft rows must not prevent the corrected costing
    entries from being replaced; finalized rows are never touched here.
    """
    MonthlyProfitCOGSLine.objects.filter(
        source_costing_entry__period=period,
        profit_period__status=MonthlyProfitPeriod.Status.DRAFT,
    ).update(source_costing_entry=None)


def calculate_monthly_costing(*, year, month, allocation_policy=None, waste_policy=None, actor=None):
    """Calculate the monthly production cost from actual issues and output.

    Period boundaries are strict: material issues count in the month of their
    stock movement and production results in the month they were recorded, so
    the same movement is never counted in two periods. Reversed issues are
    excluded because their movement carries a reversal.
    """
    require_permission(actor, "costing.calculate_monthly_costing", "احتساب التكاليف الشهرية")
    require_staff(actor)
    start_date, end_date = get_month_date_range(year, month)

    with transaction.atomic():
        # Month-scoped advisory locks (costing before profit, per the locking
        # protocol in costing.models): serialize against finalization of both
        # periods even when no period row exists yet, and make the profit
        # check below a real pre-condition instead of a race-prone read.
        lock_costing_month(year, month)
        lock_profit_month(year, month)
        if MonthlyProfitPeriod.objects.filter(
            year=year,
            month=month,
            status=MonthlyProfitPeriod.Status.FINALIZED,
        ).exists():
            raise ValidationError(
                "لا يمكن إعادة احتساب التكاليف بعد اعتماد نتيجة الشهر المالي؛ "
                "أعد فتح فترة الأرباح والخسائر أولاً."
            )
        # get_or_create under a row lock plus the advisory month lock: the
        # first concurrent calculation of a brand-new month creates the row
        # (unique year/month constraint is the backstop); every later one
        # serializes on the row and can never overwrite a concurrent
        # finalization with a stale draft save.
        period, _ = CostingPeriod.objects.select_for_update().get_or_create(
            year=year,
            month=month,
            defaults={
                "allocation_policy": allocation_policy or CostingPeriod.AllocationPolicy.UNITS_PRODUCED,
                "waste_policy": waste_policy or CostingPeriod.WastePolicy.ABSORBED_NORMAL,
                "material_valuation_method": MATERIAL_VALUATION_METHOD,
                "start_date": start_date,
                "end_date": end_date,
            },
        )

        if period.status == CostingPeriod.Status.FINALIZED:
            raise ValidationError("لا يمكن إعادة احتساب فترة تكاليف معتمدة نهائيًا؛ أعد فتح الفترة أولاً.")

        if allocation_policy:
            period.allocation_policy = allocation_policy
        if waste_policy:
            period.waste_policy = waste_policy
        period.material_valuation_method = MATERIAL_VALUATION_METHOD
        # Explicit stored month boundaries: every recalculation records the exact
        # calendar range it attributed consumption, output, and expenses to.
        period.start_date = start_date
        period.end_date = end_date

        expenses = MonthlyExpense.objects.filter(
            year=year,
            month=month,
            status=MonthlyExpense.Status.ACTIVE,
        )
        labor_recorded = expenses.filter(expense_type=MonthlyExpense.ExpenseType.LABOR).exists()
        electricity_recorded = expenses.filter(
            expense_type=MonthlyExpense.ExpenseType.ELECTRICITY
        ).exists()
        water_recorded = expenses.filter(
            expense_type=MonthlyExpense.ExpenseType.WATER
        ).exists()
        period.labor_recorded = labor_recorded
        period.electricity_recorded = electricity_recorded
        period.water_recorded = water_recorded
        labor_total = _cents(expenses.filter(expense_type=MonthlyExpense.ExpenseType.LABOR).aggregate(s=Sum("amount"))["s"])
        electricity_total = _cents(expenses.filter(expense_type=MonthlyExpense.ExpenseType.ELECTRICITY).aggregate(s=Sum("amount"))["s"])
        water_total = _cents(expenses.filter(expense_type=MonthlyExpense.ExpenseType.WATER).aggregate(s=Sum("amount"))["s"])
        other_total = _cents(expenses.filter(expense_type=MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL).aggregate(s=Sum("amount"))["s"])
        period.labor_total = labor_total
        period.electricity_total = electricity_total
        period.water_total = water_total
        period.other_industrial_total = other_total

        missing_notes = []
        is_complete = True

        if not expenses.exists():
            missing_notes.append("لم يتم تسجيل أي مصاريف صناعية (عمالة / كهرباء / مياه / أخرى) لهذا الشهر.")

        # --- Collect actual consumption and output per production order ---
        period_issues = (
            MaterialIssue.objects.filter(
                movement__occurred_at__date__gte=start_date,
                movement__occurred_at__date__lte=end_date,
                movement__reversal__isnull=True,
            )
            .select_related(
                "order",
                "order__product",
                "order__production_line",
                "requirement__material",
                "requirement__material__base_uom",
            )
            .order_by("order__order_number", "created_at", "pk")
        )
        period_results = (
            ProductionResult.objects.filter(posted_at__date__gte=start_date, posted_at__date__lte=end_date, correction__isnull=True)
            .select_related("order", "order__product", "order__production_line")
            .prefetch_related("material_waste")
            .order_by("order__order_number", "posted_at", "pk")
        )

        legacy_unknown_results = ProductionResult.objects.filter(
            posted_at__isnull=True, occurred_at__date__gte=start_date,
            occurred_at__date__lte=end_date,
        ).count()
        if legacy_unknown_results:
            is_complete = False
            missing_notes.append(
                f"يوجد {legacy_unknown_results} نتيجة إنتاج قديمة بلا وقت ترحيل موثوق؛ "
                "تعذر نسبتها إلى فترة تكلفة مالية."
            )

        order_data = {}

        def order_slot(order):
            return order_data.setdefault(
                order.pk,
                {
                    "order": order,
                    "product": order.product,
                    "production_line_id": order.production_line_id,
                    "good_units": Decimal("0"),
                    "scrap_units": Decimal("0"),
                    "has_unvalued_scrap": False,
                    "material_cost": Decimal("0.00"),
                    "materials": {},
                    "has_missing_price": False,
                },
            )

        for issue in period_issues:
            slot = order_slot(issue.order)
            material = issue.requirement.material
            quantity_base = issue.quantity_base

            # Use the cost snapshot from the material issue (last purchase cost or fallback)
            unit_cost = issue.unit_cost_snapshot
            cost_source = issue.cost_source
            if unit_cost is not None:
                line_cost = _cents(quantity_base * unit_cost)
                unit_price_str = str(unit_cost)
                if cost_source == MaterialIssue.CostSource.LAST_PURCHASE_COST:
                    price_basis = "آخر سعر شراء"
                elif cost_source == MaterialIssue.CostSource.STANDARD_PRICE_FALLBACK:
                    price_basis = "سعر قياسي احتياطي"
                else:
                    price_basis = "غير محدد"
            else:
                # No cost snapshot available — mark as missing
                slot["has_missing_price"] = True
                is_complete = False
                message = f"تكلفة المادة غير متاحة وقت الصرف: {material.sku} ({material.name_ar})"
                if message not in missing_notes:
                    missing_notes.append(message)
                line_cost = Decimal("0.00")
                unit_price_str = "غير مسعر"
                price_basis = "غير متاح"
            slot["material_cost"] += line_cost
            material_line = slot["materials"].setdefault(
                material.pk,
                {
                    "material_id": material.pk,
                    "material_sku": material.sku,
                    "material_name": material.name_ar,
                    "base_uom": material.base_uom.code,
                    "quantity_base": Decimal("0"),
                    "unit_price": unit_price_str,
                    "cost": Decimal("0.00"),
                    "orders": [],
                },
            )
            material_line["quantity_base"] += quantity_base
            material_line["cost"] += line_cost
            material_line["orders"].append(issue.order.order_number)
            # Record price basis for audit trail
            material_line.setdefault("price_basis", []).append(price_basis)

        for result in period_results:
            slot = order_slot(result.order)
            slot["good_units"] += result.good_quantity_base
            slot["scrap_units"] += result.scrap_quantity_base
            has_active_waste_evidence = ProductionMaterialWaste.objects.filter(
                result=result, reversal_of__isnull=True, reversal__isnull=True
            ).exists()
            if not hasattr(result, "waste_review") and not has_active_waste_evidence:
                is_complete = False
                missing_notes.append(
                    f"نتيجة الإنتاج #{result.pk} في أمر {result.order.order_number}: "
                    "لم تُسجل قياسات الهالك المادي ولم يُعتمد صراحة عدم وجوده."
                )

        # Waste belongs to the month in which that waste evidence was posted,
        # even when it references an older result. Reversals retain the same
        # event-time value and contribute the opposite amount.
        period_waste = ProductionMaterialWaste.objects.filter(
            Q(posted_at__date__gte=start_date, posted_at__date__lte=end_date)
            | Q(reversal_of__posted_at__date__gte=start_date, reversal_of__posted_at__date__lte=end_date)
        ).select_related("order", "result", "material")
        for waste in period_waste:
            slot = order_slot(waste.order)
            sign = Decimal("-1") if waste.reversal_of_id else Decimal("1")
            slot.setdefault("scrap_breakdown", []).append({
                "result_id": waste.result_id,
                "material_id": waste.material_id,
                "material_sku": waste.material.sku,
                "material_name": waste.material.name_ar,
                "quantity_base": str(sign * waste.quantity_base),
                "unit_cost_snapshot": str(waste.unit_cost_snapshot) if waste.unit_cost_snapshot is not None else None,
                "total_cost_snapshot": str(sign * waste.total_cost_snapshot) if waste.total_cost_snapshot is not None else None,
                "cost_source": waste.cost_source,
                "posted_at": waste.posted_at.isoformat(),
                "material_issue_id": str(waste.material_issue_id) if waste.material_issue_id else None,
                "roll_reference": waste.roll_reference,
                "is_reversal": bool(waste.reversal_of_id),
                "correction_reason": waste.correction_reason,
            })
            if waste.total_cost_snapshot is None:
                is_complete = False
                missing_notes.append(
                    f"هالك المادة {waste.material.sku} في النتيجة #{waste.result_id} غير مسعر وقت الترحيل."
                )
                slot["has_unvalued_scrap"] = True
            else:
                slot["measured_waste_cost"] = slot.get("measured_waste_cost", Decimal("0.00")) + sign * waste.total_cost_snapshot

        for slot in order_data.values():
            if slot["scrap_units"] > 0 and any(
                result.order_id == slot["order"].pk
                and not hasattr(result, "waste_review")
                and not ProductionMaterialWaste.objects.filter(
                    result=result, reversal_of__isnull=True, reversal__isnull=True
                ).exists()
                for result in period_results
            ):
                slot["has_unvalued_scrap"] = True

        if not order_data:
            _release_draft_cogs_source_links(period)
            period.product_entries.all().delete()
            period.order_entries.all().delete()
            period.materials_total = Decimal("0.00")
            period.total_production_cost = labor_total + electricity_total + water_total + other_total
            period.is_complete = False
            period.missing_data_notes = (
                "لا توجد حركات صرف مواد أو نتائج إنتاج مسجلة خلال هذا الشهر؛ "
                "يُحتسب الاستهلاك بشهر حركة الصرف حتى لا تُحتسب المادة في شهرين."
            )
            period.calculated_at = timezone.now()
            period.calculated_by = actor if actor and actor.is_authenticated else None
            period.save()
            return period

        # --- Material split between good output and recorded scrap ---
        for slot in order_data.values():
            measured_waste = _cents(slot.get("measured_waste_cost", Decimal("0.00")))
            if measured_waste < 0:
                is_complete = False
                missing_notes.append(
                    f"أمر {slot['order'].order_number}: عكوس هالك المواد تتجاوز القيود المسجلة؛ راجع العكس."
                )
                measured_waste = Decimal("0.00")
            slot["waste_material_cost"] = measured_waste
            slot["good_material_cost"] = (
                _cents(max(Decimal("0.00"), slot["material_cost"] - measured_waste))
                if period.waste_policy == CostingPeriod.WastePolicy.SEPARATE_ABNORMAL
                else _cents(slot["material_cost"])
            )

        # --- Group orders by product ---
        product_data = {}

        def product_slot(product):
            return product_data.setdefault(
                product.pk,
                {
                    "product": product,
                    "orders": [],
                    "scrap_breakdown": [],
                    "good_units": Decimal("0"),
                    "scrap_units": Decimal("0"),
                    "material_cost": Decimal("0.00"),
                    "good_material_cost": Decimal("0.00"),
                    "waste_material_cost": Decimal("0.00"),
                    "material_weight": Decimal("0"),
                    "materials": {},
                    "has_missing_price": False,
                    "requires_review": False,
                    "has_unvalued_scrap": False,
                    "line_ids": set(),
                    "line_good": {},
                },
            )

        for slot in order_data.values():
            pdata = product_slot(slot["product"])
            pdata["orders"].append(slot)
            pdata["scrap_breakdown"].extend(slot.get("scrap_breakdown", []))
            pdata["good_units"] += slot["good_units"]
            pdata["scrap_units"] += slot["scrap_units"]
            pdata["material_cost"] += slot["material_cost"]
            pdata["good_material_cost"] += slot["good_material_cost"]
            pdata["waste_material_cost"] += slot["waste_material_cost"]
            pdata["has_missing_price"] = pdata["has_missing_price"] or slot["has_missing_price"]
            pdata["has_unvalued_scrap"] = pdata["has_unvalued_scrap"] or slot["has_unvalued_scrap"]
            if slot["production_line_id"]:
                line_id = slot["production_line_id"]
                pdata["line_ids"].add(line_id)
                pdata["line_good"][line_id] = pdata["line_good"].get(line_id, Decimal("0")) + slot["good_units"]
            for material_id, line in slot["materials"].items():
                pdata["material_weight"] += line["quantity_base"]
                merged = pdata["materials"].setdefault(
                    material_id,
                    {
                        "material_id": material_id,
                        "material_sku": line["material_sku"],
                        "material_name": line["material_name"],
                        "base_uom": line["base_uom"],
                        "quantity_base": Decimal("0"),
                        "unit_price": line["unit_price"],
                        "cost": Decimal("0.00"),
                        "orders": [],
                    },
                )
                merged["quantity_base"] += line["quantity_base"]
                merged["cost"] += line["cost"]
                merged["orders"].extend(line["orders"])

        # --- Attribute each product to one production line for reporting ---
        for pdata in product_data.values():
            if not pdata["line_ids"]:
                pdata["production_line_id"] = None
            elif len(pdata["line_ids"]) == 1:
                pdata["production_line_id"] = next(iter(pdata["line_ids"]))
            else:
                chosen = max(pdata["line_ids"], key=lambda lid: (pdata["line_good"][lid], -lid))
                pdata["production_line_id"] = chosen
                missing_notes.append(
                    f"المنتج {pdata['product'].sku} أُنتج على أكثر من خط إنتاج خلال الشهر؛ "
                    "يُعرض على الخط الأكثر إنتاجًا وقد لا يطابق معيار العائد المطلوب."
                )

        # --- Allocate monthly expenses ---
        direct_by_product = {
            p_id: {"labor": Decimal("0.00"), "electricity": Decimal("0.00"), "water": Decimal("0.00"), "other": Decimal("0.00")}
            for p_id in product_data
        }
        direct_by_line = {}
        shared = {"labor": Decimal("0.00"), "electricity": Decimal("0.00"), "water": Decimal("0.00"), "other": Decimal("0.00")}

        for exp in expenses:
            bucket = None
            if exp.scope == MonthlyExpense.AllocationScope.PRODUCT and exp.product_id in direct_by_product:
                bucket = direct_by_product[exp.product_id]
            elif exp.scope == MonthlyExpense.AllocationScope.LINE and exp.production_line_id:
                bucket = direct_by_line.setdefault(
                    exp.production_line_id,
                    {"labor": Decimal("0.00"), "electricity": Decimal("0.00"), "water": Decimal("0.00"), "other": Decimal("0.00")},
                )
            else:
                if exp.scope == MonthlyExpense.AllocationScope.PRODUCT:
                    is_complete = False
                    missing_notes.append(
                        f"مصروف مخصص للمنتج {exp.product_id} بدون إنتاج مسجل له هذا الشهر؛ "
                        "تم توزيعه كمصروف مشترك والنتيجة تحتاج مراجعة."
                    )
                    for pdata in product_data.values():
                        pdata["requires_review"] = True
                bucket = shared
            if exp.expense_type == MonthlyExpense.ExpenseType.LABOR:
                bucket["labor"] += exp.amount
            elif exp.expense_type == MonthlyExpense.ExpenseType.ELECTRICITY:
                bucket["electricity"] += exp.amount
            elif exp.expense_type == MonthlyExpense.ExpenseType.WATER:
                bucket["water"] += exp.amount
            elif exp.expense_type == MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL:
                bucket["other"] += exp.amount
            # MAINTENANCE is a period operating expense deducted once by the
            # monthly profit calculation. It is intentionally not allocated into
            # product manufacturing cost, so it cannot be deducted twice.

        ALLOCATION_FIELDS = {
            "labor": "labor_allocated",
            "electricity": "electricity_allocated",
            "water": "water_allocated",
            "other": "other_allocated",
        }

        for pdata in product_data.values():
            pdata["labor_allocated"] = Decimal("0.00")
            pdata["electricity_allocated"] = Decimal("0.00")
            pdata["water_allocated"] = Decimal("0.00")
            pdata["other_allocated"] = Decimal("0.00")

        # Line-level expenses go only to products recorded on that line.
        for line_id, line_totals in direct_by_line.items():
            line_products = [p for p in product_data.values() if p["production_line_id"] == line_id]
            if not line_products:
                is_complete = False
                missing_notes.append(
                    f"مصروف مخصص لخط الإنتاج {line_id} بدون إنتاج مسجل على هذا الخط هذا الشهر؛ "
                    "تم توزيعه على باقي المنتجات والنتيجة تحتاج مراجعة."
                )
                spread = list(product_data.values())
                for pdata in spread:
                    pdata["requires_review"] = True
                even = Decimal("1") / Decimal(len(spread)) if spread else Decimal("0")
                _distribute_bucket(
                    [(pdata, even) for pdata in spread],
                    line_totals,
                    ALLOCATION_FIELDS,
                    "_linefallback",
                )
                continue
            line_units = sum(p["good_units"] for p in line_products)
            if line_units > 0:
                assignments = [(pdata, pdata["good_units"] / line_units) for pdata in line_products]
            else:
                is_complete = False
                missing_notes.append(
                    "مصروف خاص بخط إنتاج بدون وحدات جيدة مسجلة؛ تم توزيعه بالتساوي على منتجات الخط والنتيجة غير نهائية وتحتاج مراجعة."
                )
                for pdata in line_products:
                    pdata["requires_review"] = True
                even = Decimal("1") / Decimal(len(line_products))
                assignments = [(pdata, even) for pdata in line_products]
            _distribute_bucket(assignments, line_totals, ALLOCATION_FIELDS, "_line")

        for p_id, totals in direct_by_product.items():
            pdata = product_data[p_id]
            pdata["labor_allocated"] += totals["labor"]
            pdata["electricity_allocated"] += totals["electricity"]
            pdata["water_allocated"] += totals["water"]
            pdata["other_allocated"] += totals["other"]

        # Labor, electricity, and water are required monthly operating records:
        # a month with production must not silently treat an unrecorded item as
        # zero. An explicitly recorded 0.00 remains a confirmed zero.
        if not labor_recorded:
            is_complete = False
            missing_notes.append(
                "لم تُسجل مصاريف العمالة لهذا الشهر رغم وجود إنتاج؛ لا يُفترض أن قيمتها صفر — "
                "سجّل مصاريف العمالة الفعلية، أو 0.00 صراحةً عند تأكيد عدم وجود مصروف."
            )
        if not electricity_recorded:
            is_complete = False
            missing_notes.append(
                "لم تُسجل مصاريف الكهرباء لهذا الشهر رغم وجود إنتاج؛ لا يُفترض أن قيمتها صفر — سجّل فاتورة الكهرباء الفعلية."
            )
        if not water_recorded:
            is_complete = False
            missing_notes.append(
                "لم تُسجل مصاريف المياه لهذا الشهر رغم وجود إنتاج؛ لا يُفترض أن قيمتها صفر — سجّل فاتورة المياه الفعلية."
            )

        # Factory-wide shared expenses by the owner-selected policy.
        policy = period.allocation_policy
        total_good_units = sum(p["good_units"] for p in product_data.values())
        total_material_cost = sum(p["material_cost"] for p in product_data.values())
        total_material_weight = sum(p["material_weight"] for p in product_data.values())
        num_products = Decimal(len(product_data))

        if policy == CostingPeriod.AllocationPolicy.UNITS_PRODUCED and total_good_units <= 0:
            missing_notes.append(
                "سياسة التوزيع «حسب الوحدات الجيدة» لا توجد وحدات جيدة مسجلة؛ تم توزيع المصاريف بالتساوي والنتيجة غير نهائية."
            )
            is_complete = False
        elif policy == CostingPeriod.AllocationPolicy.MATERIAL_COST and total_material_cost <= 0:
            missing_notes.append(
                "سياسة التوزيع «حسب تكلفة المواد» لا توجد مواد مستهلكة مسعرة؛ تم توزيع المصاريف بالتساوي والنتيجة غير نهائية."
            )
            is_complete = False
        elif policy == CostingPeriod.AllocationPolicy.MATERIAL_WEIGHT and total_material_weight <= 0:
            missing_notes.append(
                "سياسة التوزيع «حسب كمية المواد» لا توجد كميات مستهلكة؛ تم توزيع المصاريف بالتساوي والنتيجة غير نهائية."
            )
            is_complete = False

        allocations = list(product_data.values())
        shared_assignments = []
        for pdata in allocations:
            if policy == CostingPeriod.AllocationPolicy.UNITS_PRODUCED and total_good_units > 0:
                ratio = pdata["good_units"] / total_good_units
            elif policy == CostingPeriod.AllocationPolicy.MATERIAL_COST and total_material_cost > 0:
                ratio = pdata["material_cost"] / total_material_cost
            elif policy == CostingPeriod.AllocationPolicy.MATERIAL_WEIGHT and total_material_weight > 0:
                ratio = pdata["material_weight"] / total_material_weight
            elif policy == CostingPeriod.AllocationPolicy.EQUAL_PER_LINE:
                lines = {p["production_line_id"] for p in allocations}
                same_line = [p for p in allocations if p["production_line_id"] == pdata["production_line_id"]]
                ratio = (Decimal("1") / Decimal(len(lines))) / Decimal(len(same_line))
            else:
                ratio = Decimal("1") / num_products
            shared_assignments.append((pdata, ratio))
        _distribute_bucket(shared_assignments, shared, ALLOCATION_FIELDS, "_shared")

        # --- Distribute each product's allocation across its own orders ---
        for pdata in product_data.values():
            orders = sorted(pdata["orders"], key=lambda s: s["order"].order_number)
            order_good_units = sum(s["good_units"] for s in orders)
            for key, product_total in (
                ("labor_allocated", pdata["labor_allocated"]),
                ("electricity_allocated", pdata["electricity_allocated"]),
                ("water_allocated", pdata["water_allocated"]),
                ("other_allocated", pdata["other_allocated"]),
            ):
                for slot in orders:
                    if order_good_units > 0:
                        ratio = slot["good_units"] / order_good_units
                    else:
                        ratio = Decimal("1") / Decimal(len(orders))
                    slot[key] = _cents(product_total * ratio)
                _reconcile_allocations(orders, product_total, key)
            for slot in orders:
                slot["total_cost"] = (
                    slot["good_material_cost"]
                    + slot["labor_allocated"]
                    + slot["electricity_allocated"]
                    + slot["water_allocated"]
                    + slot["other_allocated"]
                )
                if slot["good_units"] > 0:
                    slot["cost_per_good_unit"] = (slot["total_cost"] / slot["good_units"]).quantize(
                        FOUR_DECIMALS, rounding=ROUND_HALF_UP
                    )
                else:
                    slot["cost_per_good_unit"] = None

        # --- Persist order-level entries ---
        period.order_entries.all().delete()
        order_entries = []
        for slot in sorted(order_data.values(), key=lambda s: s["order"].order_number):
            order_entries.append(
                ProductionOrderCostingEntry(
                    period=period,
                    order=slot["order"],
                    product=slot["product"],
                    production_line_id=slot["production_line_id"],
                    good_units_produced=slot["good_units"],
                    scrap_units_produced=slot["scrap_units"],
                    actual_material_cost=slot["material_cost"],
                    material_cost_assigned_to_good=slot["good_material_cost"],
                    waste_material_cost=slot["waste_material_cost"],
                    allocated_labor_cost=slot.get("labor_allocated", Decimal("0.00")),
                    allocated_electricity_cost=slot.get("electricity_allocated", Decimal("0.00")),
                    allocated_water_cost=slot.get("water_allocated", Decimal("0.00")),
                    allocated_other_cost=slot.get("other_allocated", Decimal("0.00")),
                    total_production_cost=slot.get("total_cost", slot["good_material_cost"]),
                    cost_per_good_unit=slot.get("cost_per_good_unit"),
                    calculation_status=(
                        "INCOMPLETE"
                        if slot["has_missing_price"] or slot["has_unvalued_scrap"] or slot["good_units"] <= 0
                        else "COMPLETE"
                    ),
                    scrap_breakdown=slot.get("scrap_breakdown", []),
                )
            )
        ProductionOrderCostingEntry.objects.bulk_create(order_entries)

        # --- Persist product-level entries ---
        _release_draft_cogs_source_links(period)
        period.product_entries.all().delete()
        product_entries = []
        grand_materials = Decimal("0.00")
        grand_total = Decimal("0.00")
        grand_waste = Decimal("0.00")

        for pdata in sorted(product_data.values(), key=lambda p: p["product"].sku):
            product = pdata["product"]
            line_id = pdata["production_line_id"]
            attributed_line = ProductionLine.objects.filter(pk=line_id).first() if line_id else None
            yield_notes = []
            analysis = _yield_analysis(
                product,
                attributed_line,
                {m["material_id"]: m["quantity_base"] for m in pdata["materials"].values()},
                pdata["good_units"],
                yield_notes,
            )
            for note in yield_notes:
                if note not in missing_notes:
                    missing_notes.append(note)

            if pdata["good_units"] > 0:
                material_cost_per_unit = (
                    pdata["good_material_cost"] / pdata["good_units"]
                ).quantize(FOUR_DECIMALS, rounding=ROUND_HALF_UP)
                cost_per_unit = (pdata["good_material_cost"] + pdata["labor_allocated"]
                                 + pdata["electricity_allocated"] + pdata["water_allocated"]
                                 + pdata["other_allocated"]) / pdata["good_units"]
                cost_per_unit = cost_per_unit.quantize(FOUR_DECIMALS, rounding=ROUND_HALF_UP)
            else:
                material_cost_per_unit = None
                cost_per_unit = None
                is_complete = False
                pdata["requires_review"] = True
                missing_notes.append(
                    f"لا توجد وحدات جيدة مسجلة للمنتج {product.sku} هذا الشهر؛ لا يمكن حساب تكلفة الوحدة الجيدة، والنتيجة تحتاج مراجعة."
                )

            if pdata["material_cost"] == 0 and (pdata["good_units"] > 0 or pdata["scrap_units"] > 0):
                is_complete = False
                pdata["has_missing_price"] = True
                missing_notes.append(
                    f"المنتج {product.sku} لديه إنتاج مسجل هذا الشهر بدون صرف مواد خلال نفس الشهر؛ "
                    "قد تكون المواد صُرفت في شهر سابق — تكلفة المواد لهذه الفترة غير مكتملة."
                )

            selling_price_obj = ProductSellingPrice.objects.filter(product=product).first()
            breakdown = [
                {
                    **{k: (str(v) if isinstance(v, Decimal) else v) for k, v in line.items() if k != "orders"},
                    "orders": sorted(set(line["orders"])),
                }
                for line in pdata["materials"].values()
            ]

            product_total = (
                pdata["good_material_cost"]
                + pdata["labor_allocated"]
                + pdata["electricity_allocated"]
                + pdata["water_allocated"]
                + pdata["other_allocated"]
            )
            grand_materials += pdata["material_cost"]
            grand_total += product_total
            grand_waste += pdata["waste_material_cost"]

            product_entries.append(
                ProductCostingEntry(
                    period=period,
                    product=product,
                    production_line_id=pdata["production_line_id"],
                    good_units_produced=pdata["good_units"],
                    scrap_units_produced=pdata["scrap_units"],
                    rolls_consumed=analysis["rolls_consumed"],
                    expected_cartons=analysis["expected_cartons"],
                    actual_good_cartons=analysis["actual_good_cartons"],
                    yield_shortfall_percent=analysis["yield_shortfall_percent"],
                    yield_variance_cartons=analysis["yield_variance_cartons"],
                    actual_material_cost=pdata["material_cost"],
                    material_cost_assigned_to_good=pdata["good_material_cost"],
                    waste_material_cost=pdata["waste_material_cost"],
                    allocated_labor_cost=pdata["labor_allocated"],
                    allocated_electricity_cost=pdata["electricity_allocated"],
                    allocated_water_cost=pdata["water_allocated"],
                    allocated_other_cost=pdata["other_allocated"],
                    total_production_cost=product_total,
                    cost_per_good_unit=cost_per_unit,
                    material_cost_per_good_unit=material_cost_per_unit,
                    selling_price_snapshot=selling_price_obj.selling_price if selling_price_obj else None,
                    material_breakdown=breakdown,
                    scrap_breakdown=pdata["scrap_breakdown"],
                    # Historical snapshot: the exact standard (revision + approved
                    # thresholds) used at calculation time, so later spec edits
                    # never rewrite this period's classification basis.
                    yield_standard=analysis.get("standard"),
                    yield_standard_revision=(
                        analysis["standard"].revision if analysis.get("standard") else None
                    ),
                    yield_scrap_threshold_percent=(
                        analysis["standard"].max_scrap_percent if analysis.get("standard") else None
                    ),
                    yield_target_percent=(
                        analysis["standard"].target_yield_percent if analysis.get("standard") else None
                    ),
                    calculation_status=(
                        "INCOMPLETE"
                        if pdata["has_missing_price"] or pdata.get("requires_review") or pdata["has_unvalued_scrap"]
                        else "COMPLETE"
                    ),
                )
            )

        ProductCostingEntry.objects.bulk_create(product_entries)

        period.materials_total = grand_materials
        period.total_production_cost = grand_total
        period.is_complete = is_complete and not any(
            p["has_missing_price"] or p.get("requires_review")
            for p in product_data.values()
        )
        header = [
            "طريقة تقييم المواد الحالية: آخر سعر شراء للمادة (آخر استلام مشتريات ناجح). "
            "الأرقام مؤقتة إلى حين اعتماد طريقة تقييم من المحاسب؛ لا تُحتسب بـ FIFO أو المتوسط المرجح.",
            f"إجمالي تكلفة الهالك المنفصلة هذا الشهر: {grand_waste} ج.م "
            + (
                "(سياسة تحميل الهالك على الوحدات الجيدة؛ تكلفة المواد كاملة محمّلة على الجيد)."
                if period.waste_policy == CostingPeriod.WastePolicy.ABSORBED_NORMAL
                else "(سياسة فصل الهالك؛ تُعرض منفصلة ولا تُضاف فوق إجمالي تكلفة المواد)."
            ),
        ]
        period.missing_data_notes = "\n".join(header + sorted(set(missing_notes)))
        period.calculated_at = timezone.now()
        period.calculated_by = actor if actor and actor.is_authenticated else None
        period.save()

        return period


def finalize_costing_period(*, period, actor):
    """Finalize a monthly costing period after verifying its data is complete."""
    require_permission(actor, "costing.finalize_costing_period", "اعتماد فترة التكاليف")
    if not actor or not actor.is_authenticated or not actor.is_staff:
        raise ValidationError("اعتماد فترة التكاليف متاح للمستخدمين المصرح لهم فقط.")

    with transaction.atomic():
        # Locking protocol: costing month first, then profit month, before any
        # row lock. This makes finalization mutually exclusive with every
        # operational write and with a concurrent profit finalization, whether
        # or not the period rows already exist.
        lock_costing_month(period.year, period.month)
        lock_profit_month(period.year, period.month)
        period = CostingPeriod.objects.select_for_update().get(pk=period.pk)
        if period.status == CostingPeriod.Status.FINALIZED:
            raise ValidationError("هذه الفترة معتمدة نهائيًا بالفعل.")

        # Recalculate immediately before freezing so a stale draft can never
        # persist values from before a later issue, output record, price, or
        # expense. The row lock is held for the whole recalculation. A period
        # without production-order detail has no production source to refresh;
        # it is normally incomplete and will fail the completeness check below.
        if period.order_entries.exists():
            period = calculate_monthly_costing(
                year=period.year, month=period.month, actor=actor
            )
        if not period.is_complete:
            raise ValidationError("لا يمكن اعتماد فترة التكاليف لوجود بيانات ناقصة أو أسعار غير محددة.")

        period.status = CostingPeriod.Status.FINALIZED
        period.finalized_at = timezone.now()
        period.finalized_by = actor
        period.save(update_fields=["status", "finalized_at", "finalized_by", "updated_at"])
        return period


def reopen_costing_period(*, period, actor, reason):
    """Reopen a finalized period with a required audit reason."""
    require_permission(actor, "costing.reopen_costing_period", "إعادة فتح فترة التكاليف")
    if not actor or not actor.is_authenticated or not actor.is_staff:
        raise ValidationError("إعادة فتح فترة التكاليف متاح للمستخدمين المصرح لهم فقط.")
    if not reason or not reason.strip():
        raise ValidationError({"reason": "سبب إعادة فتح فترة التكاليف إلزامي لتوثيق سجل التدقيق."})

    with transaction.atomic():
        # Costing then profit month locks (locking protocol): reopening checks
        # the profit status of the same month, so it must serialize against a
        # concurrent profit finalization too.
        lock_costing_month(period.year, period.month)
        lock_profit_month(period.year, period.month)
        period = CostingPeriod.objects.select_for_update().get(pk=period.pk)
        if period.status != CostingPeriod.Status.FINALIZED:
            raise ValidationError("يمكن إعادة الفترات المعتمدة نهائيًا فقط.")
        if MonthlyProfitPeriod.objects.filter(
            year=period.year,
            month=period.month,
            status=MonthlyProfitPeriod.Status.FINALIZED,
        ).exists():
            raise ValidationError(
                "لا يمكن إعادة فتح فترة التكاليف بعد اعتماد نتيجة الشهر المالي؛ "
                "أعد فتح فترة الأرباح والخسائر أولاً."
            )
        source_usage = (
            MonthlyProfitCOGSLine.objects.filter(
                source_period=period,
                profit_period__status=MonthlyProfitPeriod.Status.FINALIZED,
            )
            .select_related("profit_period")
            .order_by("-profit_period__year", "-profit_period__month")
            .first()
        )
        if source_usage is not None:
            used_by = source_usage.profit_period
            raise ValidationError(
                "لا يمكن إعادة فتح فترة التكاليف لأنها مصدر تكلفة البضاعة المباعة "
                f"لفترة أرباح معتمدة ({used_by.year}/{used_by.month:02d})؛ "
                "أعد فتح فترة الأرباح والخسائر تلك أولاً."
            )

        period.status = CostingPeriod.Status.DRAFT
        period.reopened_at = timezone.now()
        period.reopened_by = actor
        period.reopen_reason = reason.strip()
        period.save(update_fields=["status", "reopened_at", "reopened_by", "reopen_reason", "updated_at"])
        return period
