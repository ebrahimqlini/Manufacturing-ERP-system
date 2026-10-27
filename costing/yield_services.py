"""Yield vs. recorded-scrap reconciliation and valuation limitations.

Read-only services: nothing here creates or modifies costing, profit, or
inventory records. Material-waste event values come from immutable posting
snapshots; output rejection and yield shortfall never infer raw-material damage.

The distinctions enforced throughout (never blurred):

- **Yield shortfall** (expected cartons vs. good cartons) is a
  production-efficiency measure derived from the approved standard. It is
  NEVER valued as scrap — the material behind a shortfall is unaccounted, not
  measured as rejected.
- **Recorded rejected output** is order-level. It does not identify a damaged
  raw material; only explicit measured waste records establish that evidence.
- **Issued-material differences** (issued − expected-for-recorded-output) are
  UNEXPLAINED material. The current data model cannot tell usable roll remnants
  apart from material embedded in recorded damaged output, or other unexplained
  consumption, so no classification is invented: they are never labeled scrap,
  never valued, and never deducted — residual rolls are not tracked as
  inventory, and sale/recycling rules are not yet specified.
- **Threshold classification** (within/above the approved max scrap %) is an
  operational label. The financial treatment always follows the period's waste
  policy, never the operational label.
"""

import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q, Sum

from manufacturing.models import (
    MachineCounterReading,
    ProductionOrder,
    ProductionMaterialWaste,
)

from .models import (
    CostingPeriod,
    ProductCostingEntry,
    ProductionOrderCostingEntry,
)
from .services import (
    _select_yield_standard,
)

CENT = Decimal("0.01")
PERCENT = Decimal("0.01")
FOUR_DECIMALS = Decimal("0.0001")


class ScrapClass:
    """Operational classification of the recorded scrap percentage."""

    WITHIN = "WITHIN"
    ABOVE = "ABOVE"
    NO_THRESHOLD = "NO_THRESHOLD"
    NO_STANDARD = "NO_STANDARD"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


SCRAP_CLASS_LABELS = {
    ScrapClass.WITHIN: "ضمن الحد المعتمد (هالك طبيعي)",
    ScrapClass.ABOVE: "فوق الحد المعتمد (هالك زائد يستحق مراجعة)",
    ScrapClass.NO_THRESHOLD: "لا يوجد حد هالك معتمد لهذه المواصفة",
    ScrapClass.NO_STANDARD: "لا يوجد معيار إنتاجية نشط للمقارنة",
    ScrapClass.PENDING_APPROVAL: "بانتظار اكتمال/اعتماد الاحتساب قبل التصنيف",
    ScrapClass.INSUFFICIENT_DATA: "بيانات إنتاج غير كافية لحساب النسبة",
}


def classify_scrap_percent(
    scrap_percent,
    *,
    has_standard,
    max_scrap_percent,
    entry_complete=True,
):
    """Classify recorded scrap % against the approved threshold snapshot.

    Purely operational: it never changes any monetary treatment. The threshold
    passed in is the value snapshotted at calculation time (or the live
    standard in operational views) — a later spec revision never rewrites a
    historical classification.
    """
    if not entry_complete:
        return ScrapClass.PENDING_APPROVAL
    if scrap_percent is None:
        return ScrapClass.INSUFFICIENT_DATA
    if not has_standard:
        return ScrapClass.NO_STANDARD
    if max_scrap_percent is None:
        return ScrapClass.NO_THRESHOLD
    return ScrapClass.WITHIN if scrap_percent <= max_scrap_percent else ScrapClass.ABOVE


def _percent(numerator, denominator):
    """Percentage quantized to 0.01, or None when it cannot be computed."""
    if denominator is None or denominator <= 0:
        return None
    return (numerator * Decimal("100") / denominator).quantize(
        PERCENT, rounding=ROUND_HALF_UP
    )


def _q4(value):
    if value is None:
        return None
    return Decimal(value).quantize(FOUR_DECIMALS, rounding=ROUND_HALF_UP)


