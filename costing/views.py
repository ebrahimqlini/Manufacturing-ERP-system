from datetime import date
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import (
    CostingPeriodPolicyForm,
    MonthlyExpenseForm,
    ProductSellingPriceForm,
    RawMaterialPriceForm,
    ReopenPeriodForm,
    RollYieldStandardForm,
)
from .models import (
    CostingPeriod,
    MonthlyExpense,
    ProductCostingEntry,
    ProductSellingPrice,
    ProductionOrderCostingEntry,
    RawMaterialPrice,
    RollYieldStandard,
)
from .services import (
    calculate_monthly_costing,
    finalize_costing_period,
    reopen_costing_period,
)


def staff_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            return HttpResponseForbidden("هذه الصفحة متاحة لمستخدمي النظام الداخليين فقط.")
        return view(request, *args, **kwargs)
    return wrapped


# Non-staff "finance" users can be granted this permission explicitly (for
# example through a group). Nothing in the application assigns it automatically.
FINANCIAL_VIEW_PERMISSION = "costing.view_costing_financials"


def costing_financials_required(view):
    """Restrict a costing page that exposes financial data.

    Costing output is financial data, so it is not exposed to every authenticated
    user. Access is limited to staff users and to users explicitly granted the
    ``costing.view_costing_financials`` permission. The project has no pre-existing
    role/group convention, so this standard Django permission is the only
    additional grant path; it is never assigned automatically.
    """

    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = request.user
        if user.is_staff or user.has_perm(FINANCIAL_VIEW_PERMISSION):
            return view(request, *args, **kwargs)
        return HttpResponseForbidden(
            "بيانات التكاليف المالية متاحة لمستخدمي النظام أو للمستخدمين المصرح لهم ماليًا فقط."
        )

    return wrapped


# --- Monthly Costing Periods & Reports ---

@costing_financials_required
def period_list(request):
    periods = list(CostingPeriod.objects.all().order_by("-year", "-month"))
    # Flag months whose required electricity/water bill has not been recorded at
    # all, so a missing utility is visible before opening each period report.
    for period in periods:
        recorded_types = set(
            MonthlyExpense.objects.filter(year=period.year, month=period.month).values_list(
                "expense_type", flat=True
            )
        )
        period.missing_utility_labels = [
            label
            for code, label in (
                (MonthlyExpense.ExpenseType.ELECTRICITY, "كهرباء"),
                (MonthlyExpense.ExpenseType.WATER, "مياه"),
            )
            if code not in recorded_types
        ]
    current_year = date.today().year
    current_month = date.today().month
    return render(
        request,
        "costing/period_list.html",
        {
            "periods": periods,
            "current_year": current_year,
            "current_month": current_month,
        },
    )


@staff_required
def period_calculate(request):
    if request.method == "POST":
        try:
            year = int(request.POST.get("year", date.today().year))
            month = int(request.POST.get("month", date.today().month))
            if not 1 <= month <= 12:
                raise ValueError("الشهر غير صالح.")
        except ValueError:
            messages.error(request, "يرجى إدخال سنة وشهر صالحين.")
            return redirect("costing:period_list")

        try:
            period = calculate_monthly_costing(year=year, month=month, actor=request.user)
            messages.success(request, f"تم احتساب تكاليف شهر {year}/{month:02d} بنجاح.")
            return redirect("costing:period_detail", pk=period.pk)
        except ValidationError as exc:
            messages.error(request, " — ".join(exc.messages))
            return redirect("costing:period_list")

    return redirect("costing:period_list")


