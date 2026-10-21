from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Exists, OuterRef, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone

from catalog.models import Product, ProductCategory
from costing.models import CostingPeriod, ProductCostingEntry
from inventory.services import ledger_balances
from manufacturing.models import ProductionLine, ProductionOrder, ProductionResult
from purchasing.models import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseReceipt,
    PurchaseReceiptLine,
    Supplier,
)
from sales.models import Customer, SalesOrder, SalesOrderLine, Shipment, ShipmentLine

from .serializers import (
    OwnerRecentPurchaseOrderSerializer,
    OwnerRecentSalesOrderSerializer,
    OwnerRecentShipmentSerializer,
    format_decimal,
)


CATEGORY_CODES = (
    "raw-material",
    "work-in-progress",
    "finished-goods",
    "packaging-material",
    "scrap-waste",
    "consumables",
)


def _money(value):
    return format_decimal(value or Decimal("0"), 2)


def _quantity(value):
    return format_decimal(value or Decimal("0"))


def _sum(queryset, field):
    return queryset.aggregate(total=Sum(field))["total"] or Decimal("0")


def _period(validated):
    end = validated.get("end_date") or timezone.localdate()
    start = validated.get("start_date") or (end - timedelta(days=29))
    granularity = validated.get("granularity") or ("month" if (end - start).days > 90 else "day")
    return start, end, granularity


def _bucket(value, granularity):
    if granularity == "month":
        return date(value.year, value.month, 1)
    if hasattr(value, "date"):
        return value.date()
    return value


def _bucket_dates(start, end, granularity):
    buckets = []
    if granularity == "month":
        current = date(start.year, start.month, 1)
        final = date(end.year, end.month, 1)
        while current <= final:
            buckets.append(current)
            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)
    else:
        current = start
        while current <= end:
            buckets.append(current)
            current += timedelta(days=1)
    return buckets


def _trend_rows(queryset, date_field, value_field, granularity, count_field):
    trunc = TruncMonth if granularity == "month" else TruncDate
    rows = (
        queryset.annotate(bucket=trunc(date_field))
        .values("bucket")
        .annotate(value=Sum(value_field), count=Count(count_field, distinct=True))
        .order_by("bucket")
    )
    values = {}
    for row in rows:
        key = _bucket(row["bucket"], granularity)
        values[key] = {
            "value": row["value"] or Decimal("0"),
            "count": row["count"] or 0,
        }
    return values


def _trend_series(values, buckets):
    return [
        {
            "date": bucket.isoformat(),
            "count": values.get(bucket, {}).get("count", 0),
            "value": _money(values.get(bucket, {}).get("value", Decimal("0"))),
        }
        for bucket in buckets
    ]


def _document_counts(queryset, line_queryset, relation_field):
    """Return document total, active, and fully reversed counts.

    A document is active when it has at least one non-reversed line. A document
    is fully reversed when it has at least one reversed line and no non-reversed
    line. The total deliberately includes cancelled/reversed documents so the
    audit population is not hidden.
    """
    active_lines = line_queryset.filter(
        **{relation_field: OuterRef("pk")}, movement__reversal__isnull=True
    )
    reversed_lines = line_queryset.filter(
        **{relation_field: OuterRef("pk")}, movement__reversal__isnull=False
    )
    annotated = queryset.annotate(
        _has_active_line=Exists(active_lines),
        _has_reversed_line=Exists(reversed_lines),
    )
    return {
        "total": queryset.count(),
        "active": annotated.filter(_has_active_line=True).count(),
        "fully_reversed": annotated.filter(
            _has_active_line=False, _has_reversed_line=True
        ).count(),
    }


