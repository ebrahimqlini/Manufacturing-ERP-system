"""Monthly management profit, operating expenses, and auditable loss carry-forward.

The module reads persisted shipment, costing, and expense records. It does not
recalculate product costs or re-allocate operating expenses between production
lines. COGS is the persisted direct-material cost of the quantities actually
shipped; labor, electricity, maintenance, and other operating expenses are
deducted once as period expenses. Water is deliberately outside this agreed
calculation.
"""

import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from sales.models import SalesOrder, ShipmentLine
from manufacturing.models import ProductionResult, ProductionMaterialWaste

from .yield_services import absorbed_scrap_estimate, separated_scrap_components

from .models import (
    CostingPeriod,
    MonthlyExpense,
    MonthlyProfitCOGSLine,
    MonthlyProfitPeriod,
    ProductCostingEntry,
    lock_costing_month,
    lock_profit_month,
)

from core.permissions import require_permission, require_staff

MONEY = Decimal("0.01")
SIX_PLACES = Decimal("0.000001")


def _staff(actor):
    require_staff(actor)


def _money(value):
    return (value or Decimal("0")).quantize(MONEY, rounding=ROUND_HALF_UP)


def _month_range(year, month):
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _expense_label(expense_type):
    return dict(MonthlyExpense.ExpenseType.choices).get(expense_type, expense_type)


def _previous_finalized(year, month):
    return (
        MonthlyProfitPeriod.objects.filter(
            status=MonthlyProfitPeriod.Status.FINALIZED,
        )
        .filter(Q(year__lt=year) | Q(year=year, month__lt=month))
        .order_by("-year", "-month")
        .first()
    )


def _latest_costing_entry(product, year, month):
    """Latest persisted costing entry for a product on or before the month."""
    entries = (
        ProductCostingEntry.objects.filter(product=product)
        .filter(
            Q(period__year__lt=year)
            | Q(period__year=year, period__month__lte=month)
        )
        .select_related("period")
        .order_by("-period__year", "-period__month", "-pk")
    )
    for entry in entries:
        if entry.good_units_produced and entry.good_units_produced > 0:
            return entry
    return None


def _costing_is_authoritative(entry):
    return bool(
        entry
        and entry.period.status == CostingPeriod.Status.FINALIZED
        and entry.period.is_complete
        and entry.calculation_status == "COMPLETE"
    )


def _active_sales_lines(start, end):
    return (
        ShipmentLine.objects.filter(
            shipment__shipment_date__range=(start, end),
            shipment__order__status__in=(
                SalesOrder.Status.CONFIRMED,
                SalesOrder.Status.COMPLETED,
            ),
            movement__reversal__isnull=True,
        )
        .select_related(
            "shipment",
            "order_line",
            "order_line__product",
            "order_line__product__base_uom",
        )
        .order_by("shipment__shipment_date", "shipment__pk", "sequence")
    )


def _revenue_groups(lines):
    grouped = {}
    shipment_ids = set()
    line_count = 0
    for line in lines:
        line_count += 1
        shipment_ids.add(line.shipment_id)
        product = line.order_line.product
        row = grouped.setdefault(
            product.pk,
            {
                "product": product,
                "quantity": Decimal("0"),
                "revenue": Decimal("0.00"),
                "shipments": set(),
                "line_count": 0,
            },
        )
        row["quantity"] += line.quantity_base
        row["revenue"] += line.line_total
        row["shipments"].add(line.shipment.reference)
        row["line_count"] += 1
    return grouped, len(shipment_ids), line_count


def _expense_totals(year, month):
    expenses = list(
        MonthlyExpense.objects.filter(
            year=year,
            month=month,
            status=MonthlyExpense.Status.ACTIVE,
        ).order_by("expense_type", "expense_date", "pk")
    )
    totals = {
        MonthlyExpense.ExpenseType.LABOR: Decimal("0.00"),
        MonthlyExpense.ExpenseType.ELECTRICITY: Decimal("0.00"),
        MonthlyExpense.ExpenseType.MAINTENANCE: Decimal("0.00"),
        MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL: Decimal("0.00"),
    }
    recorded = {key: False for key in totals}
    for expense in expenses:
        if expense.expense_type in totals:
            totals[expense.expense_type] += expense.amount
            recorded[expense.expense_type] = True
    return expenses, {key: _money(value) for key, value in totals.items()}, recorded