def _allocation_reconciliation(period):
    """Compare each expense category total with the amount allocated to products.

    The owner must be able to verify that no expense disappeared or was counted
    twice, so the difference is reported explicitly instead of being assumed.
    """
    allocated = period.product_entries.aggregate(
        labor=Sum("allocated_labor_cost"),
        electricity=Sum("allocated_electricity_cost"),
        water=Sum("allocated_water_cost"),
        other=Sum("allocated_other_cost"),
        material=Sum("actual_material_cost"),
    )
    rows = [
        ("مصاريف العمالة", period.labor_total, allocated["labor"]),
        ("مصاريف الكهرباء", period.electricity_total, allocated["electricity"]),
        ("مصاريف المياه", period.water_total, allocated["water"]),
        ("مصاريف صناعية أخرى", period.other_industrial_total, allocated["other"]),
        ("تكلفة المواد الفعلية", period.materials_total, allocated["material"]),
    ]
    return [
        {
            "label": label,
            "total": total or Decimal("0.00"),
            "allocated": value or Decimal("0.00"),
            "difference": (total or Decimal("0.00")) - (value or Decimal("0.00")),
        }
        for label, total, value in rows
    ]


@costing_financials_required
def period_detail(request, pk):
    period = get_object_or_404(CostingPeriod, pk=pk)
    entries = period.product_entries.select_related("product", "product__base_uom", "production_line").order_by("product__sku")
    missing_notes = [n for n in period.missing_data_notes.split("\n") if n.strip()] if period.missing_data_notes else []
    waste_total = sum(entry.waste_material_cost for entry in entries) or Decimal("0.00")

    return render(
        request,
        "costing/period_detail.html",
        {
            "period": period,
            "entries": entries,
            "missing_notes": missing_notes,
            "reconciliation": _allocation_reconciliation(period),
            "waste_total": waste_total,
            "order_entry_count": period.order_entries.count(),
        },
    )


@costing_financials_required
def order_breakdown(request, pk):
    """Read-only production-order cost breakdown for one monthly period."""
    period = get_object_or_404(CostingPeriod, pk=pk)
    order_entries = (
        ProductionOrderCostingEntry.objects.filter(period=period)
        .select_related("order", "product", "product__base_uom", "production_line")
        .order_by("product__sku", "order__order_number")
    )
    grouped = {}
    for entry in order_entries:
        grouped.setdefault((entry.product_id, entry.product, entry.production_line), []).append(entry)

    return render(
        request,
        "costing/order_breakdown.html",
        {
            "period": period,
            "groups": [
                {
                    "product": product,
                    "production_line": line,
                    "entries": rows,
                    "totals": {
                        "good": sum(r.good_units_produced for r in rows),
                        "scrap": sum(r.scrap_units_produced for r in rows),
                        "material": sum(r.actual_material_cost for r in rows),
                        "assigned": sum(r.material_cost_assigned_to_good for r in rows),
                        "waste": sum(r.waste_material_cost for r in rows),
                        "labor": sum(r.allocated_labor_cost for r in rows),
                        "electricity": sum(r.allocated_electricity_cost for r in rows),
                        "water": sum(r.allocated_water_cost for r in rows),
                        "other": sum(r.allocated_other_cost for r in rows),
                        "total": sum(r.total_production_cost for r in rows),
                    },
                }
                for (_, product, line), rows in sorted(grouped.items(), key=lambda item: item[0][1].sku)
            ],
        },
    )