def _sales_section(start, end, granularity, recent_limit):
    orders = SalesOrder.objects.filter(order_date__range=(start, end))
    status_counts = {
        row["status"]: row["count"]
        for row in orders.values("status").annotate(count=Count("id"))
    }
    commercial_value = _sum(
        SalesOrderLine.objects.filter(
            order__order_date__range=(start, end),
        ).exclude(order__status=SalesOrder.Status.CANCELLED),
        "line_total",
    )
    shipment_lines = ShipmentLine.objects.filter(
        shipment__shipment_date__range=(start, end),
        movement__reversal__isnull=True,
    )
    shipment_counts = _document_counts(
        Shipment.objects.filter(shipment_date__range=(start, end)),
        ShipmentLine.objects.all(),
        "shipment",
    )
    buckets = _bucket_dates(start, end, granularity)
    order_trend = _trend_rows(
        SalesOrderLine.objects.filter(order__order_date__range=(start, end)).exclude(
            order__status=SalesOrder.Status.CANCELLED
        ),
        "order__order_date",
        "line_total",
        granularity,
        "order_id",
    )
    shipment_trend = _trend_rows(
        shipment_lines,
        "shipment__shipment_date",
        "line_total",
        granularity,
        "shipment_id",
    )
    recent = SalesOrder.objects.filter(order_date__range=(start, end)).select_related(
        "customer"
    ).order_by("-order_date", "-created_at")[:recent_limit]
    return {
        "orders_total": orders.count(),
        "non_cancelled_orders_count": orders.exclude(
            status=SalesOrder.Status.CANCELLED
        ).count(),
        "cancelled_orders_count": orders.filter(
            status=SalesOrder.Status.CANCELLED
        ).count(),
        "orders_by_status": status_counts,
        "commercial_order_value": _money(commercial_value),
        "shipments_count": shipment_counts["total"],
        "active_shipments_count": shipment_counts["active"],
        "fully_reversed_shipments_count": shipment_counts["fully_reversed"],
        "net_shipped_value": _money(_sum(shipment_lines, "line_total")),
        "value_basis": "Commercial sales-document snapshots; not accounting revenue.",
        "count_basis": {
            "orders_total": "All sales order documents in the period, including cancelled orders.",
            "non_cancelled_orders_count": "Sales orders in the period excluding CANCELLED.",
            "cancelled_orders_count": "Sales orders in the period with CANCELLED status.",
            "shipments_count": "All shipment documents in the period, including fully reversed shipments.",
            "active_shipments_count": "Shipments with at least one non-reversed line.",
            "fully_reversed_shipments_count": "Shipments with at least one reversed line and no non-reversed line.",
        },
        "trend": {
            "granularity": granularity,
            "orders": _trend_series(order_trend, buckets),
            "shipments": _trend_series(shipment_trend, buckets),
            "count_basis": {
                "orders": "Distinct non-cancelled sales orders in each bucket.",
                "shipments": "Distinct shipments with at least one non-reversed line in each bucket.",
            },
        },
        "recent_orders": OwnerRecentSalesOrderSerializer(recent, many=True).data,
    }


def _purchases_section(start, end, granularity, recent_limit):
    orders = PurchaseOrder.objects.filter(order_date__range=(start, end))
    status_counts = {
        row["status"]: row["count"]
        for row in orders.values("status").annotate(count=Count("id"))
    }
    commercial_value = _sum(
        PurchaseOrderLine.objects.filter(
            order__order_date__range=(start, end),
        ).exclude(order__status=PurchaseOrder.Status.CANCELLED),
        "line_total",
    )
    receipt_lines = PurchaseReceiptLine.objects.filter(
        receipt__received_date__range=(start, end),
        movement__reversal__isnull=True,
    )
    receipt_counts = _document_counts(
        PurchaseReceipt.objects.filter(received_date__range=(start, end)),
        PurchaseReceiptLine.objects.all(),
        "receipt",
    )
    buckets = _bucket_dates(start, end, granularity)
    order_trend = _trend_rows(
        PurchaseOrderLine.objects.filter(order__order_date__range=(start, end)).exclude(
            order__status=PurchaseOrder.Status.CANCELLED
        ),
        "order__order_date",
        "line_total",
        granularity,
        "order_id",
    )
    receipt_trend = _trend_rows(
        receipt_lines,
        "receipt__received_date",
        "line_total",
        granularity,
        "receipt_id",
    )
    recent = PurchaseOrder.objects.filter(order_date__range=(start, end)).select_related(
        "supplier"
    ).order_by("-order_date", "-created_at")[:recent_limit]
    return {
        "orders_total": orders.count(),
        "non_cancelled_orders_count": orders.exclude(
            status=PurchaseOrder.Status.CANCELLED
        ).count(),
        "cancelled_orders_count": orders.filter(
            status=PurchaseOrder.Status.CANCELLED
        ).count(),
        "orders_by_status": status_counts,
        "commercial_order_value": _money(commercial_value),
        "receipts_count": receipt_counts["total"],
        "active_receipts_count": receipt_counts["active"],
        "fully_reversed_receipts_count": receipt_counts["fully_reversed"],
        "net_received_value": _money(_sum(receipt_lines, "line_total")),
        "value_basis": "Commercial purchase-document snapshots; not accounting expense.",
        "count_basis": {
            "orders_total": "All purchase order documents in the period, including cancelled orders.",
            "non_cancelled_orders_count": "Purchase orders in the period excluding CANCELLED.",
            "cancelled_orders_count": "Purchase orders in the period with CANCELLED status.",
            "receipts_count": "All receipt documents in the period, including fully reversed receipts.",
            "active_receipts_count": "Receipts with at least one non-reversed line.",
            "fully_reversed_receipts_count": "Receipts with at least one reversed line and no non-reversed line.",
        },
        "trend": {
            "granularity": granularity,
            "orders": _trend_series(order_trend, buckets),
            "receipts": _trend_series(receipt_trend, buckets),
            "count_basis": {
                "orders": "Distinct non-cancelled purchase orders in each bucket.",
                "receipts": "Distinct receipts with at least one non-reversed line in each bucket.",
            },
        },
        "recent_orders": OwnerRecentPurchaseOrderSerializer(recent, many=True).data,
    }