def _q2(value):
    if value is None:
        return None
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def order_yield_report(*, order):
    """Read-only per-order yield, scrap, counter, and reconciliation report.

    Counter readings are presented as a *measurement*, never as production:
    only APPROVED readings are counted as confirmed, everything else is
    provisional, and a line whose counter behavior is unconfirmed is labeled
    as such so its figures are never shown as verified good output.
    """
    notes = []
    good_base = order.actual_good_base
    scrap_base = order.actual_scrap_base
    total_output = good_base + scrap_base
    yield_percent = _percent(good_base, total_output)
    scrap_percent = _percent(scrap_base, total_output)

    standard = _select_yield_standard(order.product, order.production_line, notes)
    has_standard = standard is not None
    classification = classify_scrap_percent(
        scrap_percent,
        has_standard=has_standard,
        max_scrap_percent=standard.max_scrap_percent if standard else None,
        entry_complete=True,
    )

    # --- Counter comparison (measurement vs. recorded production) ---
    behavior = order.production_line.counter_behavior if order.production_line_id else "UNCONFIRMED"
    readings = MachineCounterReading.objects.filter(order=order)

    def _accepted(reading):
        if behavior == "COUNTS_GOOD_ONLY":
            return reading.counter_output_base or Decimal("0")
        # COUNTS_ALL_OUTPUT (and the unconfirmed default, shown as provisional)
        return reading.accepted_good_base or Decimal("0")

    approved = [r for r in readings if r.status == MachineCounterReading.Status.APPROVED and r.is_closed]
    unapproved = [
        r
        for r in readings
        if r.status in (MachineCounterReading.Status.COMPLETE, MachineCounterReading.Status.CORRECTED)
        and r.is_closed
    ]
    open_count = readings.filter(status=MachineCounterReading.Status.PROVISIONAL).count()
    confirmed_base = sum((_accepted(r) for r in approved), Decimal("0"))
    provisional_base = sum((_accepted(r) for r in unapproved), Decimal("0"))
    counter = {
        "line_behavior": behavior,
        "line_behavior_display": order.production_line.get_counter_behavior_display()
        if order.production_line_id
        else "غير محدد",
        # Unconfirmed behavior means the counter cannot be presented as
        # verified good output, only as a physical first measurement.
        "is_verified": behavior != "UNCONFIRMED",
        "confirmed_base": _q4(confirmed_base) if approved else None,
        "provisional_base": _q4(provisional_base) if unapproved else None,
        "open_intervals": open_count,
        "recorded_good_base": _q4(good_base),
        "variance_vs_recorded_good": _q4(confirmed_base - good_base) if approved else None,
        "approved_intervals": len(approved),
        "pending_intervals": len(unapproved) + open_count,
    }
    if behavior == "UNCONFIRMED" and (approved or unapproved or open_count):
        notes.append(
            "سلوك عدّاد الآلة غير مؤكد؛ أرقام العدّاد قياس فيزيائي أولي ولا تُعرض "
            "كإنتاج جيد موثّق حتى يؤكد المالك ما يعده العدّاد."
        )
    if behavior == "COUNTS_GOOD_ONLY" and scrap_base > 0:
        notes.append(
            "العدّاد يحتسب المنتوج الجيد فقط؛ الهالك المسجل يأتي من نتائج الإنتاج "
            "ولا يظهر في قراءات العدّاد."
        )

    # --- Material reconciliation: issued vs. expected-for-recorded-output ---
    material_rows = []
    unreconciled_total = Decimal("0")
    issues = order.material_issues.filter(movement__reversal__isnull=True)
    issued_by_material = dict(
        issues.values("requirement__material_id")
        .annotate(total=Sum("quantity_base"))
        .values_list("requirement__material_id", "total")
    )
    planned_total = order.planned_quantity_base
    for requirement in order.requirements.select_related("material"):
        issued = Decimal(issued_by_material.get(requirement.material_id, Decimal("0")) or Decimal("0"))
        if planned_total and planned_total > 0:
            ratio = requirement.planned_quantity_base / planned_total
            expected = ratio * good_base
        else:
            expected = Decimal("0")
        unreconciled = issued - expected
        unreconciled_total += max(unreconciled, Decimal("0"))
        material_rows.append(
            {
                "material_sku": requirement.material.sku,
                "material_name": requirement.material.name_ar,
                "issued_base": _q4(issued),
                "expected_for_recorded_output_base": _q4(expected),
                "unreconciled_base": _q4(unreconciled),
                # An unexplained difference, deliberately NOT classified: it may
                # mix usable roll remnants with material consumed by recorded
                # damaged units, and the data model cannot separate them.
                "label": "UNRECONCILED_MATERIAL" if unreconciled > 0 else "RECONCILED",
            }
        )
    result_events = [
        {
            "posted_at": result.posted_at,
            "scrap_quantity_base": _q4(result.scrap_quantity_base),
            "valuation_status": "UNAVAILABLE_MATERIAL_ATTRIBUTION"
            if result.scrap_quantity_base > 0
            else "NOT_APPLICABLE",
        }
        for result in order.results.filter(correction__isnull=True).order_by("posted_at", "pk")
    ]
    material_waste_events = [
        {
            "result_id": row.result_id,
            "material_sku": row.material.sku,
            "material_name": row.material.name_ar,
            "quantity_base": _q4(row.quantity_base),
            "posted_at": row.posted_at,
            "unit_cost_snapshot": row.unit_cost_snapshot,
            "total_cost_snapshot": row.total_cost_snapshot,
            "cost_source": row.cost_source,
            "is_reversal": bool(row.reversal_of_id),
            "correction_reason": row.correction_reason,
        }
        for row in order.material_waste.select_related("material").order_by("posted_at", "pk")
    ]
    if unreconciled_total > 0:
        notes.append(
            "الفرق بين المصروف والمتوقع للإنتاج المسجل مادة غير محوَّلة وغير مفسَّرة؛ "
            "قد يشمل رولات متبقية قابلة للاستخدام و/أو مادة استُهلكت في وحدات الهالك "
            "المسجلة، ولا يمكن تمييزهما ببيانات النظام الحالية. لا يُصنَّف هالكًا ولا "
            "يُقيَّم ولا يُخصم، ولم تُحدَّد قواعد بيعه أو إعادة تدويره بعد."
        )

    # --- Authoritative persisted scrap cost for this order ---
    scrap_cost_rows = []
    for entry in ProductionOrderCostingEntry.objects.filter(order=order).select_related("period"):
        scrap_cost_rows.append(
            {
                "year": entry.period.year,
                "month": entry.period.month,
                "waste_policy": entry.period.waste_policy,
                "waste_policy_display": entry.period.get_waste_policy_display(),
                "separated_scrap_cost": _q2(entry.waste_material_cost),
                "calculation_status": entry.calculation_status,
            }
        )

    if scrap_base > 0:
        notes.append(
            "الهالك المسجل على مستوى المنتج لا يثبت بذاته كمية مادة خام تالفة؛ راجع قياسات المواد المرتبطة "
            "وأدلة تكلفتها وقت الترحيل."
        )

    return {
        "order": order,
        "good_base": _q4(good_base),
        "scrap_base": _q4(scrap_base),
        "total_output_base": _q4(total_output),
        "yield_percent": yield_percent,
        "scrap_percent": scrap_percent,
        "classification": classification,
        "classification_label": SCRAP_CLASS_LABELS[classification],
        "standard": standard,
        "threshold_percent": standard.max_scrap_percent if standard else None,
        "target_yield_percent": standard.target_yield_percent if standard else None,
        "counter": counter,
        "materials": material_rows,
        "unreconciled_material_base": _q4(unreconciled_total),
        "scrap_cost_rows": scrap_cost_rows,
        "scrap_event_times": result_events,
        "material_waste_events": material_waste_events,
        "scrap_measurement_basis": "ORDER_LEVEL",
        "scrap_cost_valuation_status": "UNAVAILABLE_MATERIAL_ATTRIBUTION"
        if scrap_base > 0
        else "NOT_APPLICABLE",
        "notes": notes,
    }