@costing_financials_required
def period_compare(request):
    """Read-only comparison of the same product's cost across months.

    Values come from stored calculated entries only; the screen never re-derives
    or invents figures. Cost movements are shown next to production and waste
    quantities so an increase in cost is not mistaken for an increase in
    consumption.
    """
    periods = list(CostingPeriod.objects.all().order_by("year", "month"))
    monthly_rows = [
        {
            "period": period,
            "labor": period.labor_total,
            "electricity": period.electricity_total,
            "water": period.water_total,
            "other": period.other_industrial_total,
            "materials": period.materials_total,
            "total": period.total_production_cost,
            "is_complete": period.is_complete,
        }
        for period in periods
    ]

    entries = list(
        ProductCostingEntry.objects.select_related("product", "product__base_uom", "production_line").order_by(
            "period__year", "period__month"
        )
    )
    collected = {}
    products = {}
    for entry in entries:
        collected[(entry.product_id, entry.period_id)] = entry
        products.setdefault(entry.product_id, entry.product)

    ordered_products = sorted(products.values(), key=lambda product: product.sku)
    selected_product = None
    requested = request.GET.get("product", "")
    if requested.isdigit():
        selected_product = products.get(int(requested))
    if selected_product is None and ordered_products:
        selected_product = ordered_products[0]

    detail_rows = []
    previous = None
    if selected_product is not None:
        for period in periods:
            entry = collected.get((selected_product.pk, period.pk))
            if entry is None:
                continue
            row = {
                "period": period,
                "entry": entry,
                "previous_period": previous["period"] if previous else None,
                "delta_electricity": None,
                "delta_water": None,
                "delta_materials": None,
                "delta_labor": None,
                "delta_other": None,
                "delta_total": None,
                "delta_cost_per_unit": None,
                "delta_good_units": None,
                "delta_scrap_units": None,
            }
            if previous is not None:
                prev_entry = previous["entry"]
                row["delta_electricity"] = entry.allocated_electricity_cost - prev_entry.allocated_electricity_cost
                row["delta_water"] = entry.allocated_water_cost - prev_entry.allocated_water_cost
                row["delta_materials"] = (
                    entry.material_cost_assigned_to_good - prev_entry.material_cost_assigned_to_good
                )
                row["delta_labor"] = entry.allocated_labor_cost - prev_entry.allocated_labor_cost
                row["delta_other"] = entry.allocated_other_cost - prev_entry.allocated_other_cost
                row["delta_total"] = entry.total_production_cost - prev_entry.total_production_cost
                row["delta_good_units"] = entry.good_units_produced - prev_entry.good_units_produced
                row["delta_scrap_units"] = entry.scrap_units_produced - prev_entry.scrap_units_produced
                if entry.cost_per_good_unit is not None and prev_entry.cost_per_good_unit is not None:
                    row["delta_cost_per_unit"] = entry.cost_per_good_unit - prev_entry.cost_per_good_unit
            detail_rows.append(row)
            previous = row

    product_matrix = [
        {"product": product, "cells": [collected.get((product.pk, period.pk)) for period in periods]}
        for product in ordered_products
    ]

    return render(
        request,
        "costing/period_compare.html",
        {
            "periods": periods,
            "monthly_rows": monthly_rows,
            "products": ordered_products,
            "selected_product": selected_product,
            "detail_rows": detail_rows,
            "product_matrix": product_matrix,
        },
    )


@staff_required
def period_policy_edit(request, pk):
    period = get_object_or_404(CostingPeriod, pk=pk)
    if period.status == CostingPeriod.Status.FINALIZED:
        messages.error(request, "لا يمكن تعديل سياسات فترة معتمدة نهائيًا؛ أعد فتح الفترة أولاً.")
        return redirect("costing:period_detail", pk=period.pk)

    form = CostingPeriodPolicyForm(request.POST or None, instance=period)
    if request.method == "POST" and form.is_valid():
        form.save()
        try:
            calculate_monthly_costing(
                year=period.year,
                month=period.month,
                allocation_policy=period.allocation_policy,
                waste_policy=period.waste_policy,
                actor=request.user,
            )
            messages.success(request, "تم حفظ السياسات وإعادة احتساب الفترة وفقًا للخيارات المعتمدة.")
        except ValidationError as exc:
            messages.error(request, " — ".join(exc.messages))
        return redirect("costing:period_detail", pk=period.pk)

    return render(
        request,
        "costing/policy_form.html",
        {
            "form": form,
            "period": period,
            "title": f"ضبط سياسات التكاليف — {period.year}/{period.month:02d}",
        },
    )


@require_POST
@staff_required
def period_finalize(request, pk):
    period = get_object_or_404(CostingPeriod, pk=pk)
    try:
        finalize_costing_period(period=period, actor=request.user)
        messages.success(request, f"تم اعتماد تقرير تكاليف شهر {period.year}/{period.month:02d} نهائيًا.")
    except ValidationError as exc:
        messages.error(request, " — ".join(exc.messages))
    return redirect("costing:period_detail", pk=period.pk)