def _stock_totals():
    totals = defaultdict(lambda: Decimal("0"))
    for row in ledger_balances():
        totals[row["product_id"]] += row["balance"] or Decimal("0")
    return totals


def _inventory_section(stock_totals):
    products = list(
        Product.objects.filter(pk__in=stock_totals.keys()).select_related(
            "category", "base_uom"
        )
    )
    product_map = {product.pk: product for product in products}
    categories = {category.code: category for category in ProductCategory.objects.filter(code__in=CATEGORY_CODES)}
    unit_names = {}
    grouped = {code: defaultdict(lambda: Decimal("0")) for code in CATEGORY_CODES}
    product_counts = defaultdict(int)
    for product_id, balance in stock_totals.items():
        product = product_map.get(product_id)
        if not product or balance == 0:
            continue
        code = product.category.code
        if code not in grouped:
            continue
        grouped[code][product.base_uom.code] += balance
        unit_names.setdefault(product.base_uom.code, product.base_uom.name_ar)
        product_counts[code] += 1

    category_rows = []
    for code in CATEGORY_CODES:
        category = categories.get(code)
        balances = [
            {
                "base_uom": unit_code,
                "base_uom_name_ar": unit_names.get(unit_code, unit_code),
                "quantity_base": _quantity(quantity),
            }
            for unit_code, quantity in sorted(grouped[code].items())
        ]
        category_rows.append({
            "code": code,
            "name_ar": category.name_ar if category else code,
            "name_en": category.name_en if category else "",
            "product_count": product_counts[code],
            "balances": balances,
        })
    return {
        "categories": category_rows,
        "products_with_balance": sum(product_counts.values()),
        "quantity_basis": "Base units, grouped by base UOM; different dimensions are never summed.",
        "valuation_available": False,
        "valuation_note": "No inventory valuation method or ledger unit cost is implemented.",
    }


def _low_stock_section(stock_totals):
    configured = Product.objects.filter(active=True, low_stock_threshold__gt=0).select_related(
        "category", "base_uom"
    )
    alerts = []
    for product in configured:
        balance = stock_totals.get(product.pk, Decimal("0"))
        if balance > product.low_stock_threshold:
            continue
        alerts.append({
            "product_id": product.pk,
            "sku": product.sku,
            "name_ar": product.name_ar,
            "category": product.category.code,
            "base_uom": product.base_uom.code,
            "balance_base": _quantity(balance),
            "threshold_base": _quantity(product.low_stock_threshold),
            "shortage_base": _quantity(max(product.low_stock_threshold - balance, Decimal("0"))),
            "is_out_of_stock": balance <= 0,
        })
    alerts.sort(key=lambda row: (not row["is_out_of_stock"], -Decimal(row["shortage_base"])))
    unconfigured_count = Product.objects.filter(active=True, low_stock_threshold=0).count()
    return {
        "alerts": alerts,
        "configured_thresholds_count": configured.count(),
        "unconfigured_active_products_count": unconfigured_count,
        "threshold_source": "catalog.Product.low_stock_threshold",
        "note": "Only active products with a positive configured threshold are alerted.",
    }