def yield_report(*, year, month):
    """Period yield/scrap report over persisted costing entries.

    Classification uses the threshold SNAPSHOT stored on each entry, so a
    later revision of the standard never rewrites this month's classification.
    """
    period = CostingPeriod.objects.filter(year=year, month=month).first()
    rows = []
    if period is None:
        return {"period": None, "rows": [], "notes": ["لا توجد فترة تكاليف لهذه الشهر."]}

    entries = ProductCostingEntry.objects.filter(period=period).select_related(
        "product", "production_line", "yield_standard", "yield_standard__raw_material"
    )
    for entry in entries:
        good = entry.good_units_produced
        scrap = entry.scrap_units_produced
        total = good + scrap
        scrap_percent = _percent(scrap, total)
        classification = classify_scrap_percent(
            scrap_percent,
            has_standard=entry.yield_standard_id is not None,
            max_scrap_percent=entry.yield_scrap_threshold_percent,
            # Operational classification depends on recorded output quantities,
            # not on whether financial waste valuation is complete.
            entry_complete=total > 0,
        )
        price_info = None
        event_rows = entry.scrap_breakdown or []
        if event_rows:
            unvalued_event = any(row.get("total_cost_snapshot") is None for row in event_rows)
            first_event = event_rows[0]
            price_info = {
                "material_sku": first_event.get("material_sku"),
                "price": first_event.get("unit_cost_snapshot"),
                "basis": "لقطة تكلفة حدث الهالك المادي",
                "note": (
                    "سُجل قياس الهالك المادي لكن تعذر الحصول على تقييم موثوق وقت الترحيل."
                    if unvalued_event else
                    "تعرض تكلفة المادة كما حُفظت وقت ترحيل قياس الهالك."
                ),
            }
        elif entry.yield_standard_id and entry.yield_standard.raw_material_id:
            material = entry.yield_standard.raw_material
            price_info = {
                "material_sku": material.sku,
                "price": None,
                "basis": "لا يوجد قياس هالك مادة",
                "note": (
                    f"هالك المنتج لا يثبت تلف المادة الخام {material.sku}؛ "
                    "لا يُعرض تقييم للمادة دون قياس مادي مرتبط بها."
                ),
            }
        rows.append(
            {
                "entry": entry,
                "product": entry.product,
                "production_line": entry.production_line,
                "good_units": _q4(good),
                "scrap_units": _q4(scrap),
                "scrap_percent": scrap_percent,
                "classification": classification,
                "classification_label": SCRAP_CLASS_LABELS[classification],
                "threshold_percent": entry.yield_scrap_threshold_percent,
                "target_yield_percent": entry.yield_target_percent,
                "standard_revision": entry.yield_standard_revision,
                "standard_sku": entry.yield_standard.raw_material.sku if entry.yield_standard_id else None,
                "yield_shortfall_percent": entry.yield_shortfall_percent,
                # Measured raw material waste, valued at the event snapshot.
                "separated_scrap_cost": _q2(entry.waste_material_cost),
                "scrap_cost_valuation_status": (
                    "INCOMPLETE_EVIDENCE" if entry.calculation_status != "COMPLETE"
                    else "MEASURED_EVENT_SNAPSHOT" if entry.scrap_breakdown
                    else "REVIEWED_NO_MATERIAL_WASTE"
                ),
                "scrap_cost_basis": "EVENT_TIME_MEASURED_MATERIAL_WASTE",
                "waste_policy": period.waste_policy,
                "waste_policy_display": period.get_waste_policy_display(),
                "valuation": price_info,
            }
        )
    return {
        "period": period,
        "rows": rows,
        "notes": [],
    }