@staff_required
def period_reopen(request, pk):
    period = get_object_or_404(CostingPeriod, pk=pk)
    if period.status != CostingPeriod.Status.FINALIZED:
        messages.info(request, "هذه الفترة ليست معتمدة نهائيًا.")
        return redirect("costing:period_detail", pk=period.pk)

    form = ReopenPeriodForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            reopen_costing_period(
                period=period,
                actor=request.user,
                reason=form.cleaned_data["reason"],
            )
            messages.success(request, f"تمت إعادة فتح فترة {period.year}/{period.month:02d} بنجاح مع توثيق سبب التعديل.")
            return redirect("costing:period_detail", pk=period.pk)
        except ValidationError as exc:
            messages.error(request, " — ".join(exc.messages))

    return render(
        request,
        "costing/period_reopen.html",
        {
            "form": form,
            "period": period,
            "title": f"إعادة فتح فترة التكاليف — {period.year}/{period.month:02d}",
        },
    )


@costing_financials_required
def product_breakdown(request, pk):
    entry = get_object_or_404(
        ProductCostingEntry.objects.select_related("period", "product", "product__base_uom", "production_line"),
        pk=pk,
    )
    return render(
        request,
        "costing/product_breakdown.html",
        {
            "entry": entry,
            "period": entry.period,
            "materials": entry.material_breakdown,
        },
    )


# --- Monthly Expenses Management ---

@costing_financials_required
def expense_list(request):
    expenses = MonthlyExpense.objects.select_related("production_line", "product").order_by("-year", "-month", "expense_type")
    year_filter = request.GET.get("year", "")
    month_filter = request.GET.get("month", "")
    type_filter = request.GET.get("type", "")

    if year_filter.isdigit():
        expenses = expenses.filter(year=int(year_filter))
    if month_filter.isdigit():
        expenses = expenses.filter(month=int(month_filter))
    if type_filter in MonthlyExpense.ExpenseType.values:
        expenses = expenses.filter(expense_type=type_filter)

    totals = {
        "labor": expenses.filter(expense_type=MonthlyExpense.ExpenseType.LABOR).aggregate(s=Sum("amount"))["s"] or 0,
        "electricity": expenses.filter(expense_type=MonthlyExpense.ExpenseType.ELECTRICITY).aggregate(s=Sum("amount"))["s"] or 0,
        "water": expenses.filter(expense_type=MonthlyExpense.ExpenseType.WATER).aggregate(s=Sum("amount"))["s"] or 0,
        "other": expenses.filter(expense_type=MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL).aggregate(s=Sum("amount"))["s"] or 0,
    }
    totals["grand"] = totals["labor"] + totals["electricity"] + totals["water"] + totals["other"]

    # Warn about periods whose required electricity/water bill is not recorded yet.
    utility_gaps = []
    for period in CostingPeriod.objects.all().order_by("-year", "-month"):
        recorded = set(
            MonthlyExpense.objects.filter(year=period.year, month=period.month).values_list(
                "expense_type", flat=True
            )
        )
        missing = [
            label
            for code, label in (
                (MonthlyExpense.ExpenseType.ELECTRICITY, "كهرباء"),
                (MonthlyExpense.ExpenseType.WATER, "مياه"),
            )
            if code not in recorded
        ]
        if missing:
            utility_gaps.append({"period": period, "missing": missing})

    return render(
        request,
        "costing/expense_list.html",
        {
            "expenses": expenses[:150],
            "totals": totals,
            "types": MonthlyExpense.ExpenseType.choices,
            "selected_year": year_filter,
            "selected_month": month_filter,
            "selected_type": type_filter,
            "utility_gaps": utility_gaps,
        },
    )


@staff_required
def expense_create(request):
    form = MonthlyExpenseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        expense = form.save(commit=False)
        expense.created_by = request.user
        try:
            expense.save()
            messages.success(request, "تم تسجيل المصروف الشهري بنجاح.")
            return redirect("costing:expense_list")
        except ValidationError as exc:
            for msg in exc.messages:
                form.add_error(None, msg)

    return render(
        request,
        "costing/expense_form.html",
        {"form": form, "title": "إضافة مصروف شهري"},
    )