def calculate_monthly_profit(*, year, month, actor=None):
    """Calculate and persist one month's management result and loss carry-forward.

    Revenue is recognized from active shipment lines in the month. A credit sale
    is included when shipped even if no payment collection record exists. COGS is
    computed only for quantities actually shipped, using the persisted direct
    material cost per good unit from the latest available costing entry. Products
    manufactured but not sold remain inventory and are not expensed.

    Separated scrap (SEPARATE_ABNORMAL policy) is deducted exactly once as a
    loss line — only for completed orders with complete entries — because that
    material was excluded from unit cost and COGS. Scrap absorbed under the
    normal policy is never deducted again. The formula is:
    ``monthly_result = gross_profit − operating_total − separated_scrap_loss``.
    """
    require_permission(actor, "costing.calculate_monthly_profit", "احتساب الأرباح الشهرية")
    require_staff(actor)
    try:
        year = int(year)
        month = int(month)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"month": "السنة أو الشهر غير صالح."}) from exc
    if not 2020 <= year <= 2100:
        raise ValidationError({"year": "السنة يجب أن تكون بين 2020 و2100."})
    if not 1 <= month <= 12:
        raise ValidationError({"month": "الشهر يجب أن يكون بين 1 و12."})
    start, end = _month_range(year, month)

    with transaction.atomic():
        # Profit-month advisory lock first (locking protocol): serializes with
        # finalization/reopen/shipments and with the first creation of this
        # month's period row. get_or_create under a row lock plus this lock
        # means concurrent first calculations never race on the unique
        # year/month constraint and a stale recalculation can never overwrite
        # a concurrent finalization.
        lock_profit_month(year, month)
        # Use get_or_create under row lock: concurrent first calculations
        # serialize instead of racing to satisfy the unique month constraint.
        period, _ = MonthlyProfitPeriod.objects.select_for_update().get_or_create(
            year=year,
            month=month,
            defaults={"status": MonthlyProfitPeriod.Status.DRAFT},
        )
        if period.status == MonthlyProfitPeriod.Status.FINALIZED:
            raise ValidationError(
                "لا يمكن إعادة احتساب نتيجة شهر معتمدة نهائيًا؛ أعد فتح فترة الأرباح والخسائر أولاً."
            )
        costing_period = CostingPeriod.objects.filter(year=year, month=month).first()

        lines = list(_active_sales_lines(start, end))
        groups, shipment_count, line_count = _revenue_groups(lines)
        notes = []

        cogs_lines = []
        cogs_total = Decimal("0.00")
        cogs_complete = True
        revenue_total = Decimal("0.00")
        for product_id in sorted(groups, key=lambda key: groups[key]["product"].sku):
            row = groups[product_id]
            product = row["product"]
            entry = _latest_costing_entry(product, year, month)
            unit_cost = None
            status = MonthlyProfitCOGSLine.CalculationStatus.MISSING_COST
            notes_for_line = []
            if entry is None:
                cogs_complete = False
                notes_for_line.append(
                    f"لا توجد تكلفة مواد محفوظة للمنتج {product.sku} ({product.name_ar}) "
                    "قبل أو خلال هذا الشهر؛ لم تُفترض تكلفة صفر."
                )
            else:
                unit_cost = entry.material_cost_per_good_unit
                if unit_cost is None and entry.good_units_produced:
                    # Legacy safety: the field is populated by current service
                    # output and by the migration, but never invent a cost if
                    # the stored entry lacks a usable direct-material amount.
                    unit_cost = None
                if unit_cost is None:
                    cogs_complete = False
                    notes_for_line.append(
                        f"بند تكلفة المنتج {product.sku} لا يحتوي تكلفة مواد مباشرة "
                        "صالحة للوحدة الجيدة؛ لم تُفترض قيمة."
                    )
                elif not _costing_is_authoritative(entry):
                    cogs_complete = False
                    status = MonthlyProfitCOGSLine.CalculationStatus.NON_AUTHORITATIVE
                    notes_for_line.append(
                        f"تكلفة المنتج {product.sku} مأخوذة من فترة تكاليف غير معتمدة أو "
                        f"غير مكتملة ({entry.period.year}/{entry.period.month:02d})."
                    )
                else:
                    status = MonthlyProfitCOGSLine.CalculationStatus.COMPLETE
                    if (entry.period.year, entry.period.month) != (year, month):
                        notes.append(
                            f"المنتج {product.sku}: استُخدمت أحدث تكلفة مواد معتمدة من "
                            f"{entry.period.year}/{entry.period.month:02d} لأن مبيعات الشهر "
                            "قد تشمل مخزونًا سابقًا. هذه ليست قيمة مخزون محاسبية كاملة."
                        )

            cogs = (
                _money(row["quantity"] * unit_cost)
                if unit_cost is not None
                else Decimal("0.00")
            )
            if status == MonthlyProfitCOGSLine.CalculationStatus.MISSING_COST:
                cogs = Decimal("0.00")
            revenue = _money(row["revenue"])
            revenue_total += revenue
            cogs_total += cogs

            cogs_lines.append(
                MonthlyProfitCOGSLine(
                    profit_period=period,
                    product=product,
                    quantity_sold_base=row["quantity"],
                    base_uom_code=product.base_uom.code,
                    base_uom_name_ar=product.base_uom.name_ar,
                    revenue=revenue,
                    cost_per_base_unit=(
                        unit_cost.quantize(SIX_PLACES, rounding=ROUND_HALF_UP)
                        if unit_cost is not None
                        else None
                    ),
                    cogs=cogs,
                    shipment_count=len(row["shipments"]),
                    source_costing_entry=entry,
                    source_period=entry.period if entry else None,
                    calculation_status=status,
                    shipment_references=sorted(row["shipments"]),
                    # The field is intentionally bounded; the full audit note
                    # remains in the month-level missing_data_notes text.
                    notes=" ".join(notes_for_line)[:255],
                )
            )
            notes.extend(notes_for_line)

        revenue_total = _money(revenue_total)
        cogs_total = _money(cogs_total)
        expenses, expense_totals, recorded = _expense_totals(year, month)
        missing_expenses = [
            expense_type
            for expense_type in (
                MonthlyExpense.ExpenseType.LABOR,
                MonthlyExpense.ExpenseType.ELECTRICITY,
                MonthlyExpense.ExpenseType.MAINTENANCE,
                MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL,
            )
            if not recorded[expense_type]
        ]
        for expense_type in missing_expenses:
            notes.append(
                f"لم تُسجل {_expense_label(expense_type)} لهذا الشهر؛ "
                "غياب السجل لا يعني قيمة صفر مؤكدة."
            )
        if not expenses:
            notes.append("لا توجد أي سجلات مصاريف تشغيلية نشطة لهذا الشهر.")
        if any(
            expense.expense_type == MonthlyExpense.ExpenseType.WATER for expense in expenses
        ):
            notes.append(
                "مصاريف المياه مسجلة لأغراض التكاليف لكنها مستبعدة من حساب الربح "
                "التشغيلي المتفق عليه؛ لا تُخصم مرة أخرى هنا."
            )

        labor = expense_totals[MonthlyExpense.ExpenseType.LABOR]
        electricity = expense_totals[MonthlyExpense.ExpenseType.ELECTRICITY]
        maintenance = expense_totals[MonthlyExpense.ExpenseType.MAINTENANCE]
        other = expense_totals[MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL]
        operating_total = _money(labor + electricity + maintenance + other)
        gross_profit = _money(revenue_total - cogs_total)

        # --- Separated scrap: deducted once, only when truly outside cost ---
        # Under SEPARATE_ABNORMAL the recorded scrap material was excluded from
        # material_cost_per_good_unit (and therefore from COGS), so its
        # persisted waste_material_cost is deducted here as a loss line — but
        # only for COMPLETED orders with COMPLETE entries; the rest is
        # reported as unreconciled and never deducted or fabricated. Under
        # ABSORBED_NORMAL nothing is deducted: the scrap already rides inside
        # unit cost → COGS, and deducting again would double-count it.
        separated_scrap, unreconciled_scrap, scrap_notes = separated_scrap_components(
            period=costing_period
        )
        absorbed_scrap = absorbed_scrap_estimate(period=costing_period)
        notes.extend(scrap_notes)
        active_waste_evidence = ProductionMaterialWaste.objects.filter(
            result_id=OuterRef("pk"), reversal_of__isnull=True, reversal__isnull=True
        )
        unresolved_waste_reviews = ProductionResult.objects.filter(
            posted_at__date__gte=start, posted_at__date__lte=end,
            correction__isnull=True,
            waste_review__isnull=True,
        ).annotate(has_active_waste_evidence=Exists(active_waste_evidence)).filter(
            has_active_waste_evidence=False
        ).count()
        unvalued_material_waste = ProductionMaterialWaste.objects.filter(
            posted_at__date__gte=start, posted_at__date__lte=end,
            total_cost_snapshot__isnull=True,
        ).count()
        legacy_unknown_results = ProductionResult.objects.filter(
            posted_at__isnull=True, occurred_at__date__gte=start, occurred_at__date__lte=end,
        ).count()
        if unresolved_waste_reviews:
            notes.append(
                f"توجد {unresolved_waste_reviews} نتيجة إنتاج لم تكتمل مراجعة المواد التالفة فيها؛ "
                "سجّل القياس المادي أو أكد صراحة عدم وجوده."
            )
        if unvalued_material_waste:
            notes.append(
                f"يوجد {unvalued_material_waste} سجل هالك مواد بلا تقييم موثوق وقت الترحيل."
            )
        if legacy_unknown_results:
            notes.append(
                f"توجد {legacy_unknown_results} نتيجة إنتاج قديمة بلا وقت ترحيل موثوق؛ "
                "لا يمكن نسبتها ماليًا إلى هذا الشهر من تاريخ الإدخال."
            )
        if costing_period is None:
            scrap_treatment = MonthlyProfitPeriod.ScrapTreatment.NO_COSTING
            notes.append(
                "لا توجد فترة تكاليف مرتبطة بهذا الشهر؛ لم تُحتسب أي تكلفة هالك معتمدة."
            )
        elif separated_scrap > 0:
            scrap_treatment = MonthlyProfitPeriod.ScrapTreatment.SEPARATED
            notes.append(
                f"خسارة هالك مفصلة مخصومة مرة واحدة: {separated_scrap} ج.م "
                "(كانت مستبعدة أصلًا من تكلفة الوحدات الجيدة وCOGS بسياسة فصل الهالك)."
            )
        elif unreconciled_scrap > 0:
            scrap_treatment = MonthlyProfitPeriod.ScrapTreatment.SEPARATED
            notes.append(
                f"هالك مفصل بقيمة {unreconciled_scrap} ج.م غير مخصوم بعد (أوامر غير مكتملة "
                "أو احتساب غير مكتمل)؛ يظهر في «هالك غير محوَّل» ولا يُخصم من النتيجة."
            )
        elif absorbed_scrap > 0:
            scrap_treatment = MonthlyProfitPeriod.ScrapTreatment.ABSORBED
            notes.append(
                f"تكلفة الهالك المقدّرة {absorbed_scrap} ج.م محمّلة ضمن تكلفة الوحدات "
                "الجيدة ومنها تكلفة البضاعة المباعة؛ تُعرض ولا تُخصم مرة أخرى."
            )
        else:
            scrap_treatment = MonthlyProfitPeriod.ScrapTreatment.NONE
        monthly_result = _money(gross_profit - operating_total - separated_scrap)

        previous = _previous_finalized(year, month)
        opening_loss = _money(
            previous.closing_accumulated_loss if previous else Decimal("0.00")
        )
        if monthly_result > 0:
            loss_recovered = _money(min(monthly_result, opening_loss))
            closing_loss = _money(opening_loss - loss_recovered)
            profit_after_recovery = _money(monthly_result - loss_recovered)
        else:
            loss_recovered = Decimal("0.00")
            closing_loss = _money(opening_loss + abs(min(monthly_result, Decimal("0.00"))))
            profit_after_recovery = Decimal("0.00")

        expenses_complete = not missing_expenses
        period.start_date = start
        period.end_date = end
        period.costing_period = costing_period
        period.revenue = revenue_total
        period.cogs = cogs_total
        period.gross_profit = gross_profit
        period.labor_expense = labor
        period.electricity_expense = electricity
        period.maintenance_expense = maintenance
        period.other_expense = other
        period.operating_expenses_total = operating_total
        period.separated_scrap_loss = separated_scrap
        period.absorbed_scrap_cost = absorbed_scrap
        period.unreconciled_scrap_cost = unreconciled_scrap
        period.scrap_treatment = scrap_treatment
        period.monthly_result = monthly_result
        period.opening_accumulated_loss = opening_loss
        period.loss_recovered = loss_recovered
        period.closing_accumulated_loss = closing_loss
        period.profit_after_loss_recovery = profit_after_recovery
        period.labor_recorded = recorded[MonthlyExpense.ExpenseType.LABOR]
        period.electricity_recorded = recorded[MonthlyExpense.ExpenseType.ELECTRICITY]
        period.maintenance_recorded = recorded[MonthlyExpense.ExpenseType.MAINTENANCE]
        period.other_recorded = recorded[MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL]
        period.cogs_complete = cogs_complete
        # Do not finalize a month while separated scrap remains unreconciled.
        # Absorbed scrap remains inside COGS and is never deducted a second time.
        scrap_complete = (
            unreconciled_scrap == 0 and not unresolved_waste_reviews
            and not unvalued_material_waste and not legacy_unknown_results
        )
        period.is_complete = bool(cogs_complete and expenses_complete and scrap_complete)
        period.missing_data_notes = "\n".join(sorted(set(note for note in notes if note)))
        period.active_shipment_count = shipment_count
        period.active_shipment_line_count = line_count
        period.revenue_product_count = len(groups)
        period.expense_record_count = len(expenses)
        period.calculated_at = timezone.now()
        period.calculated_by = actor if actor and actor.is_authenticated else None
        period.save()

        period.cogs_lines.all().delete()
        if cogs_lines:
            MonthlyProfitCOGSLine.objects.bulk_create(cogs_lines)
        return period