def separated_scrap_components(*, period):
    """Authoritative separated-scrap figures for one costing period.

    Returns ``(deductible, unreconciled, notes)``.

    - ``deductible``: separated waste material cost whose order is COMPLETED
      and whose order-level entry is COMPLETE, inside a FINALIZED costing
      period — the same authority gate COGS relies on — so it is safe to
      deduct exactly once as a loss line, because under ``SEPARATE_ABNORMAL``
      this material was excluded from ``material_cost_per_good_unit`` (and
      therefore from COGS).
    - ``unreconciled``: separated waste still inside draft, unfinished, or
      uncomputed orders. It is reported, never deducted, and never fabricated.
    """
    notes = []
    if period is None:
        return Decimal("0.00"), Decimal("0.00"), ["لا توجد فترة تكاليف مرتبطة."]
    if period.waste_policy != CostingPeriod.WastePolicy.SEPARATE_ABNORMAL:
        notes.append(
            "سياسة الفترة: تحميل الهالك على تكلفة الوحدات الجيدة؛ تكلفة الهالك ضمن "
            "تكلفة الوحدة ثم تكلفة البضاعة المباعة عند البيع، فلا تُخصم مرة أخرى."
        )
        return Decimal("0.00"), Decimal("0.00"), notes

    # The deduction gate mirrors _costing_is_authoritative: only a finalized,
    # complete costing period with complete entries yields an authoritative
    # figure. Draft-period waste stays visible as unreconciled until then.
    period_authoritative = (
        period.status == CostingPeriod.Status.FINALIZED and period.is_complete
    )
    deductible = Decimal("0.00")
    unreconciled = Decimal("0.00")
    for entry in ProductionOrderCostingEntry.objects.filter(period=period).select_related("order"):
        if entry.waste_material_cost <= 0:
            continue
        if not period_authoritative:
            unreconciled += entry.waste_material_cost
            notes.append(
                f"أمر {entry.order.order_number}: تكلفة هالك مفصلة داخل فترة تكاليف لم تُعتمد بعد؛ "
                "تُعرض ولا تُخصم حتى اعتماد الفترة."
            )
        elif entry.calculation_status != "COMPLETE":
            unreconciled += entry.waste_material_cost
            notes.append(
                f"أمر {entry.order.order_number}: تكلفة هالك مفصلة لكن احتساب الأمر غير مكتمل؛ "
                "تُعرض ولا تُخصم حتى اكتمال الاحتساب."
            )
        elif entry.order.status != ProductionOrder.Status.COMPLETED:
            unreconciled += entry.waste_material_cost
            notes.append(
                f"أمر {entry.order.order_number}: تكلفة هالك مفصلة على أمر ما زال "
                f"{entry.order.get_status_display()}؛ تُعرض ولا تُخصم قبل إكمال الأمر."
            )
        else:
            deductible += entry.waste_material_cost
    return _q2(deductible), _q2(unreconciled), notes