@staff_required
def expense_edit(request, pk):
    expense = get_object_or_404(MonthlyExpense, pk=pk)
    period = CostingPeriod.objects.filter(year=expense.year, month=expense.month, status=CostingPeriod.Status.FINALIZED).first()
    if period:
        messages.error(request, "لا يمكن تعديل مصروف لفترة معتمدة نهائيًا؛ أعد فتح الفترة أولاً.")
        return redirect("costing:expense_list")

    form = MonthlyExpenseForm(request.POST or None, instance=expense)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
            messages.success(request, "تم تحديث المصروف.")
            return redirect("costing:expense_list")
        except ValidationError as exc:
            for msg in exc.messages:
                form.add_error(None, msg)

    return render(
        request,
        "costing/expense_form.html",
        {"form": form, "title": f"تعديل مصروف — {expense.description}"},
    )


# --- Raw Material Pricing Management ---

@costing_financials_required
def price_list(request):
    prices = RawMaterialPrice.objects.select_related("material", "unit", "created_by").order_by("-effective_date", "material__sku")
    return render(request, "costing/price_list.html", {"prices": prices})


@staff_required
def price_create(request):
    form = RawMaterialPriceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        price = form.save(commit=False)
        price.created_by = request.user
        try:
            price.save()
            messages.success(request, "تم حفظ سعر المادة الخام.")
            return redirect("costing:price_list")
        except ValidationError as exc:
            for msg in exc.messages:
                form.add_error(None, msg)

    return render(
        request,
        "costing/price_form.html",
        {"form": form, "title": "إضافة سعر مادة خام"},
    )


@staff_required
def price_edit(request, pk):
    price = get_object_or_404(RawMaterialPrice, pk=pk)
    form = RawMaterialPriceForm(request.POST or None, instance=price)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
            messages.success(request, "تم تحديث سعر المادة الخام.")
            return redirect("costing:price_list")
        except ValidationError as exc:
            for msg in exc.messages:
                form.add_error(None, msg)

    return render(
        request,
        "costing/price_form.html",
        {"form": form, "title": f"تعديل سعر {price.material.sku}"},
    )


# --- Roll Yield Standards Management ---

@costing_financials_required
def yield_standard_list(request):
    standards = RollYieldStandard.objects.select_related(
        "raw_material", "product", "production_line", "roll_unit", "carton_unit"
    ).order_by("product__sku")
    return render(request, "costing/yield_standard_list.html", {"standards": standards})


@staff_required
def yield_standard_create(request):
    form = RollYieldStandardForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        standard = form.save(commit=False)
        standard.created_by = request.user
        try:
            standard.save()
            messages.success(request, "تم حفظ معيار إنتاجية الرول.")
            return redirect("costing:yield_standard_list")
        except ValidationError as exc:
            for msg in exc.messages:
                form.add_error(None, msg)

    return render(
        request,
        "costing/yield_standard_form.html",
        {"form": form, "title": "إضافة معيار إنتاجية رول"},
    )


@staff_required
def yield_standard_edit(request, pk):
    standard = get_object_or_404(RollYieldStandard, pk=pk)
    form = RollYieldStandardForm(request.POST or None, instance=standard)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
            messages.success(request, "تم تحديث معيار إنتاجية الرول.")
            return redirect("costing:yield_standard_list")
        except ValidationError as exc:
            for msg in exc.messages:
                form.add_error(None, msg)

    return render(
        request,
        "costing/yield_standard_form.html",
        {"form": form, "title": f"تعديل معيار إنتاجية {standard.product.sku}"},
    )


# --- Product Selling Price Reference (Display-Only) ---

@costing_financials_required
def selling_price_list(request):
    prices = ProductSellingPrice.objects.select_related("product", "unit", "updated_by").order_by("product__sku")
    return render(request, "costing/selling_price_list.html", {"prices": prices})


@staff_required
def selling_price_form(request, pk=None):
    instance = get_object_or_404(ProductSellingPrice, pk=pk) if pk else None
    form = ProductSellingPriceForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        selling = form.save(commit=False)
        selling.updated_by = request.user
        selling.save()
        messages.success(request, "تم حفظ سعر البيع المرجعي للمنتج.")
        return redirect("costing:selling_price_list")

    return render(
        request,
        "costing/price_form.html",
        {"form": form, "title": "تسجيل سعر بيع مرجعي"},
    )