def finalize_monthly_profit(*, period, actor):
    """Finalize a complete month and refresh later draft carry-forward rows."""
    require_permission(actor, "costing.finalize_monthly_profit", "اعتماد نتيجة الشهر")
    _staff(actor)

    with transaction.atomic():
        # Profit-month advisory lock before any row lock (locking protocol):
        # finalization is mutually exclusive with every operational write for
        # the same month, even before the period row exists.
        lock_profit_month(period.year, period.month)
        period = MonthlyProfitPeriod.objects.select_for_update().get(pk=period.pk)
        if period.status == MonthlyProfitPeriod.Status.FINALIZED:
            raise ValidationError("هذه الفترة معتمدة نهائيًا بالفعل.")
        later_finalized = MonthlyProfitPeriod.objects.filter(
            status=MonthlyProfitPeriod.Status.FINALIZED
        ).filter(
            Q(year__gt=period.year)
            | Q(year=period.year, month__gt=period.month)
        )
        if later_finalized.exists():
            raise ValidationError(
                "لا يمكن اعتماد شهر أقدم قبل إعادة فتح الأشهر اللاحقة المعتمدة؛ "
                "حتى لا يتغير رصيد الخسائر المتراكمة بأثر رجعي دون توثيق."
            )
        earlier_drafts = MonthlyProfitPeriod.objects.filter(
            status=MonthlyProfitPeriod.Status.DRAFT,
        ).filter(
            Q(year__lt=period.year)
            | Q(year=period.year, month__lt=period.month)
        )
        if earlier_drafts.exists():
            raise ValidationError(
                "لا يمكن اعتماد شهر بينما توجد فترة أرباح سابقة ما زالت مسودة؛ "
                "اعتمد الفترات السابقة أولًا حتى لا تُحذف خسائرها من الرصيد المتراكم."
            )
        # Recalculate immediately before finalization so a stale draft cannot
        # persist an earlier result after expenses, shipments, or costs changed.
        period = calculate_monthly_profit(
            year=period.year, month=period.month, actor=actor
        )
        if not period.is_complete:
            raise ValidationError(
                "لا يمكن اعتماد نتيجة الشهر لوجود بيانات ناقصة. راجع ملاحظات "
                "البيانات الناقصة وسجلات المصاريف وتكاليف البضاعة المباعة والهالك "
                "غير المحوَّل. " + period.missing_data_notes
            )
        period.status = MonthlyProfitPeriod.Status.FINALIZED
        period.finalized_at = timezone.now()
        period.finalized_by = actor
        period._allow_finalize_save = True
        period.save(
            update_fields=["status", "finalized_at", "finalized_by", "updated_at"]
        )
        closing_loss = period.closing_accumulated_loss

    _refresh_draft_successors(
        year=period.year, month=period.month, actor=actor, closing_loss=closing_loss
    )
    return period