def _production_section(start, end):
    lines = list(ProductionLine.objects.all().order_by("code"))
    status_rows = (
        ProductionOrder.objects.filter(created_at__date__range=(start, end))
        .values("production_line_id", "status")
        .annotate(count=Count("id"))
    )
    status_by_line = defaultdict(dict)
    for row in status_rows:
        status_by_line[row["production_line_id"]][row["status"]] = row["count"]

    output_rows = (
        ProductionResult.objects.filter(occurred_at__date__range=(start, end))
        .values(
            "order__production_line_id",
            "order__product__base_uom__code",
            "order__product__base_uom__name_ar",
        )
        .annotate(good=Sum("good_quantity_base"), scrap=Sum("scrap_quantity_base"))
    )
    output_by_line = defaultdict(list)
    for row in output_rows:
        output_by_line[row["order__production_line_id"]].append({
            "base_uom": row["order__product__base_uom__code"],
            "base_uom_name_ar": row["order__product__base_uom__name_ar"],
            "good_quantity_base": _quantity(row["good"]),
            "scrap_quantity_base": _quantity(row["scrap"]),
        })

    status_values = [value for value, _ in ProductionOrder.Status.choices]
    payload = []
    for line in lines:
        counts = {status: status_by_line[line.pk].get(status, 0) for status in status_values}
        payload.append({
            "code": line.code,
            "name_ar": line.name_ar,
            "active": line.active,
            "total_orders": sum(counts.values()),
            "status_counts": counts,
            "output": sorted(output_by_line[line.pk], key=lambda row: row["base_uom"]),
        })
    return {
        "lines": payload,
        "status_labels": {value: label for value, label in ProductionOrder.Status.choices},
        "status_counts_basis": (
            "Counts of production orders created in the requested period (ProductionOrder.created_at), "
            "grouped by the order's current status."
        ),
        "output_basis": (
            "Good and scrap quantities from production results recorded in the requested period "
            "(ProductionResult.occurred_at)."
        ),
        "quantity_note": (
            "Order status counts and output use intentionally different date fields; output from an order "
            "created in an earlier period can appear in the requested period. Output is grouped by base UOM; "
            "quantities in different dimensions are not summed."
        ),
    }


def _recent_shipments(start, end, recent_limit):
    shipments = Shipment.objects.filter(shipment_date__range=(start, end)).select_related(
        "order", "order__customer"
    ).order_by("-shipment_date", "-created_at")[:recent_limit]
    return OwnerRecentShipmentSerializer(shipments, many=True).data


def _customers_section(start, end):
    return {
        "total": Customer.objects.count(),
        "active": Customer.objects.filter(active=True).count(),
        "with_orders_in_period": Customer.objects.filter(
            sales_orders__order_date__range=(start, end),
            sales_orders__status__in=[
                SalesOrder.Status.DRAFT,
                SalesOrder.Status.CONFIRMED,
                SalesOrder.Status.COMPLETED,
            ],
        ).distinct().count(),
        "note": "Contact master data only; no credit, tax, or payment terms are implemented.",
    }


def _suppliers_section():
    return {
        "total": Supplier.objects.count(),
        "active": Supplier.objects.filter(active=True).count(),
        "note": "Contact master data only; no bank, tax, invoice, or payment terms are stored.",
    }


