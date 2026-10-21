from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date

from manufacturing.models import ProductionLine

from .services import (
    costing_report,
    current_stock_rows,
    data_quality_warnings,
    granularity_for,
    inventory_report,
    production_report,
    profit_report,
    purchases_report,
    resolve_period,
    sales_report,
)


def staff_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            return HttpResponseForbidden("هذه الصفحة متاحة لمستخدمي النظام الداخليين فقط.")
        return view(request, *args, **kwargs)

    return wrapped


def _period_from_request(request):
    today = timezone.localdate()
    start_raw = request.GET.get("start_date")
    end_raw = request.GET.get("end_date")
    start = end = None
    if start_raw or end_raw:
        start = parse_date(start_raw or "")
        end = parse_date(end_raw or "")
        if not start or not end:
            messages.error(request, "أدخل تاريخ بداية ونهاية صحيحين بالصيغة YYYY-MM-DD.")
            start = end = None
    if start is None or end is None:
        end = today
        start = end - timedelta(days=29)
    try:
        start, end = resolve_period(start, end)
    except ValidationError:
        messages.error(request, "الفترة المطلوبة غير صالحة؛ استُخدمت آخر 30 يومًا.")
        end = today
        start = end - timedelta(days=29)
    granularity = granularity_for(
        start, end, request.GET.get("granularity") or None
    )
    return start, end, granularity


def _build_reports(start, end, granularity, production_line=None):
    dimension_limit = 25
    sales = sales_report(start, end, granularity, 5, dimension_limit)
    purchases = purchases_report(start, end, granularity, 5, dimension_limit)
    inventory = inventory_report(start, end, granularity, 8, dimension_limit)
    production = production_report(
        start, end, granularity, 5, dimension_limit, production_line
    )
    costing = costing_report(start, end, 5, dimension_limit)
    profit = profit_report(start, end, dimension_limit)
    warnings = data_quality_warnings(
        sales, purchases, inventory, production, costing, profit
    )
    return {
        "sales": sales,
        "purchases": purchases,
        "inventory": inventory,
        "production": production,
        "costing": costing,
        "profit": profit,
        "data_quality": warnings,
    }


def _context(request, production_line=None):
    start, end, granularity = _period_from_request(request)
    reports = _build_reports(start, end, granularity, production_line)
    return {
        "start_date": start,
        "end_date": end,
        "granularity": granularity,
        "period_days": (end - start).days + 1,
        **reports,
    }


@staff_required
def overview(request):
    context = _context(request)
    context["active_report"] = "overview"
    return render(request, "reports/overview.html", context)


@staff_required
def sales(request):
    context = _context(request)
    context["active_report"] = "sales"
    return render(request, "reports/sales.html", context)


@staff_required
def purchases(request):
    context = _context(request)
    context["active_report"] = "purchases"
    return render(request, "reports/purchases.html", context)


@staff_required
def inventory(request):
    context = _context(request)
    context["active_report"] = "inventory"
    rows = current_stock_rows()
    query = (request.GET.get("q") or "").strip()
    if query:
        lowered = query.lower()
        rows = [
            row
            for row in rows
            if lowered in row["sku"].lower()
            or lowered in row["name_ar"].lower()
            or lowered in row["category_name"].lower()
        ]
    context["stock_query"] = query
    context["stock_count"] = len(rows)
    page_obj = Paginator(rows, 25).get_page(request.GET.get("page"))
    context["page_obj"] = page_obj
    context["stock_rows"] = page_obj.object_list
    context["page_query"] = (
        f"start_date={context['start_date']}&end_date={context['end_date']}"
        f"&granularity={context['granularity']}&q={query}"
    )
    return render(request, "reports/inventory.html", context)


@staff_required
def production(request):
    line_code = (request.GET.get("line") or "").strip()
    if line_code and not ProductionLine.objects.filter(code=line_code).exists():
        messages.error(request, "خط الإنتاج المحدد غير موجود؛ عُرضت كل الخطوط.")
        line_code = ""
    context = _context(request, line_code or None)
    context["active_report"] = "production"
    context["production_lines"] = ProductionLine.objects.order_by("code")
    context["selected_line"] = line_code
    return render(request, "reports/production.html", context)


@staff_required
def costing(request):
    context = _context(request)
    context["active_report"] = "costing"
    return render(request, "reports/costing.html", context)


@staff_required
def profit(request):
    context = _context(request)
    context["active_report"] = "profit"
    return render(request, "reports/profit.html", context)