def _refresh_draft_successors(*, year, month, actor, closing_loss):
    """Keep later draft periods aligned with the newly finalized carry-forward."""
    later_drafts = MonthlyProfitPeriod.objects.filter(
        status=MonthlyProfitPeriod.Status.DRAFT,
    ).filter(Q(year__gt=year) | Q(year=year, month__gt=month)).order_by("year", "month")
    for draft in list(later_drafts):
        calculate_monthly_profit(year=draft.year, month=draft.month, actor=actor)


def reopen_monthly_profit(*, period, actor, reason):
    """Reopen a finalized month with a mandatory audit reason.

    Later finalized months must be reopened first so their loss balances are not
    silently rewritten. The record keeps its original finalization metadata and
    adds the reopen actor, time, and reason.
    """
    require_permission(actor, "costing.reopen_monthly_profit", "إعادة فتح نتيجة الشهر")
    _staff(actor)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(
            {"reason": "سبب إعادة فتح فترة الأرباح والخسائر إلزامي لتوثيق سجل التدقيق."}
        )

    with transaction.atomic():
        # Profit-month advisory lock before any row lock (locking protocol):
        # reopening is mutually exclusive with concurrent operational writes
        # and finalizations for the same month.
        lock_profit_month(period.year, period.month)
        period = MonthlyProfitPeriod.objects.select_for_update().get(pk=period.pk)
        if period.status != MonthlyProfitPeriod.Status.FINALIZED:
            raise ValidationError("يمكن إعادة فتح الفترات المعتمدة نهائيًا فقط.")
        if MonthlyProfitPeriod.objects.filter(
            status=MonthlyProfitPeriod.Status.FINALIZED
        ).filter(
            Q(year__gt=period.year)
            | Q(year=period.year, month__gt=period.month)
        ).exists():
            raise ValidationError(
                "أعد فتح الأشهر اللاحقة المعتمدة أولًا؛ لا يُعاد فتح شهر أقدم "
                "وتُركت أشهر لاحقة تعتمد على رصيده القديم."
            )
        period.status = MonthlyProfitPeriod.Status.DRAFT
        period.reopened_at = timezone.now()
        period.reopened_by = actor
        period.reopen_reason = reason
        period._allow_reopen_save = True
        period.save(
            update_fields=["status", "reopened_at", "reopened_by", "reopen_reason", "updated_at"]
        )
        closing_loss = period.closing_accumulated_loss

    # Later draft rows still carry the reopened month's old closing balance.
    # Recalculate them against the remaining finalized chain immediately; when
    # this month is finalized again its new balance will refresh them once more.
    _refresh_draft_successors(
        year=period.year, month=period.month, actor=actor, closing_loss=closing_loss
    )
    return period


def reverse_monthly_expense(*, expense, actor, reason):
    """Preserve an expense row and mark it reversed without deleting history."""
    require_permission(actor, "costing.manage_expenses", "عكس المصاريف")
    _staff(actor)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "سبب عكس المصروف مطلوب."})

    with transaction.atomic():
        # Locking protocol: the expense's costing month before its profit
        # month, before the expense row lock, so an expense reversal cannot
        # interleave with the finalization of either period.
        lock_costing_month(expense.year, expense.month)
        lock_profit_month(expense.year, expense.month)
        expense = MonthlyExpense.objects.select_for_update().get(pk=expense.pk)
        if expense.status == MonthlyExpense.Status.REVERSED:
            raise ValidationError("هذا المصروف معكوس بالفعل.")
        expense.status = MonthlyExpense.Status.REVERSED
        expense.reversed_at = timezone.now()
        expense.reversed_by = actor
        expense.reversal_reason = reason
        expense.updated_by = actor
        expense.save(
            update_fields=[
                "status",
                "reversed_at",
                "reversed_by",
                "reversal_reason",
                "updated_by",
                "updated_at",
            ]
        )
        return expense