def _costing_section():
    periods = list(CostingPeriod.objects.order_by("-year", "-month")[:6])
    period_rows = []
    for period in periods:
        stored = period.product_entries.aggregate(
            assigned=Sum("material_cost_assigned_to_good"),
            waste=Sum("waste_material_cost"),
        )
        assigned = stored["assigned"] or Decimal("0.00")
        waste = stored["waste"] or Decimal("0.00")
        allocated_total = (
            assigned
            + period.labor_total
            + period.electricity_total
            + period.water_total
            + period.other_industrial_total
        )
        period_rows.append({
            "year": period.year,
            "month": period.month,
            "status": period.status,
            "status_label": period.get_status_display(),
            "is_complete": period.is_complete,
            "calculation_status": "COMPLETE" if period.is_complete else "INCOMPLETE",
            "missing_data_notes": period.missing_data_notes,
            "labor_recorded": period.labor_recorded,
            "labor_completeness": "RECORDED" if period.labor_recorded else "MISSING",
            "allocation_policy": period.allocation_policy,
            "allocation_policy_label": period.get_allocation_policy_display(),
            "waste_policy": period.waste_policy,
            "waste_policy_label": period.get_waste_policy_display(),
            "material_valuation_method": period.material_valuation_method,
            "labor_total": _money(period.labor_total),
            "electricity_total": _money(period.electricity_total),
            "water_total": _money(period.water_total),
            "other_industrial_total": _money(period.other_industrial_total),
            "actual_material_cost": _money(period.materials_total),
            "material_cost_assigned_to_good": _money(assigned),
            "waste_material_cost": _money(waste),
            "materials_total": _money(period.materials_total),
            "total_production_cost": _money(period.total_production_cost),
            "calculated_at": period.calculated_at,
            "finalized_at": period.finalized_at,
            "reconciliation": {
                "actual_material_cost": _money(period.materials_total),
                "material_cost_assigned_to_good": _money(assigned),
                "waste_material_cost": _money(waste),
                "materials_reconcile": assigned + waste == period.materials_total,
                "labor_total": _money(period.labor_total),
                "electricity_total": _money(period.electricity_total),
                "water_total": _money(period.water_total),
                "other_industrial_total": _money(period.other_industrial_total),
                "total_production_cost": _money(period.total_production_cost),
                "cost_total_reconciles": allocated_total == period.total_production_cost,
                "formula": (
                    "material_cost_assigned_to_good + labor_total + electricity_total + "
                    "water_total + other_industrial_total = total_production_cost"
                ),
                "source": "Sum of persisted ProductCostingEntry rows and persisted CostingPeriod totals.",
            },
        })
    latest_entries = []
    if periods:
        latest = periods[0]
        for entry in ProductCostingEntry.objects.filter(period=latest).select_related(
            "product", "production_line"
        ).order_by("product__sku"):
            latest_entries.append({
                "product_sku": entry.product.sku,
                "product_name_ar": entry.product.name_ar,
                "production_line": entry.production_line.code if entry.production_line else None,
                "good_units_produced": _quantity(entry.good_units_produced),
                "scrap_units_produced": _quantity(entry.scrap_units_produced),
                "actual_material_cost": _money(entry.actual_material_cost),
                "material_cost_assigned_to_good": _money(entry.material_cost_assigned_to_good),
                "waste_material_cost": _money(entry.waste_material_cost),
                "allocated_labor_cost": _money(entry.allocated_labor_cost),
                "allocated_electricity_cost": _money(entry.allocated_electricity_cost),
                "allocated_water_cost": _money(entry.allocated_water_cost),
                "allocated_other_cost": _money(entry.allocated_other_cost),
                "total_production_cost": _money(entry.total_production_cost),
                "cost_per_good_unit": format_decimal(entry.cost_per_good_unit, 4),
                "selling_price_snapshot": format_decimal(entry.selling_price_snapshot, 4),
                "calculation_status": entry.calculation_status,
            })
    return {
        "periods": period_rows,
        "latest_period_entries": latest_entries,
        "source": "Persisted monthly costing outputs; the API never recalculates product cost.",
        "allocation_review": {
            "expense_allocation": "Labor, electricity, water, and other industrial costs are allocated by the monthly costing service and read from persisted period entries.",
            "independent_recalculation": False,
            "accounting_final": False,
            "reconciliation_basis": "Stored period totals and product-entry allocations only; arithmetic checks do not recalculate unit costs.",
            "note": "Costing remains provisional until the owner/accountant approves the valuation and treatment.",
        },
    }


def _financial_integrity():
    return {
        "accounting_available": False,
        "currency": None,
        "profit": None,
        "net_profit": None,
        "receivables": None,
        "payables": None,
        "cash_balance": None,
        "sales_commercial_value_is_revenue": False,
        "purchase_commercial_value_is_expense": False,
        "missing_dependencies": [
            "Shared accounting chart of accounts, journal, and ledger",
            "Customer invoices and credit notes",
            "Supplier invoices and credit notes",
            "Payments, receivables, and payables",
            "Validated accounting postings and closing periods",
        ],
        "note": "No authoritative profit, receivable, payable, or cash metric is reported by this API.",
    }