def absorbed_scrap_estimate(*, period):
    """Display measured, event-valued scrap retained in absorbed material cost."""
    if period is None or period.waste_policy != CostingPeriod.WastePolicy.ABSORBED_NORMAL:
        return Decimal("0.00")
    measured_total = Decimal("0.00")
    for entry in ProductCostingEntry.objects.filter(period=period):
        measured_total += entry.waste_material_cost
    return _q2(measured_total)


def monthly_scrap_summary(*, year, month):
    """Aggregate scrap view for one month (measured vs. valued)."""
    period = CostingPeriod.objects.filter(year=year, month=month).first()
    if period is None:
        return {
            "period": None,
            "good_units": None,
            "scrap_units": None,
            "scrap_percent": None,
            "authoritative_scrap_cost": Decimal("0.00"),
            "rows": [],
            "notes": ["لا توجد فترة تكاليف لهذه الشهر؛ لم تُحتسب أي تكلفة هالك."],
        }
    entries = list(
        ProductionOrderCostingEntry.objects.filter(period=period).select_related("product")
    )
    good_total = sum((e.good_units_produced for e in entries), Decimal("0"))
    scrap_total = sum((e.scrap_units_produced for e in entries), Decimal("0"))
    measured_cost = sum((e.waste_material_cost for e in entries), Decimal("0.00"))
    deductible, unreconciled, notes = separated_scrap_components(period=period)
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    waste_events = [
        {
            "order_number": row.order.order_number,
            "result_id": row.result_id,
            "material_sku": row.material.sku,
            "material_name": row.material.name_ar,
            "quantity_base": _q4((-1 if row.reversal_of_id else 1) * row.quantity_base),
            "unit_code": row.material.base_uom.code,
            "unit_cost_snapshot": row.unit_cost_snapshot,
            "total_cost_snapshot": (-1 if row.reversal_of_id else 1) * row.total_cost_snapshot if row.total_cost_snapshot is not None else None,
            "cost_source": row.cost_source,
            "posted_at": row.posted_at,
            "roll_reference": row.roll_reference,
            "is_reversal": bool(row.reversal_of_id),
            "correction_reason": row.correction_reason,
        }
        for row in ProductionMaterialWaste.objects.filter(
            Q(posted_at__date__gte=start, posted_at__date__lte=end)
            | Q(reversal_of__posted_at__date__gte=start, reversal_of__posted_at__date__lte=end)
        ).select_related("order", "result", "material", "material__base_uom").order_by("posted_at", "pk")
    ]
    if scrap_total > 0 and not period.is_complete:
        notes.append(
            "فترة التكاليف غير مكتملة؛ راجع تفاصيل الأدلة والقياس المادي قبل اعتماد أي تكلفة هالك."
        )
    return {
        "period": period,
        "good_units": _q4(good_total),
        "scrap_units": _q4(scrap_total),
        "scrap_percent": _percent(scrap_total, good_total + scrap_total),
        # Compatibility key now contains measured event-time waste cost.
        "authoritative_scrap_cost": _q2(measured_cost),
        "allocated_scrap_cost_estimate": _q2(measured_cost),
        "scrap_cost_basis": "EVENT_TIME_MEASURED_MATERIAL_WASTE",
        "waste_events": waste_events,
        "separated_scrap_cost": deductible,
        "unreconciled_scrap_cost": unreconciled,
        "waste_policy_display": period.get_waste_policy_display(),
        "notes": notes,
    }