def _operational_alerts(stock_alerts):
    return [
        {
            "code": "LOW_STOCK",
            "severity": "WARNING",
            "count": len(stock_alerts["alerts"]),
            "message_ar": "أصناف وصلت إلى حد المخزون المنخفض المكوّن.",
        },
        {
            "code": "DRAFT_SALES_ORDERS",
            "severity": "INFO",
            "count": SalesOrder.objects.filter(status=SalesOrder.Status.DRAFT).count(),
            "message_ar": "أوامر بيع مسودة بانتظار المراجعة أو التأكيد.",
        },
        {
            "code": "DRAFT_PURCHASE_ORDERS",
            "severity": "INFO",
            "count": PurchaseOrder.objects.filter(status=PurchaseOrder.Status.DRAFT).count(),
            "message_ar": "أوامر شراء مسودة بانتظار المراجعة أو التأكيد.",
        },
        {
            "code": "PRODUCTION_IN_PROGRESS",
            "severity": "INFO",
            "count": ProductionOrder.objects.filter(
                status__in=[ProductionOrder.Status.RELEASED, ProductionOrder.Status.IN_PROGRESS]
            ).count(),
            "message_ar": "أوامر إنتاج مطلقة أو قيد التنفيذ.",
        },
        {
            "code": "COSTING_DRAFT_PERIODS",
            "severity": "INFO",
            "count": CostingPeriod.objects.filter(status=CostingPeriod.Status.DRAFT).count(),
            "message_ar": "فترات تكاليف لم تُعتمد نهائيًا.",
        },
    ]


def _period_payload(start, end, granularity):
    return {
        "start_date": start,
        "end_date": end,
        "days": (end - start).days + 1,
        "granularity": granularity,
        "timezone": timezone.get_current_timezone_name(),
    }


def _base(validated):
    start, end, granularity = _period(validated)
    recent_limit = validated.get("recent_limit") or 5
    return start, end, granularity, recent_limit


def inventory_payload(validated):
    start, end, granularity, _ = _base(validated)
    stock_totals = _stock_totals()
    low_stock = _low_stock_section(stock_totals)
    return {
        "generated_at": timezone.now(),
        "period": _period_payload(start, end, granularity),
        "inventory": _inventory_section(stock_totals),
        "low_stock": low_stock,
    }


def sales_payload(validated):
    start, end, granularity, recent_limit = _base(validated)
    return {
        "generated_at": timezone.now(),
        "period": _period_payload(start, end, granularity),
        "sales": _sales_section(start, end, granularity, recent_limit),
        "recent_shipments": _recent_shipments(start, end, recent_limit),
        "financial_integrity": _financial_integrity(),
    }


def purchases_payload(validated):
    start, end, granularity, recent_limit = _base(validated)
    return {
        "generated_at": timezone.now(),
        "period": _period_payload(start, end, granularity),
        "purchases": _purchases_section(start, end, granularity, recent_limit),
        "financial_integrity": _financial_integrity(),
    }


def production_payload(validated):
    start, end, granularity, _ = _base(validated)
    return {
        "generated_at": timezone.now(),
        "period": _period_payload(start, end, granularity),
        "production": _production_section(start, end),
    }


def costing_payload(validated):
    start, end, granularity, _ = _base(validated)
    return {
        "generated_at": timezone.now(),
        "period": _period_payload(start, end, granularity),
        "costing": _costing_section(),
        "financial_integrity": _financial_integrity(),
    }


def dashboard_payload(validated):
    start, end, granularity, recent_limit = _base(validated)
    stock_totals = _stock_totals()
    low_stock = _low_stock_section(stock_totals)
    sales = _sales_section(start, end, granularity, recent_limit)
    purchases = _purchases_section(start, end, granularity, recent_limit)
    return {
        "generated_at": timezone.now(),
        "period": _period_payload(start, end, granularity),
        "sales": sales,
        "purchases": purchases,
        "inventory": _inventory_section(stock_totals),
        "low_stock_alerts": low_stock["alerts"],
        "production": _production_section(start, end),
        "recent": {
            "sales_orders": sales["recent_orders"],
            "purchase_orders": purchases["recent_orders"],
            "shipments": _recent_shipments(start, end, recent_limit),
        },
        "customers": _customers_section(start, end),
        "suppliers": _suppliers_section(),
        "costing": _costing_section(),
        "operational_alerts": _operational_alerts(low_stock),
        "financial_integrity": _financial_integrity(),
        "data_sources": {
            "sales": "sales.SalesOrder, SalesOrderLine, Shipment, ShipmentLine",
            "purchases": "purchasing.PurchaseOrder, PurchaseOrderLine, PurchaseReceipt, PurchaseReceiptLine",
            "inventory": "shared inventory ledger through inventory.services.ledger_balances",
            "production": "manufacturing.ProductionOrder and ProductionResult",
            "costing": "persisted costing.CostingPeriod and ProductCostingEntry",
            "accounting": "unavailable; not implemented",
        },
    }
