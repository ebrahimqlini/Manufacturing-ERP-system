"""Read-only consolidated reporting built from persisted ERP records.

The reporting layer intentionally never recalculates inventory balances, order
values, production output, or product costs. It reads the shared inventory
service and persisted costing entries, applies display-level reconciliation
arithmetic, and labels every date/status basis in the returned payload.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db.models import (
    Count,
    DecimalField,
    Exists,
    F,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, Greatest, TruncDate, TruncMonth
from django.utils import timezone

from catalog.models import Product, ProductCategory
from costing.models import (
    CostingPeriod,
    MonthlyExpense,
    MonthlyProfitCOGSLine,
    MonthlyProfitPeriod,
    ProductCostingEntry,
    ProductionOrderCostingEntry,
)
from inventory.models import StockMovement
from inventory.services import ledger_balances
from manufacturing.models import MaterialIssue, ProductionLine, ProductionOrder, ProductionResult
from purchasing.models import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseReceipt,
    PurchaseReceiptLine,
    Supplier,
)
from sales.models import Customer, SalesOrder, SalesOrderLine, Shipment, ShipmentLine

MONEY = Decimal("0.01")
QUANTITY = Decimal("0.000000000001")
PERCENT = Decimal("0.01")

DECIMAL_30_12 = DecimalField(max_digits=30, decimal_places=12)
DECIMAL_18_2 = DecimalField(max_digits=18, decimal_places=2)

CATEGORY_CODES = (
    "raw-material",
    "work-in-progress",
    "finished-goods",
    "packaging-material",
    "scrap-waste",
    "consumables",
)

CATEGORY_LABELS = {
    "raw-material": "مواد خام",
    "work-in-progress": "إنتاج تحت التشغيل",
    "finished-goods": "منتجات تامة",
    "packaging-material": "مواد تغليف",
    "scrap-waste": "هالك وخردة",
    "consumables": "مستهلكات",
}

SALES_ACTIVE_STATUSES = (
    SalesOrder.Status.DRAFT,
    SalesOrder.Status.CONFIRMED,
    SalesOrder.Status.COMPLETED,
)

PURCHASE_ACTIVE_STATUSES = (
    PurchaseOrder.Status.DRAFT,
    PurchaseOrder.Status.CONFIRMED,
)


def _decimal(value):
    if value is None:
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(value)


def money(value):
    """Format a monetary value with the project's two-decimal currency precision."""
    return format(_decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP), "f")


def quantity(value):
    """Format a base-unit quantity without silently rounding stored precision."""
    return format(_decimal(value).quantize(QUANTITY, rounding=ROUND_HALF_UP), "f")


def percent(value):
    if value is None:
        return None
    return format(_decimal(value).quantize(PERCENT, rounding=ROUND_HALF_UP), "f")


def _sum(queryset, field):
    return queryset.aggregate(total=Sum(field))["total"] or Decimal("0")


def resolve_period(start_date=None, end_date=None, default_days=30):
    """Resolve and validate an inclusive reporting period.

    The API serializer validates the same boundaries before this point. Keeping
    the rule here as well protects the server-rendered reports.
    """
    end = end_date or timezone.localdate()
    start = start_date or (end - timedelta(days=default_days - 1))
    if start > end:
        raise ValidationError({"end_date": "تاريخ النهاية يسبق تاريخ البداية."})
    if (end - start).days > 366:
        raise ValidationError({"end_date": "الحد الأقصى للفترة هو 366 يومًا."})
    return start, end


def granularity_for(start, end, requested=None):
    if requested in ("day", "month"):
        return requested
    return "month" if (end - start).days > 90 else "day"


def period_payload(start, end, granularity):
    return {
        "start_date": start,
        "end_date": end,
        "days": (end - start).days + 1,
        "granularity": granularity,
        "timezone": timezone.get_current_timezone_name(),
    }


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
            current = (
                date(current.year + 1, 1, 1)
                if current.month == 12
                else date(current.year, current.month + 1, 1)
            )
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
        values[_bucket(row["bucket"], granularity)] = {
            "value": row["value"] or Decimal("0"),
            "count": row["count"] or 0,
        }
    return values


def _trend_series(values, buckets):
    return [
        {
            "date": bucket.isoformat(),
            "count": values.get(bucket, {}).get("count", 0),
            "value": money(values.get(bucket, {}).get("value", Decimal("0"))),
        }
        for bucket in buckets
    ]


def _sum_subquery(model, relation_filter, group_field, sum_field, output_field):
    subquery = (
        model.objects.filter(**relation_filter)
        .values(group_field)
        .annotate(total=Sum(sum_field))
        .values("total")
    )
    return Subquery(subquery, output_field=output_field)


def annotate_sales_order_progress(queryset):
    """Attach ordered and net-shipped base quantities and values in one query set."""
    ordered_base = _sum_subquery(
        SalesOrderLine, {"order": OuterRef("pk")}, "order", "quantity_base", DECIMAL_30_12
    )
    shipped_base = _sum_subquery(
        ShipmentLine,
        {"shipment__order": OuterRef("pk"), "movement__reversal__isnull": True},
        "shipment__order",
        "quantity_base",
        DECIMAL_30_12,
    )
    ordered_value = _sum_subquery(
        SalesOrderLine, {"order": OuterRef("pk")}, "order", "line_total", DECIMAL_18_2
    )
    shipped_value = _sum_subquery(
        ShipmentLine,
        {"shipment__order": OuterRef("pk"), "movement__reversal__isnull": True},
        "shipment__order",
        "line_total",
        DECIMAL_18_2,
    )
    return queryset.annotate(
        report_ordered_base=Coalesce(
            ordered_base, Value(Decimal("0")), output_field=DECIMAL_30_12
        ),
        report_shipped_base=Coalesce(
            shipped_base, Value(Decimal("0")), output_field=DECIMAL_30_12
        ),
        report_ordered_value=Coalesce(
            ordered_value, Value(Decimal("0.00")), output_field=DECIMAL_18_2
        ),
        report_shipped_value=Coalesce(
            shipped_value, Value(Decimal("0.00")), output_field=DECIMAL_18_2
        ),
    )


def annotate_purchase_order_progress(queryset):
    ordered_base = _sum_subquery(
        PurchaseOrderLine, {"order": OuterRef("pk")}, "order", "quantity_base", DECIMAL_30_12
    )
    received_base = _sum_subquery(
        PurchaseReceiptLine,
        {"receipt__order": OuterRef("pk"), "movement__reversal__isnull": True},
        "receipt__order",
        "quantity_base",
        DECIMAL_30_12,
    )
    ordered_value = _sum_subquery(
        PurchaseOrderLine, {"order": OuterRef("pk")}, "order", "line_total", DECIMAL_18_2
    )
    received_value = _sum_subquery(
        PurchaseReceiptLine,
        {"receipt__order": OuterRef("pk"), "movement__reversal__isnull": True},
        "receipt__order",
        "line_total",
        DECIMAL_18_2,
    )
    return queryset.annotate(
        report_ordered_base=Coalesce(
            ordered_base, Value(Decimal("0")), output_field=DECIMAL_30_12
        ),
        report_received_base=Coalesce(
            received_base, Value(Decimal("0")), output_field=DECIMAL_30_12
        ),
        report_ordered_value=Coalesce(
            ordered_value, Value(Decimal("0.00")), output_field=DECIMAL_18_2
        ),
        report_received_value=Coalesce(
            received_value, Value(Decimal("0.00")), output_field=DECIMAL_18_2
        ),
    )


def _order_shipment_state(ordered_base, shipped_base):
    if ordered_base <= 0 or shipped_base <= 0:
        return SalesOrder.ShipmentState.NOT_SHIPPED
    if shipped_base < ordered_base:
        return SalesOrder.ShipmentState.PARTIALLY_SHIPPED
    return SalesOrder.ShipmentState.SHIPPED


def _sales_order_row(order):
    lines = list(order.lines.all())
    has_unpriced = bool(order.customer_submitted and any(line.unit_price <= 0 for line in lines))
    if not order.customer_submitted:
        pricing_status = "RECORDED"
    elif has_unpriced:
        pricing_status = "PENDING_REVIEW" if order.status == SalesOrder.Status.DRAFT else "UNPRICED"
    else:
        pricing_status = "RECORDED"
    ordered_base = _decimal(getattr(order, "report_ordered_base", order.ordered_quantity_base))
    shipped_base = _decimal(getattr(order, "report_shipped_base", order.shipped_quantity_base))
    state = _order_shipment_state(ordered_base, shipped_base)
    return {
        "order_number": order.order_number,
        "customer_code": order.customer.code,
        "customer_name": order.customer.name_ar,
        "order_date": order.order_date,
        "requested_delivery_date": order.requested_delivery_date,
        "customer_reference": order.customer_reference,
        "status": order.status,
        "status_display": order.get_status_display(),
        "ordered_value": money(getattr(order, "report_ordered_value", order.ordered_value)),
        "shipped_value": money(getattr(order, "report_shipped_value", order.shipped_value)),
        "ordered_quantity_base": quantity(ordered_base),
        "shipped_quantity_base": quantity(shipped_base),
        "outstanding_quantity_base": quantity(max(ordered_base - shipped_base, Decimal("0"))),
        "shipment_state": state,
        "shipment_state_display": dict(SalesOrder.ShipmentState.choices)[state],
        "customer_submitted": order.customer_submitted,
        "pricing_status": pricing_status,
        "pricing_pending": has_unpriced,
        "created_at": order.created_at,
    }


def _shipment_rows(queryset, limit):
    rows = []
    shipments = queryset.select_related("order", "order__customer").prefetch_related(
        Prefetch(
            "lines",
            queryset=ShipmentLine.objects.select_related("movement").prefetch_related("movement__reversal"),
        )
    )[:limit]
    for shipment in shipments:
        lines = list(shipment.lines.all())
        total = sum((line.line_total for line in lines), Decimal("0.00"))
        active_lines = [line for line in lines if not _is_reversed(line.movement)]
        net = sum((line.line_total for line in active_lines), Decimal("0.00"))
        reversed_count = len(lines) - len(active_lines)
        rows.append({
            "reference": shipment.reference,
            "order_number": shipment.order.order_number,
            "customer_code": shipment.order.customer.code,
            "customer_name": shipment.order.customer.name_ar,
            "shipment_date": shipment.shipment_date,
            "net_value": money(net),
            "total_value": money(total),
            "reversed_value": money(total - net),
            "line_count": len(lines),
            "reversed_line_count": reversed_count,
            "is_fully_reversed": bool(lines) and reversed_count == len(lines),
            "has_reversed_lines": reversed_count > 0,
        })
    return rows


def _is_reversed(movement):
    try:
        return movement.reversal is not None
    except ObjectDoesNotExist:
        return False


def _document_counts(queryset, line_queryset, relation_field):
    """Return total/active/partially/fully reversed document counts.

    A document is active when it has at least one non-reversed line. A document
    is fully reversed when it has at least one reversed line and no active line.
    A partially reversed document has both active and reversed lines. The total
    deliberately includes cancelled/reversed documents so the audit population
    is not hidden.
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
        "partially_reversed": annotated.filter(
            _has_active_line=True, _has_reversed_line=True
        ).count(),
        "fully_reversed": annotated.filter(
            _has_active_line=False, _has_reversed_line=True
        ).count(),
    }


def _status_count_map(queryset, field="status"):
    return {
        row[field]: row["count"]
        for row in queryset.values(field).annotate(count=Count("id"))
    }


def _has_unpriced_order_expression():
    return Exists(SalesOrderLine.objects.filter(order=OuterRef("pk"), unit_price__lte=0))


def _quantity_rows(queryset, quantity_field, value_field, group_fields, count_field):
    fields = list(group_fields)
    return (
        queryset.values(*fields)
        .annotate(
            quantity=Sum(quantity_field),
            value=Sum(value_field),
            document_count=Count(count_field, distinct=True),
        )
        .order_by(*fields)
    )


def _quantity_map(rows, code_key="base_uom_code"):
    totals = defaultdict(lambda: Decimal("0"))
    names = {}
    for row in rows:
        code = row[code_key]
        totals[code] += row["quantity"] or Decimal("0")
        names[code] = row.get("base_uom_name_ar", code)
    return totals, names


def sales_report(start, end, granularity, recent_limit=5, dimension_limit=20):
    """Consolidated sales and shipment report.

    Order values are commercial document snapshots, not accounting revenue.
    Cancelled orders are excluded from active totals but remain in document
    counts; reversed shipment lines are excluded from net shipment values.
    """
    orders = SalesOrder.objects.filter(order_date__range=(start, end))
    non_cancelled = orders.exclude(status=SalesOrder.Status.CANCELLED)
    status_counts = _status_count_map(orders)
    annotated_orders = annotate_sales_order_progress(non_cancelled)
    unpriced_exists = _has_unpriced_order_expression()
    customer_orders = orders.filter(customer_submitted=True).annotate(
        _has_unpriced=unpriced_exists
    )
    pending_review = customer_orders.filter(
        status=SalesOrder.Status.DRAFT, _has_unpriced=True
    ).count()
    unpriced_legacy = customer_orders.exclude(status=SalesOrder.Status.DRAFT).filter(
        _has_unpriced=True
    ).count()

    ordered_lines = SalesOrderLine.objects.filter(
        order__order_date__range=(start, end)
    ).exclude(order__status=SalesOrder.Status.CANCELLED)
    ordered_quantity_rows = list(
        _quantity_rows(
            ordered_lines,
            "quantity_base",
            "line_total",
            (
                "product__base_uom__code",
                "product__base_uom__name_ar",
            ),
            "order_id",
        )
    )
    ordered_quantities = [
        {
            "base_uom": row["product__base_uom__code"],
            "base_uom_name_ar": row["product__base_uom__name_ar"],
            "quantity_base": quantity(row["quantity"]),
            "value": money(row["value"]),
            "order_count": row["document_count"],
        }
        for row in ordered_quantity_rows
    ]
    ordered_totals, ordered_names = _quantity_map(
        [
            {
                "base_uom_code": row["product__base_uom__code"],
                "base_uom_name_ar": row["product__base_uom__name_ar"],
                "quantity": row["quantity"],
            }
            for row in ordered_quantity_rows
        ]
    )
    commercial_value = _sum(ordered_lines, "line_total")

    shipment_lines_all = ShipmentLine.objects.filter(
        shipment__shipment_date__range=(start, end)
    )
    active_shipment_lines = shipment_lines_all.filter(movement__reversal__isnull=True)
    reversed_shipment_lines = shipment_lines_all.filter(movement__reversal__isnull=False)
    shipment_counts = _document_counts(
        Shipment.objects.filter(shipment_date__range=(start, end)),
        ShipmentLine.objects.all(),
        "shipment",
    )
    shipped_quantity_rows = list(
        _quantity_rows(
            active_shipment_lines,
            "quantity_base",
            "line_total",
            (
                "order_line__product__base_uom__code",
                "order_line__product__base_uom__name_ar",
            ),
            "shipment_id",
        )
    )
    shipped_quantities = [
        {
            "base_uom": row["order_line__product__base_uom__code"],
            "base_uom_name_ar": row["order_line__product__base_uom__name_ar"],
            "quantity_base": quantity(row["quantity"]),
            "value": money(row["value"]),
            "shipment_count": row["document_count"],
        }
        for row in shipped_quantity_rows
    ]
    shipped_totals, shipped_names = _quantity_map(
        [
            {
                "base_uom_code": row["order_line__product__base_uom__code"],
                "base_uom_name_ar": row["order_line__product__base_uom__name_ar"],
                "quantity": row["quantity"],
            }
            for row in shipped_quantity_rows
        ]
    )
    reversed_quantity_rows = list(
        _quantity_rows(
            reversed_shipment_lines,
            "quantity_base",
            "line_total",
            (
                "order_line__product__base_uom__code",
                "order_line__product__base_uom__name_ar",
            ),
            "shipment_id",
        )
    )
    reversed_quantities = [
        {
            "base_uom": row["order_line__product__base_uom__code"],
            "base_uom_name_ar": row["order_line__product__base_uom__name_ar"],
            "quantity_base": quantity(row["quantity"]),
            "value": money(row["value"]),
            "shipment_count": row["document_count"],
        }
        for row in reversed_quantity_rows
    ]

    reversed_totals, reversed_names = _quantity_map(
        [
            {
                "base_uom_code": row["order_line__product__base_uom__code"],
                "base_uom_name_ar": row["order_line__product__base_uom__name_ar"],
                "quantity": row["quantity"],
            }
            for row in reversed_quantity_rows
        ]
    )
    outstanding_quantities = []
    quantity_summary = []
    for code in sorted(set(ordered_totals) | set(shipped_totals) | set(reversed_totals)):
        outstanding = max(ordered_totals.get(code, Decimal("0")) - shipped_totals.get(code, Decimal("0")), Decimal("0"))
        name = (
            ordered_names.get(code)
            or shipped_names.get(code)
            or reversed_names.get(code)
            or code
        )
        outstanding_quantities.append({
            "base_uom": code,
            "base_uom_name_ar": name,
            "quantity_base": quantity(outstanding),
        })
        quantity_summary.append({
            "base_uom": code,
            "base_uom_name_ar": name,
            "ordered_quantity_base": quantity(ordered_totals.get(code, Decimal("0"))),
            "shipped_quantity_base": quantity(shipped_totals.get(code, Decimal("0"))),
            "reversed_quantity_base": quantity(reversed_totals.get(code, Decimal("0"))),
            "outstanding_quantity_base": quantity(outstanding),
        })

    partial_shipments = annotated_orders.filter(
        report_shipped_base__gt=0,
        report_shipped_base__lt=F("report_ordered_base"),
    ).count()
    no_shipments = annotated_orders.filter(
        report_ordered_base__gt=0, report_shipped_base=0
    ).count()
    fully_shipped = annotated_orders.filter(
        report_ordered_base__gt=0,
        report_shipped_base__gte=F("report_ordered_base"),
    ).count()

    customer_rows = list(
        _quantity_rows(
            ordered_lines,
            "quantity_base",
            "line_total",
            (
                "order__customer_id",
                "order__customer__code",
                "order__customer__name_ar",
                "product__base_uom__code",
                "product__base_uom__name_ar",
            ),
            "order_id",
        )
    )
    customer_order_counts = {
        row["order__customer_id"]: row["count"]
        for row in ordered_lines.values("order__customer_id").annotate(
            count=Count("order_id", distinct=True)
        )
    }
    customer_map = {}
    for row in customer_rows:
        key = row["order__customer_id"]
        item = customer_map.setdefault(
            key,
            {
                "customer_id": key,
                "customer_code": row["order__customer__code"],
                "customer_name": row["order__customer__name_ar"],
                "order_count": 0,
                "value": Decimal("0.00"),
                "quantities": defaultdict(lambda: Decimal("0")),
                "quantity_names": {},
            },
        )
        item["order_count"] = customer_order_counts.get(key, 0)
        item["value"] += row["value"] or Decimal("0.00")
        code = row["product__base_uom__code"]
        item["quantities"][code] += row["quantity"] or Decimal("0")
        item["quantity_names"][code] = row["product__base_uom__name_ar"]
    by_customer = []
    for item in customer_map.values():
        by_customer.append({
            "customer_id": item["customer_id"],
            "customer_code": item["customer_code"],
            "customer_name": item["customer_name"],
            "order_count": item["order_count"],
            "ordered_value": money(item["value"]),
            "quantities": [
                {
                    "base_uom": code,
                    "base_uom_name_ar": item["quantity_names"].get(code, code),
                    "quantity_base": quantity(value),
                }
                for code, value in sorted(item["quantities"].items())
            ],
        })
    by_customer.sort(key=lambda row: (-Decimal(row["ordered_value"]), row["customer_code"]))

    product_rows = list(
        _quantity_rows(
            ordered_lines,
            "quantity_base",
            "line_total",
            (
                "product_id",
                "product__sku",
                "product__name_ar",
                "product__base_uom__code",
                "product__base_uom__name_ar",
            ),
            "order_id",
        )
    )
    by_product = [
        {
            "product_id": row["product_id"],
            "sku": row["product__sku"],
            "name_ar": row["product__name_ar"],
            "base_uom": row["product__base_uom__code"],
            "base_uom_name_ar": row["product__base_uom__name_ar"],
            "quantity_base": quantity(row["quantity"]),
            "ordered_value": money(row["value"]),
            "order_count": row["document_count"],
        }
        for row in product_rows
    ]
    by_product.sort(key=lambda row: (-Decimal(row["ordered_value"]), row["sku"]))

    buckets = _bucket_dates(start, end, granularity)
    order_trend = _trend_rows(
        ordered_lines,
        "order__order_date",
        "line_total",
        granularity,
        "order_id",
    )
    shipment_trend = _trend_rows(
        active_shipment_lines,
        "shipment__shipment_date",
        "line_total",
        granularity,
        "shipment_id",
    )
    recent = annotate_sales_order_progress(
        SalesOrder.objects.filter(order_date__range=(start, end))
        .select_related("customer")
        .prefetch_related("lines")
        .order_by("-order_date", "-created_at", "-pk")
    )[:recent_limit]

    return {
        "orders_total": orders.count(),
        "non_cancelled_orders_count": non_cancelled.count(),
        "cancelled_orders_count": orders.filter(status=SalesOrder.Status.CANCELLED).count(),
        "draft_orders_count": status_counts.get(SalesOrder.Status.DRAFT, 0),
        "confirmed_orders_count": status_counts.get(SalesOrder.Status.CONFIRMED, 0),
        "completed_orders_count": status_counts.get(SalesOrder.Status.COMPLETED, 0),
        "orders_by_status": status_counts,
        "status_labels": dict(SalesOrder.Status.choices),
        "status_count_rows": [
            {"value": value, "label": label, "count": status_counts.get(value, 0)}
            for value, label in SalesOrder.Status.choices
        ],
        "customer_submitted_orders_count": customer_orders.count(),
        "customer_price_review_pending_count": pending_review,
        "customer_price_review_draft_count": pending_review,
        "legacy_unpriced_confirmed_orders_count": unpriced_legacy,
        "commercial_order_value": money(commercial_value),
        "ordered_quantities": ordered_quantities,
        "ordered_line_count": ordered_lines.count(),
        "shipments_count": shipment_counts["total"],
        "active_shipments_count": shipment_counts["active"],
        "partially_reversed_shipments_count": shipment_counts["partially_reversed"],
        "fully_reversed_shipments_count": shipment_counts["fully_reversed"],
        "net_shipped_value": money(_sum(active_shipment_lines, "line_total")),
        "total_shipment_value": money(_sum(shipment_lines_all, "line_total")),
        "reversed_shipment_value": money(_sum(reversed_shipment_lines, "line_total")),
        "shipped_quantities": shipped_quantities,
        "reversed_shipped_quantities": reversed_quantities,
        "outstanding_quantities": outstanding_quantities,
        "quantity_summary": quantity_summary,
        "partially_shipped_orders_count": partial_shipments,
        "unshipped_orders_count": no_shipments,
        "fully_shipped_orders_count": fully_shipped,
        "by_customer": by_customer[:dimension_limit],
        "customers_with_orders_count": len(by_customer),
        "top_customers": by_customer[:5],
        "by_product": by_product[:dimension_limit],
        "products_with_orders_count": len(by_product),
        "top_products": by_product[:5],
        "value_basis": "Commercial sales-document snapshots; not accounting revenue.",
        "quantity_basis": (
            "Ordered, shipped, reversed, and outstanding quantities are base-unit quantities "
            "from line snapshots, grouped by base UOM. Different dimensions are never summed."
        ),
        "count_basis": {
            "orders_total": "All sales order documents in the period, including cancelled orders.",
            "non_cancelled_orders_count": "Sales orders in the period excluding CANCELLED.",
            "cancelled_orders_count": "Sales orders in the period with CANCELLED status.",
            "customer_submitted_orders_count": "Orders in the period submitted by a customer account.",
            "customer_price_review_pending_count": "Draft customer-submitted orders with at least one zero-priced line.",
            "legacy_unpriced_confirmed_orders_count": "Non-draft customer orders that still carry a zero-priced line.",
            "partially_shipped_orders_count": "Non-cancelled orders with shipped quantity greater than zero and less than ordered quantity.",
            "unshipped_orders_count": "Non-cancelled orders with ordered quantity and no active shipment quantity.",
            "fully_shipped_orders_count": "Non-cancelled orders whose active shipped quantity is at least the ordered quantity.",
            "shipments_count": "All shipment documents in the period, including fully reversed shipments.",
            "active_shipments_count": "Shipments with at least one non-reversed line.",
            "partially_reversed_shipments_count": "Shipments with both active and reversed lines.",
            "fully_reversed_shipments_count": "Shipments with at least one reversed line and no active line.",
        },
        "trend": {
            "granularity": granularity,
            "orders": _trend_series(order_trend, buckets),
            "shipments": _trend_series(shipment_trend, buckets),
            "count_basis": {
                "orders": "Distinct non-cancelled sales orders in each bucket.",
                "shipments": "Distinct shipments with at least one non-reversed line in each bucket.",
            },
            "value_basis": "Commercial line snapshots; cancelled order lines and reversed shipment lines are excluded.",
        },
        "recent_orders": [_sales_order_row(order) for order in recent],
        "pending_price_orders": [
            _sales_order_row(order)
            for order in annotate_sales_order_progress(
                customer_orders.filter(_has_unpriced=True)
                .select_related("customer")
                .prefetch_related("lines")
                .order_by("-order_date", "-created_at")
            )[:recent_limit]
        ],
        "recent_shipments": _shipment_rows(
            Shipment.objects.filter(shipment_date__range=(start, end)).order_by(
                "-shipment_date", "-created_at", "-pk"
            ),
            recent_limit,
        ),
    }


def _purchase_order_row(order):
    ordered_base = _decimal(getattr(order, "report_ordered_base", order.ordered_quantity_base))
    received_base = _decimal(getattr(order, "report_received_base", order.received_quantity_base))
    if ordered_base <= 0 or received_base <= 0:
        receipt_state = PurchaseOrder.ReceiptState.NOT_RECEIVED
    elif received_base < ordered_base:
        receipt_state = PurchaseOrder.ReceiptState.PARTIALLY_RECEIVED
    else:
        receipt_state = PurchaseOrder.ReceiptState.RECEIVED
    return {
        "order_number": order.order_number,
        "supplier_code": order.supplier.code,
        "supplier_name": order.supplier.name_ar,
        "order_date": order.order_date,
        "expected_date": order.expected_date,
        "supplier_reference": order.supplier_reference,
        "status": order.status,
        "status_display": order.get_status_display(),
        "receipt_state": receipt_state,
        "receipt_state_display": dict(PurchaseOrder.ReceiptState.choices)[receipt_state],
        "ordered_value": money(getattr(order, "report_ordered_value", order.ordered_value)),
        "received_value": money(getattr(order, "report_received_value", order.received_value)),
        "ordered_quantity_base": quantity(ordered_base),
        "received_quantity_base": quantity(received_base),
        "outstanding_quantity_base": quantity(max(ordered_base - received_base, Decimal("0"))),
        "created_at": order.created_at,
    }


def _receipt_rows(queryset, limit):
    rows = []
    receipts = queryset.select_related("order", "order__supplier").prefetch_related(
        Prefetch(
            "lines",
            queryset=PurchaseReceiptLine.objects.select_related("movement").prefetch_related(
                "movement__reversal"
            ),
        )
    )[:limit]
    for receipt in receipts:
        lines = list(receipt.lines.all())
        total = sum((line.line_total for line in lines), Decimal("0.00"))
        active_lines = [line for line in lines if not _is_reversed(line.movement)]
        net = sum((line.line_total for line in active_lines), Decimal("0.00"))
        reversed_count = len(lines) - len(active_lines)
        rows.append({
            "reference": receipt.reference,
            "order_number": receipt.order.order_number,
            "supplier_code": receipt.order.supplier.code,
            "supplier_name": receipt.order.supplier.name_ar,
            "received_date": receipt.received_date,
            "net_value": money(net),
            "total_value": money(total),
            "reversed_value": money(total - net),
            "line_count": len(lines),
            "reversed_line_count": reversed_count,
            "is_fully_reversed": bool(lines) and reversed_count == len(lines),
            "has_reversed_lines": reversed_count > 0,
        })
    return rows


def purchases_report(start, end, granularity, recent_limit=5, dimension_limit=20):
    """Consolidated purchase order and receipt report.

    Order value and receipt value are separate commercial snapshots. Cancelled
    purchase orders are excluded from active totals; fully or partially reversed
    receipts are excluded from net received value and reported separately.
    """
    orders = PurchaseOrder.objects.filter(order_date__range=(start, end))
    non_cancelled = orders.exclude(status=PurchaseOrder.Status.CANCELLED)
    status_counts = _status_count_map(orders)
    annotated_orders = annotate_purchase_order_progress(non_cancelled)
    ordered_lines = PurchaseOrderLine.objects.filter(
        order__order_date__range=(start, end)
    ).exclude(order__status=PurchaseOrder.Status.CANCELLED)
    ordered_quantity_rows = list(
        _quantity_rows(
            ordered_lines,
            "quantity_base",
            "line_total",
            (
                "material__base_uom__code",
                "material__base_uom__name_ar",
            ),
            "order_id",
        )
    )
    ordered_quantities = [
        {
            "base_uom": row["material__base_uom__code"],
            "base_uom_name_ar": row["material__base_uom__name_ar"],
            "quantity_base": quantity(row["quantity"]),
            "value": money(row["value"]),
            "order_count": row["document_count"],
        }
        for row in ordered_quantity_rows
    ]
    ordered_totals, ordered_names = _quantity_map(
        [
            {
                "base_uom_code": row["material__base_uom__code"],
                "base_uom_name_ar": row["material__base_uom__name_ar"],
                "quantity": row["quantity"],
            }
            for row in ordered_quantity_rows
        ]
    )

    received_subquery = _sum_subquery(
        PurchaseReceiptLine,
        {"order_line": OuterRef("pk"), "movement__reversal__isnull": True},
        "order_line",
        "quantity_base",
        DECIMAL_30_12,
    )
    outstanding_lines = ordered_lines.annotate(
        report_received_base=Coalesce(
            received_subquery, Value(Decimal("0")), output_field=DECIMAL_30_12
        ),
        report_outstanding_base=Greatest(
            F("quantity_base")
            - Coalesce(received_subquery, Value(Decimal("0")), output_field=DECIMAL_30_12),
            Value(Decimal("0")),
        ),
    )
    outstanding_quantity_rows = list(
        outstanding_lines.values(
            "material__base_uom__code", "material__base_uom__name_ar"
        ).annotate(quantity=Sum("report_outstanding_base"))
    )
    outstanding_quantities = [
        {
            "base_uom": row["material__base_uom__code"],
            "base_uom_name_ar": row["material__base_uom__name_ar"],
            "quantity_base": quantity(row["quantity"]),
        }
        for row in outstanding_quantity_rows
    ]

    receipt_lines_all = PurchaseReceiptLine.objects.filter(
        receipt__received_date__range=(start, end)
    )
    active_receipt_lines = receipt_lines_all.filter(movement__reversal__isnull=True)
    reversed_receipt_lines = receipt_lines_all.filter(movement__reversal__isnull=False)
    receipt_counts = _document_counts(
        PurchaseReceipt.objects.filter(received_date__range=(start, end)),
        PurchaseReceiptLine.objects.all(),
        "receipt",
    )
    received_quantity_rows = list(
        _quantity_rows(
            active_receipt_lines,
            "quantity_base",
            "line_total",
            (
                "order_line__material__base_uom__code",
                "order_line__material__base_uom__name_ar",
            ),
            "receipt_id",
        )
    )
    received_totals, received_names = _quantity_map(
        [
            {
                "base_uom_code": row["order_line__material__base_uom__code"],
                "base_uom_name_ar": row["order_line__material__base_uom__name_ar"],
                "quantity": row["quantity"],
            }
            for row in received_quantity_rows
        ]
    )
    received_quantities = [
        {
            "base_uom": row["order_line__material__base_uom__code"],
            "base_uom_name_ar": row["order_line__material__base_uom__name_ar"],
            "quantity_base": quantity(row["quantity"]),
            "value": money(row["value"]),
            "receipt_count": row["document_count"],
        }
        for row in received_quantity_rows
    ]
    reversed_quantity_rows = list(
        _quantity_rows(
            reversed_receipt_lines,
            "quantity_base",
            "line_total",
            (
                "order_line__material__base_uom__code",
                "order_line__material__base_uom__name_ar",
            ),
            "receipt_id",
        )
    )
    reversed_quantities = [
        {
            "base_uom": row["order_line__material__base_uom__code"],
            "base_uom_name_ar": row["order_line__material__base_uom__name_ar"],
            "quantity_base": quantity(row["quantity"]),
            "value": money(row["value"]),
            "receipt_count": row["document_count"],
        }
        for row in reversed_quantity_rows
    ]
    reversed_totals, reversed_names = _quantity_map(
        [
            {
                "base_uom_code": row["order_line__material__base_uom__code"],
                "base_uom_name_ar": row["order_line__material__base_uom__name_ar"],
                "quantity": row["quantity"],
            }
            for row in reversed_quantity_rows
        ]
    )
    outstanding_map = {
        row["material__base_uom__code"]: row["quantity"]
        for row in outstanding_quantity_rows
    }
    quantity_summary = []
    for code in sorted(
        set(ordered_totals) | set(received_totals) | set(reversed_totals) | set(outstanding_map)
    ):
        quantity_summary.append({
            "base_uom": code,
            "base_uom_name_ar": (
                ordered_names.get(code)
                or received_names.get(code)
                or reversed_names.get(code)
                or code
            ),
            "ordered_quantity_base": quantity(ordered_totals.get(code, Decimal("0"))),
            "received_quantity_base": quantity(received_totals.get(code, Decimal("0"))),
            "reversed_quantity_base": quantity(reversed_totals.get(code, Decimal("0"))),
            "outstanding_quantity_base": quantity(outstanding_map.get(code, Decimal("0"))),
        })

    supplier_rows = list(
        _quantity_rows(
            ordered_lines,
            "quantity_base",
            "line_total",
            (
                "order__supplier_id",
                "order__supplier__code",
                "order__supplier__name_ar",
                "material__base_uom__code",
                "material__base_uom__name_ar",
            ),
            "order_id",
        )
    )
    supplier_order_counts = {
        row["order__supplier_id"]: row["count"]
        for row in ordered_lines.values("order__supplier_id").annotate(
            count=Count("order_id", distinct=True)
        )
    }
    supplier_map = {}
    for row in supplier_rows:
        key = row["order__supplier_id"]
        item = supplier_map.setdefault(
            key,
            {
                "supplier_id": key,
                "supplier_code": row["order__supplier__code"],
                "supplier_name": row["order__supplier__name_ar"],
                "order_count": supplier_order_counts.get(key, 0),
                "ordered_value": Decimal("0.00"),
                "received_value": Decimal("0.00"),
                "quantities": defaultdict(lambda: Decimal("0")),
                "quantity_names": {},
            },
        )
        item["ordered_value"] += row["value"] or Decimal("0.00")
        code = row["material__base_uom__code"]
        item["quantities"][code] += row["quantity"] or Decimal("0")
        item["quantity_names"][code] = row["material__base_uom__name_ar"]
    supplier_received_rows = active_receipt_lines.values(
        "receipt__order__supplier_id"
    ).annotate(value=Sum("line_total"))
    for row in supplier_received_rows:
        item = supplier_map.get(row["receipt__order__supplier_id"])
        if item:
            item["received_value"] += row["value"] or Decimal("0.00")
    by_supplier = [
        {
            "supplier_id": item["supplier_id"],
            "supplier_code": item["supplier_code"],
            "supplier_name": item["supplier_name"],
            "order_count": item["order_count"],
            "ordered_value": money(item["ordered_value"]),
            "received_value": money(item["received_value"]),
            "quantities": [
                {
                    "base_uom": code,
                    "base_uom_name_ar": item["quantity_names"].get(code, code),
                    "quantity_base": quantity(value),
                }
                for code, value in sorted(item["quantities"].items())
            ],
        }
        for item in supplier_map.values()
    ]
    by_supplier.sort(key=lambda row: (-Decimal(row["ordered_value"]), row["supplier_code"]))

    product_rows = list(
        _quantity_rows(
            ordered_lines,
            "quantity_base",
            "line_total",
            (
                "material_id",
                "material__sku",
                "material__name_ar",
                "material__base_uom__code",
                "material__base_uom__name_ar",
            ),
            "order_id",
        )
    )
    received_by_product = {
        row["order_line__material_id"]: row
        for row in active_receipt_lines.values(
            "order_line__material_id"
        ).annotate(
            quantity=Sum("quantity_base"),
            value=Sum("line_total"),
            receipt_count=Count("receipt_id", distinct=True),
        )
    }
    by_product = [
        {
            "product_id": row["material_id"],
            "sku": row["material__sku"],
            "name_ar": row["material__name_ar"],
            "base_uom": row["material__base_uom__code"],
            "base_uom_name_ar": row["material__base_uom__name_ar"],
            "ordered_quantity_base": quantity(row["quantity"]),
            "ordered_value": money(row["value"]),
            "received_quantity_base": quantity(
                received_by_product.get(row["material_id"], {}).get("quantity", Decimal("0"))
            ),
            "received_value": money(
                received_by_product.get(row["material_id"], {}).get("value", Decimal("0"))
            ),
            "order_count": row["document_count"],
        }
        for row in product_rows
    ]
    by_product.sort(key=lambda row: (-Decimal(row["ordered_value"]), row["sku"]))

    receipt_state_counts = {
        "NOT_RECEIVED": annotated_orders.filter(report_received_base=0).count(),
        "PARTIALLY_RECEIVED": annotated_orders.filter(
            report_received_base__gt=0,
            report_received_base__lt=F("report_ordered_base"),
        ).count(),
        "RECEIVED": annotated_orders.filter(
            report_ordered_base__gt=0,
            report_received_base__gte=F("report_ordered_base"),
        ).count(),
    }

    buckets = _bucket_dates(start, end, granularity)
    order_trend = _trend_rows(
        ordered_lines,
        "order__order_date",
        "line_total",
        granularity,
        "order_id",
    )
    receipt_trend = _trend_rows(
        active_receipt_lines,
        "receipt__received_date",
        "line_total",
        granularity,
        "receipt_id",
    )
    recent = annotate_purchase_order_progress(
        PurchaseOrder.objects.filter(order_date__range=(start, end))
        .select_related("supplier")
        .prefetch_related("lines")
        .order_by("-order_date", "-created_at", "-pk")
    )[:recent_limit]

    return {
        "orders_total": orders.count(),
        "non_cancelled_orders_count": non_cancelled.count(),
        "cancelled_orders_count": orders.filter(status=PurchaseOrder.Status.CANCELLED).count(),
        "draft_orders_count": status_counts.get(PurchaseOrder.Status.DRAFT, 0),
        "confirmed_orders_count": status_counts.get(PurchaseOrder.Status.CONFIRMED, 0),
        "orders_by_status": status_counts,
        "status_labels": dict(PurchaseOrder.Status.choices),
        "status_count_rows": [
            {"value": value, "label": label, "count": status_counts.get(value, 0)}
            for value, label in PurchaseOrder.Status.choices
        ],
        "commercial_order_value": money(_sum(ordered_lines, "line_total")),
        "ordered_quantities": ordered_quantities,
        "ordered_line_count": ordered_lines.count(),
        "outstanding_quantities": outstanding_quantities,
        "quantity_summary": quantity_summary,
        "receipt_state_counts": receipt_state_counts,
        "receipt_state_labels": dict(PurchaseOrder.ReceiptState.choices),
        "receipts_count": receipt_counts["total"],
        "active_receipts_count": receipt_counts["active"],
        "partially_reversed_receipts_count": receipt_counts["partially_reversed"],
        "fully_reversed_receipts_count": receipt_counts["fully_reversed"],
        "net_received_value": money(_sum(active_receipt_lines, "line_total")),
        "total_receipt_value": money(_sum(receipt_lines_all, "line_total")),
        "reversed_receipt_value": money(_sum(reversed_receipt_lines, "line_total")),
        "received_quantities": received_quantities,
        "reversed_received_quantities": reversed_quantities,
        "by_supplier": by_supplier[:dimension_limit],
        "suppliers_with_orders_count": len(by_supplier),
        "top_suppliers": by_supplier[:5],
        "by_product": by_product[:dimension_limit],
        "products_with_orders_count": len(by_product),
        "top_products": by_product[:5],
        "value_basis": "Commercial purchase and receipt document snapshots; not accounting expense.",
        "quantity_basis": (
            "Ordered, received, reversed, and outstanding quantities are base-unit quantities "
            "from line snapshots, grouped by base UOM. Different dimensions are never summed."
        ),
        "count_basis": {
            "orders_total": "All purchase order documents in the period, including cancelled orders.",
            "non_cancelled_orders_count": "Purchase orders in the period excluding CANCELLED.",
            "cancelled_orders_count": "Purchase orders in the period with CANCELLED status.",
            "receipts_count": "All receipt documents in the period, including fully reversed receipts.",
            "active_receipts_count": "Receipts with at least one non-reversed line.",
            "partially_reversed_receipts_count": "Receipts with both active and reversed lines.",
            "fully_reversed_receipts_count": "Receipts with at least one reversed line and no active line.",
            "receipt_state_counts": "Receipt state among non-cancelled orders, based on net active received base quantity.",
        },
        "trend": {
            "granularity": granularity,
            "orders": _trend_series(order_trend, buckets),
            "receipts": _trend_series(receipt_trend, buckets),
            "count_basis": {
                "orders": "Distinct non-cancelled purchase orders in each bucket.",
                "receipts": "Distinct receipts with at least one non-reversed line in each bucket.",
            },
            "value_basis": "Commercial line snapshots; cancelled order lines and reversed receipt lines are excluded.",
        },
        "recent_orders": [_purchase_order_row(order) for order in recent],
        "recent_receipts": _receipt_rows(
            PurchaseReceipt.objects.filter(received_date__range=(start, end)).order_by(
                "-received_date", "-created_at", "-pk"
            ),
            recent_limit,
        ),
    }


def current_stock_rows():
    """Return current per-product base balances from the authoritative ledger service."""
    totals = defaultdict(lambda: Decimal("0"))
    for row in ledger_balances():
        totals[row["product_id"]] += row["balance"] or Decimal("0")
    products = (
        Product.objects.filter(pk__in=totals.keys())
        .select_related("category", "base_uom")
        .order_by("sku")
    )
    rows = []
    for product in products:
        balance = totals.get(product.pk, Decimal("0"))
        rows.append({
            "product_id": product.pk,
            "sku": product.sku,
            "name_ar": product.name_ar,
            "category_code": product.category.code,
            "category_name": product.category.name_ar,
            "base_uom": product.base_uom.code,
            "base_uom_name_ar": product.base_uom.name_ar,
            "quantity_base": quantity(balance),
            "low_stock_threshold": quantity(product.low_stock_threshold),
            "below_threshold": bool(
                product.low_stock_threshold > 0 and balance < product.low_stock_threshold
            ),
            "active": product.active,
        })
    return rows


def _category_summary(stock_rows):
    categories = {
        category.code: category
        for category in ProductCategory.objects.filter(code__in=CATEGORY_CODES)
    }
    groups = {
        code: {
            "code": code,
            "name_ar": categories[code].name_ar if code in categories else CATEGORY_LABELS[code],
            "product_count": 0,
            "balances": defaultdict(lambda: Decimal("0")),
            "balance_names": {},
            "balance_product_counts": defaultdict(int),
        }
        for code in CATEGORY_CODES
    }
    for row in stock_rows:
        group = groups.setdefault(
            row["category_code"],
            {
                "code": row["category_code"],
                "name_ar": row["category_name"],
                "product_count": 0,
                "balances": defaultdict(lambda: Decimal("0")),
                "balance_names": {},
                "balance_product_counts": defaultdict(int),
            },
        )
        if Decimal(row["quantity_base"]) == 0:
            continue
        group["product_count"] += 1
        group["balances"][row["base_uom"]] += Decimal(row["quantity_base"])
        group["balance_names"][row["base_uom"]] = row["base_uom_name_ar"]
        group["balance_product_counts"][row["base_uom"]] += 1
    return [
        {
            "code": code,
            "name_ar": groups[code]["name_ar"],
            "product_count": groups[code]["product_count"],
            "balances": [
                {
                    "base_uom": uom,
                    "base_uom_name_ar": groups[code]["balance_names"].get(uom, uom),
                    "quantity_base": quantity(value),
                }
                for uom, value in sorted(groups[code]["balances"].items())
            ],
            "balance_product_counts": [
                {
                    "base_uom": uom,
                    "product_count": groups[code]["balance_product_counts"][uom],
                }
                for uom in sorted(groups[code]["balances"])
            ],
        }
        for code in CATEGORY_CODES
        if code in groups
    ]


def low_stock_report(stock_rows=None):
    stock_rows = stock_rows if stock_rows is not None else current_stock_rows()
    current = {row["product_id"]: Decimal(row["quantity_base"]) for row in stock_rows}
    alerts = []
    products = Product.objects.filter(active=True, low_stock_threshold__gt=0).select_related(
        "base_uom", "category"
    )
    for product in products:
        balance = current.get(product.pk, Decimal("0"))
        if balance <= product.low_stock_threshold:
            shortage = max(product.low_stock_threshold - balance, Decimal("0"))
            alerts.append({
                "product_id": product.pk,
                "sku": product.sku,
                "name_ar": product.name_ar,
                "category": product.category.code,
                "category_code": product.category.code,
                "base_uom": product.base_uom.code,
                "base_uom_name_ar": product.base_uom.name_ar,
                "current_quantity_base": quantity(balance),
                "threshold_quantity_base": quantity(product.low_stock_threshold),
                "shortfall_quantity_base": quantity(shortage),
                "is_out_of_stock": balance <= 0,
                # Backward-compatible names used by the existing owner dashboard.
                "balance_base": quantity(balance),
                "threshold_base": quantity(product.low_stock_threshold),
                "shortage_base": quantity(shortage),
            })
    alerts.sort(key=lambda row: (not row["is_out_of_stock"], -Decimal(row["shortage_base"])))
    return {
        "count": len(alerts),
        "alerts": alerts,
        "configured_thresholds_count": products.count(),
        "unconfigured_active_products_count": Product.objects.filter(
            active=True, low_stock_threshold=0
        ).count(),
        "threshold_source": "catalog.Product.low_stock_threshold",
        "note": "Only active products with a positive configured threshold are alerted.",
        "basis": (
            "Active products with a non-zero low-stock threshold whose current ledger base "
            "balance is at or below that threshold."
        ),
    }


def _movement_type_label(movement_type):
    return dict(StockMovement.Type.choices).get(movement_type, movement_type)


def inventory_report(start, end, granularity, recent_limit=5, dimension_limit=20):
    """Current stock plus movement activity for the selected period.

    Current balances are taken directly from inventory.services.ledger_balances.
    The report does not pretend those current balances are historical as-of
    balances, and it does not value inventory because no accounting valuation
    method is approved.
    """
    stock_rows = current_stock_rows()
    category_rows = _category_summary(stock_rows)
    low_stock = low_stock_report(stock_rows)
    total_by_uom = defaultdict(lambda: Decimal("0"))
    names = {}
    for row in stock_rows:
        total_by_uom[row["base_uom"]] += Decimal(row["quantity_base"])
        names[row["base_uom"]] = row["base_uom_name_ar"]

    movements = StockMovement.objects.filter(occurred_at__date__range=(start, end))
    active_movements = movements.exclude(movement_type=StockMovement.Type.REVERSAL).filter(
        reversal__isnull=True
    )
    active_rows = list(
        active_movements.values(
            "movement_type",
            "product__base_uom__code",
            "product__base_uom__name_ar",
        ).annotate(quantity=Sum("base_quantity"), movement_count=Count("id"))
    )
    active_quantities = [
        {
            "movement_type": row["movement_type"],
            "movement_type_label": _movement_type_label(row["movement_type"]),
            "base_uom": row["product__base_uom__code"],
            "base_uom_name_ar": row["product__base_uom__name_ar"],
            "quantity_base": quantity(row["quantity"]),
            "movement_count": row["movement_count"],
        }
        for row in active_rows
        if row["quantity"] is not None
    ]
    active_quantities.sort(
        key=lambda row: (row["movement_type"], row["base_uom"])
    )

    buckets = _bucket_dates(start, end, granularity)
    movement_trend = _trend_rows(
        active_movements,
        "occurred_at",
        "base_quantity",
        granularity,
        "id",
    )
    recent = list(
        movements.select_related(
            "product", "unit", "source_location", "destination_location", "reversal_of"
        ).order_by("-occurred_at", "-posted_at")[:recent_limit]
    )
    recent_movements = [
        {
            "reference": movement.reference,
            "movement_type": movement.movement_type,
            "movement_type_label": movement.get_movement_type_display(),
            "product_id": movement.product_id,
            "sku": movement.product.sku,
            "product_name": movement.product.name_ar,
            "quantity": quantity(movement.quantity),
            "unit": movement.unit.code,
            "base_uom": movement.product.base_uom.code,
            "base_uom_name_ar": movement.product.base_uom.name_ar,
            "base_quantity": quantity(movement.base_quantity),
            "source_location": str(movement.source_location) if movement.source_location else None,
            "destination_location": (
                str(movement.destination_location) if movement.destination_location else None
            ),
            "occurred_at": movement.occurred_at,
            "is_reversal": movement.movement_type == StockMovement.Type.REVERSAL,
            "reverses_reference": (
                movement.reversal_of.reference if movement.reversal_of_id else None
            ),
        }
        for movement in recent
    ]

    negative_rows = [
        row for row in stock_rows if Decimal(row["quantity_base"]) < 0
    ]
    return {
        "inventory": {
            "categories": category_rows,
            "products_with_balance": sum(1 for row in stock_rows if Decimal(row["quantity_base"]) != 0),
            "total_by_uom": [
                {
                    "base_uom": uom,
                    "base_uom_name_ar": names.get(uom, uom),
                    "quantity_base": quantity(value),
                }
                for uom, value in sorted(total_by_uom.items())
            ],
            "balance_basis": (
                "Current base-unit ledger balance. Balances of different UOM dimensions "
                "are listed separately and are never summed."
            ),
            "valuation_available": False,
            "historical_balance_available": False,
        },
        "low_stock": low_stock,
        "movement_summary": {
            "active_movement_count": active_movements.count(),
            "active_quantities": active_quantities,
            "reversals_in_period_count": movements.filter(
                movement_type=StockMovement.Type.REVERSAL
            ).count(),
            "original_movements_with_reversal_count": movements.exclude(
                movement_type=StockMovement.Type.REVERSAL
            ).filter(reversal__isnull=False).count(),
            "trend": {
                "granularity": granularity,
                "movements": _trend_series(movement_trend, buckets),
                "value_basis": "Base quantity sum of active movements; not a monetary value.",
            },
            "basis": (
                "Active movement totals exclude reversal entries and original movements "
                "that have been reversed. Reversal entries and reversed originals are "
                "reported separately to avoid double counting."
            ),
        },
        "recent_movements": recent_movements,
        "reconciliation": {
            "source": "inventory.services.ledger_balances",
            "product_balance_count": len(stock_rows),
            "negative_balance_count": len(negative_rows),
            "negative_balances": negative_rows,
            "historical_balance_available": False,
            "historical_balance_note": (
                "Current balances only. Historical as-of-date stock reconstruction is not "
                "exposed by this report; do not treat current stock as historical stock."
            ),
            "valuation_available": False,
            "valuation_note": (
                "No approved inventory valuation/accounting method exists, so no stock "
                "value or accounting profit is reported."
            ),
            "blocked_operation_audit_available": False,
            "blocked_operation_audit_note": (
                "Negative balances are visible from the ledger, but there is no separate "
                "blocked-operation audit record in the persisted models."
            ),
        },
    }


def _production_order_row(order):
    return {
        "order_number": order.order_number,
        "product_id": order.product_id,
        "sku": order.product.sku,
        "product_name": order.product.name_ar,
        "production_line": order.production_line.code,
        "production_line_name": order.production_line.name_ar,
        "status": order.status,
        "status_display": order.get_status_display(),
        "planned_quantity": quantity(order.planned_quantity),
        "planned_unit": order.planned_unit.code,
        "planned_quantity_base": quantity(order.planned_quantity_base),
        "base_uom": order.product.base_uom.code,
        "base_uom_name_ar": order.product.base_uom.name_ar,
        "created_at": order.created_at,
        "released_at": order.released_at,
        "completed_at": order.completed_at,
    }


def _production_result_row(result):
    total = result.good_quantity_base + result.scrap_quantity_base
    return {
        "order_number": result.order.order_number,
        "product_id": result.order.product_id,
        "sku": result.order.product.sku,
        "product_name": result.order.product.name_ar,
        "production_line": result.order.production_line.code,
        "good_quantity": quantity(result.good_quantity),
        "scrap_quantity": quantity(result.scrap_quantity),
        "input_unit": result.unit.code,
        "good_quantity_base": quantity(result.good_quantity_base),
        "scrap_quantity_base": quantity(result.scrap_quantity_base),
        "base_uom": result.order.product.base_uom.code,
        "base_uom_name_ar": result.order.product.base_uom.name_ar,
        "yield_percent": percent((result.good_quantity_base * 100 / total) if total else None),
        "occurred_at": result.occurred_at,
    }


def production_report(
    start, end, granularity, recent_limit=5, dimension_limit=20, line_code=None
):
    """Production status, planned quantities, consumption, output, and yield.

    Planned quantities use order creation dates. Actual material consumption uses
    issue movement dates. Output uses production-result dates. Those bases are
    deliberately reported separately rather than silently mixing periods. An
    optional production-line code filters only production records; shared sales,
    purchasing, and inventory ledger entries are never duplicated by line.
    """
    order_filter = {"production_line__code": line_code} if line_code else {}
    result_filter = {"order__production_line__code": line_code} if line_code else {}
    issue_filter = {"order__production_line__code": line_code} if line_code else {}
    orders_in_period = ProductionOrder.objects.filter(
        created_at__date__range=(start, end), **order_filter
    )
    status_rows = list(
        orders_in_period.values(
            "production_line_id",
            "production_line__code",
            "production_line__name_ar",
            "status",
        ).annotate(count=Count("id"))
    )
    line_status = defaultdict(lambda: defaultdict(int))
    for row in status_rows:
        line_status[row["production_line_id"]][row["status"]] = row["count"]

    planned_rows = list(
        orders_in_period.exclude(status=ProductionOrder.Status.CANCELLED)
        .values(
            "production_line_id",
            "product__base_uom__code",
            "product__base_uom__name_ar",
        )
        .annotate(
            planned_quantity=Sum("planned_quantity_base"),
            order_count=Count("id", distinct=True),
        )
    )
    line_planned = defaultdict(list)
    for row in planned_rows:
        line_planned[row["production_line_id"]].append({
            "base_uom": row["product__base_uom__code"],
            "base_uom_name_ar": row["product__base_uom__name_ar"],
            "planned_quantity_base": quantity(row["planned_quantity"]),
            "order_count": row["order_count"],
        })

    # All results in the period (for output, consumption, yield calculations)
    all_results_in_period = ProductionResult.objects.filter(
        occurred_at__date__range=(start, end), correction__isnull=True, **result_filter
    ).select_related("order", "order__production_line", "order__product", "unit")
    output_rows = list(
        all_results_in_period.values(
            "order__production_line_id",
            "order__production_line__code",
            "order__production_line__name_ar",
            "order__product_id",
            "order__product__sku",
            "order__product__name_ar",
            "order__product__base_uom__code",
            "order__product__base_uom__name_ar",
        ).annotate(
            good=Sum("good_quantity_base"),
            scrap=Sum("scrap_quantity_base"),
            result_count=Count("id"),
            order_count=Count("order_id", distinct=True),
        )
    )
    line_output_names = defaultdict(dict)
    line_output_by_product = defaultdict(list)
    total_output = defaultdict(lambda: Decimal("0"))
    total_good = defaultdict(lambda: Decimal("0"))
    total_scrap = defaultdict(lambda: Decimal("0"))
    total_output_names = {}
    yield_rows = []
    for row in output_rows:
        line_id = row["order__production_line_id"]
        code = row["order__product__base_uom__code"]
        good = row["good"] or Decimal("0")
        scrap = row["scrap"] or Decimal("0")
        line_output_names[line_id][code] = row["order__product__base_uom__name_ar"]
        total_output[code] += good + scrap
        total_good[code] += good
        total_scrap[code] += scrap
        total_output_names[code] = row["order__product__base_uom__name_ar"]
        total = good + scrap
        line_output_by_product[line_id].append({
            "product_id": row["order__product_id"],
            "sku": row["order__product__sku"],
            "product_name": row["order__product__name_ar"],
            "base_uom": code,
            "base_uom_name_ar": row["order__product__base_uom__name_ar"],
            "good_quantity_base": quantity(good),
            "scrap_quantity_base": quantity(scrap),
            "yield_percent": percent((good * 100 / total) if total else None),
            "result_count": row["result_count"],
            "order_count": row["order_count"],
        })
        if total:
            yield_rows.append({
                "production_line": row["order__production_line__code"],
                "production_line_name": row["order__production_line__name_ar"],
                "product_id": row["order__product_id"],
                "sku": row["order__product__sku"],
                "product_name": row["order__product__name_ar"],
                "base_uom": code,
                "base_uom_name_ar": row["order__product__base_uom__name_ar"],
                "good_quantity_base": quantity(good),
                "scrap_quantity_base": quantity(scrap),
                "yield_percent": percent(good * 100 / total),
            })

    # Aggregate line output with separate good/scrap semantics from the source rows.
    line_output = defaultdict(lambda: defaultdict(lambda: {"good": Decimal("0"), "scrap": Decimal("0")}))
    for row in output_rows:
        line_id = row["order__production_line_id"]
        code = row["order__product__base_uom__code"]
        line_output[line_id][code]["good"] += row["good"] or Decimal("0")
        line_output[line_id][code]["scrap"] += row["scrap"] or Decimal("0")
    line_good_totals = defaultdict(lambda: Decimal("0"))
    line_scrap_totals = defaultdict(lambda: Decimal("0"))
    for values_by_uom in line_output.values():
        for code, values in values_by_uom.items():
            line_good_totals[code] += values["good"]
            line_scrap_totals[code] += values["scrap"]

    consumption_rows = list(
        MaterialIssue.objects.filter(
            movement__occurred_at__date__range=(start, end),
            movement__reversal__isnull=True,
            **issue_filter,
        )
        .values(
            "order__production_line_id",
            "requirement__material_id",
            "requirement__material__sku",
            "requirement__material__name_ar",
            "requirement__material__base_uom__code",
            "requirement__material__base_uom__name_ar",
        )
        .annotate(
            quantity=Sum("quantity_base"),
            issue_count=Count("id"),
            order_count=Count("order_id", distinct=True),
        )
    )
    line_consumption = defaultdict(list)
    total_consumption = defaultdict(lambda: Decimal("0"))
    total_consumption_names = {}
    for row in consumption_rows:
        line_id = row["order__production_line_id"]
        line_consumption[line_id].append({
            "product_id": row["requirement__material_id"],
            "sku": row["requirement__material__sku"],
            "product_name": row["requirement__material__name_ar"],
            "base_uom": row["requirement__material__base_uom__code"],
            "base_uom_name_ar": row["requirement__material__base_uom__name_ar"],
            "quantity_base": quantity(row["quantity"]),
            "issue_count": row["issue_count"],
            "order_count": row["order_count"],
        })
        code = row["requirement__material__base_uom__code"]
        total_consumption[code] += row["quantity"] or Decimal("0")
        total_consumption_names[code] = row["requirement__material__base_uom__name_ar"]

    production_lines = ProductionLine.objects.order_by("code")
    if line_code:
        production_lines = production_lines.filter(code=line_code)
    lines = []
    for line in production_lines:
        lines.append({
            "code": line.code,
            "name_ar": line.name_ar,
            "active": line.active,
            "total_orders": sum(line_status[line.pk].values()),
            "status_counts": dict(line_status[line.pk]),
            "output": [
                {
                    "base_uom": code,
                    "base_uom_name_ar": line_output_names[line.pk].get(code, code),
                    "good_quantity_base": quantity(values["good"]),
                    "scrap_quantity_base": quantity(values["scrap"]),
                }
                for code, values in sorted(line_output[line.pk].items())
            ],
            "planned_quantities": line_planned.get(line.pk, []),
            "consumption": line_consumption.get(line.pk, []),
            "output_by_product": line_output_by_product.get(line.pk, []),
        })

    open_orders = ProductionOrder.objects.filter(
        status__in=(
            ProductionOrder.Status.DRAFT,
            ProductionOrder.Status.RELEASED,
            ProductionOrder.Status.IN_PROGRESS,
        ),
        **order_filter,
    )
    orders_without_results = (
        orders_in_period.exclude(status=ProductionOrder.Status.CANCELLED)
        .annotate(_has_result=Exists(ProductionResult.objects.filter(order=OuterRef("pk"), correction__isnull=True)))
        .filter(_has_result=False)
        .count()
    )

    # Cross-period results: results in period for orders created before this
    # period, matching the documented cross_period_basis. The base queryset
    # already carries select_related, so the trend and recent-result rows below
    # reuse it without rebuilding the query.
    cross_period_results = all_results_in_period.filter(order__created_at__date__lt=start)
    cross_period_output_count = cross_period_results.count()
    buckets = _bucket_dates(start, end, granularity)
    output_trend = _trend_rows(
        all_results_in_period,
        "occurred_at",
        "good_quantity_base",
        granularity,
        "order_id",
    )
    recent_results = all_results_in_period.order_by("-occurred_at", "-pk")[:recent_limit]
    recent_orders = (
        orders_in_period.select_related("product", "product__base_uom", "production_line")
        .order_by("-created_at", "-pk")[:recent_limit]
    )

    return {
        "line_filter": {
            "production_line": line_code,
            "is_filtered": bool(line_code),
            "basis": (
                "Optional production-line filter. Only production orders, results, and "
                "material issues are filtered; shared inventory, sales, and purchasing "
                "entries have no duplicated per-line ledger."
            ),
        },
        "lines": lines,
        "status_labels": dict(ProductionOrder.Status.choices),
        "orders_total": orders_in_period.count(),
        "non_cancelled_orders_count": orders_in_period.exclude(
            status=ProductionOrder.Status.CANCELLED
        ).count(),
        "cancelled_orders_count": orders_in_period.filter(
            status=ProductionOrder.Status.CANCELLED
        ).count(),
        "open_orders_count": open_orders.count(),
        "orders_without_results_count": orders_without_results,
        "cross_period_output_count": cross_period_output_count,
        "total_output_by_uom": [
            {
                "base_uom": code,
                "base_uom_name_ar": total_output_names.get(code, code),
                "quantity_base": quantity(value),
            }
            for code, value in sorted(total_output.items())
        ],
        "total_consumption_by_uom": [
            {
                "base_uom": code,
                "base_uom_name_ar": total_consumption_names.get(code, code),
                "quantity_base": quantity(value),
            }
            for code, value in sorted(total_consumption.items())
        ],
        "reconciliation": {
            "output_by_uom": [
                {
                    "base_uom": code,
                    "base_uom_name_ar": total_output_names.get(code, code),
                    "good_quantity_base": quantity(total_good.get(code, Decimal("0"))),
                    "scrap_quantity_base": quantity(total_scrap.get(code, Decimal("0"))),
                    "total_quantity_base": quantity(total_output.get(code, Decimal("0"))),
                    "attributed_to_lines_base": quantity(
                        line_good_totals.get(code, Decimal("0"))
                        + line_scrap_totals.get(code, Decimal("0"))
                    ),
                    "unallocated_base": quantity(
                        max(
                            total_output.get(code, Decimal("0"))
                            - line_good_totals.get(code, Decimal("0"))
                            - line_scrap_totals.get(code, Decimal("0")),
                            Decimal("0"),
                        )
                    ),
                }
                for code in sorted(total_output)
            ],
            "all_output_attributed_to_line": all(
                total_output.get(code, Decimal("0"))
                == line_good_totals.get(code, Decimal("0"))
                + line_scrap_totals.get(code, Decimal("0"))
                for code in total_output
            ),
            "unallocated_output_note": (
                "Production orders require a production line, so no output is expected to "
                "be unallocated. The check is computed from the displayed rows rather than "
                "assumed."
            ),
            "shared_inventory_note": (
                "Inventory, sales, and purchasing are shared ledgers without a per-line "
                "duplicate. They are not filtered or allocated to production lines by this "
                "report."
            ),
        },
        "yield_rows": sorted(yield_rows, key=lambda row: (row["production_line"], row["sku"])),
        "yield_formula": "good output / (good output + scrap output) for the same product and base UOM.",
        "status_counts_basis": "ProductionOrder.created_at date inside the selected period.",
        "planned_basis": (
            "Planned base quantities for non-cancelled orders created in the selected period. "
            "This is a plan snapshot, not actual output."
        ),
        "output_basis": (
            "Actual production-result quantity by ProductionResult.occurred_at date. Quantities "
            "are base UOM for each product and are never mixed across dimensions."
        ),
        "consumption_basis": (
            "Active material issues by inventory movement occurred_at date; original issues "
            "that were reversed are excluded and reversal entries are not counted again."
        ),
        "yield_basis": (
            "Output yield only. It is not material yield because material inputs use different "
            "UOM dimensions from finished products."
        ),
        "cross_period_basis": (
            "Count of results inside the period whose production order was created before the "
            "period start. Planned and actual output date bases are therefore not directly "
            "comparable without this warning."
        ),
        "incomplete_basis": (
            "Orders without any production result in the selected creation-date period and all "
            "currently open DRAFT/RELEASED/IN_PROGRESS orders."
        ),
        "trend": {
            "granularity": granularity,
            "good_output": _trend_series(output_trend, buckets),
            "value_basis": "Base quantity of good output, not monetary value.",
        },
        "recent_orders": [_production_order_row(order) for order in recent_orders],
        "recent_results": [_production_result_row(result) for result in recent_results],
    }


def _month_keys(start, end):
    keys = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        keys.append((year, month))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return keys


def _costing_periods_for_range(start, end):
    query = Q()
    for year, month in _month_keys(start, end):
        query |= Q(year=year, month=month)
    if not query:
        return CostingPeriod.objects.none()
    return CostingPeriod.objects.filter(query).order_by("-year", "-month")


def _missing_notes_list(notes):
    rows = [line.strip() for line in (notes or "").splitlines() if line.strip()]
    return rows or ([(notes or "").strip()] if (notes or "").strip() else [])


def _costing_period_row(period):
    entries = period.product_entries.all()
    stored = entries.aggregate(
        actual_material=Sum("actual_material_cost"),
        assigned=Sum("material_cost_assigned_to_good"),
        waste=Sum("waste_material_cost"),
        allocated_labor=Sum("allocated_labor_cost"),
        allocated_electricity=Sum("allocated_electricity_cost"),
        allocated_water=Sum("allocated_water_cost"),
        allocated_other=Sum("allocated_other_cost"),
        entry_total=Sum("total_production_cost"),
        entry_count=Count("id"),
        incomplete_entries=Count("id", filter=Q(calculation_status="INCOMPLETE")),
    )
    actual_material = _decimal(stored["actual_material"])
    assigned = _decimal(stored["assigned"])
    waste = _decimal(stored["waste"])
    allocated_labor = _decimal(stored["allocated_labor"])
    allocated_electricity = _decimal(stored["allocated_electricity"])
    allocated_water = _decimal(stored["allocated_water"])
    allocated_other = _decimal(stored["allocated_other"])
    entry_total = _decimal(stored["entry_total"])
    overhead_total = (
        allocated_labor + allocated_electricity + allocated_water + allocated_other
    )
    allocated_total = assigned + overhead_total
    incomplete_entries = stored["incomplete_entries"] or 0
    pending_value = _sum(
        entries.filter(calculation_status="INCOMPLETE"), "total_production_cost"
    )
    good_by_uom = [
        {
            "base_uom": row["product__base_uom__code"],
            "base_uom_name_ar": row["product__base_uom__name_ar"],
            "good_units_produced": quantity(row["good"]),
            "scrap_units_produced": quantity(row["scrap"]),
        }
        for row in entries.values(
            "product__base_uom__code", "product__base_uom__name_ar"
        ).annotate(good=Sum("good_units_produced"), scrap=Sum("scrap_units_produced"))
    ]
    good_by_uom.sort(key=lambda row: row["base_uom"])
    missing_expenses = []
    if not period.labor_recorded:
        missing_expenses.append("LABOR")
    if not getattr(period, "electricity_recorded", False):
        missing_expenses.append("ELECTRICITY")
    if not getattr(period, "water_recorded", False):
        missing_expenses.append("WATER")
    review_required = bool(
        (not period.is_complete) or incomplete_entries or missing_expenses
    )
    return {
        "period_id": period.pk,
        "year": period.year,
        "month": period.month,
        "period_start": period.start_date,
        "period_end": period.end_date,
        "status": period.status,
        "status_label": period.get_status_display(),
        "is_finalized": period.status == CostingPeriod.Status.FINALIZED,
        "is_complete": period.is_complete,
        "calculation_status": "COMPLETE" if period.is_complete else "INCOMPLETE",
        "missing_data_notes": period.missing_data_notes,
        "missing_data_notes_list": _missing_notes_list(period.missing_data_notes),
        "labor_recorded": period.labor_recorded,
        "electricity_recorded": getattr(period, "electricity_recorded", False),
        "water_recorded": getattr(period, "water_recorded", False),
        "labor_completeness": "RECORDED" if period.labor_recorded else "MISSING",
        "electricity_completeness": (
            "RECORDED" if getattr(period, "electricity_recorded", False) else "MISSING"
        ),
        "water_completeness": (
            "RECORDED" if getattr(period, "water_recorded", False) else "MISSING"
        ),
        "missing_required_expenses": missing_expenses,
        "missing_expense_basis": (
            "Recorded at the last costing calculation. A recorded 0.00 is complete; "
            "an absent expense record is missing."
        ),
        "allocation_policy": period.allocation_policy,
        "allocation_policy_label": period.get_allocation_policy_display(),
        "waste_policy": period.waste_policy,
        "waste_policy_label": period.get_waste_policy_display(),
        "material_valuation_method": period.material_valuation_method,
        "labor_total": money(period.labor_total),
        "electricity_total": money(period.electricity_total),
        "water_total": money(period.water_total),
        "other_industrial_total": money(period.other_industrial_total),
        "actual_material_cost": money(period.materials_total),
        "stored_actual_material_cost": money(period.materials_total),
        "material_cost_assigned_to_good": money(assigned),
        "waste_material_cost": money(waste),
        "waste_cost_basis": "ORDER_LEVEL_ALLOCATION_ESTIMATE",
        "waste_cost_valuation_status": (
            "UNAVAILABLE_MATERIAL_ATTRIBUTION" if waste > 0 else "NO_RECORDED_WASTE"
        ),
        "allocated_labor_cost": money(allocated_labor),
        "allocated_electricity_cost": money(allocated_electricity),
        "allocated_water_cost": money(allocated_water),
        "allocated_other_cost": money(allocated_other),
        "overhead_allocation_total": money(overhead_total),
        "materials_total": money(period.materials_total),
        "total_production_cost": money(period.total_production_cost),
        "entry_total_production_cost": money(entry_total),
        "entry_count": stored["entry_count"] or 0,
        "incomplete_entries_count": incomplete_entries,
        "entries_pending_review_value": money(pending_value),
        "good_quantities_by_uom": good_by_uom,
        "review_required": review_required,
        "finalization_blocked": not period.is_complete,
        "calculated_at": period.calculated_at,
        "finalized_at": period.finalized_at,
        "reconciliation": {
            "actual_material_cost": money(period.materials_total),
            "material_cost_assigned_to_good": money(assigned),
            "waste_material_cost": money(waste),
            "materials_reconcile": (
                assigned + waste == period.materials_total
                and actual_material == period.materials_total
            ),
            "labor_total": money(period.labor_total),
            "electricity_total": money(period.electricity_total),
            "water_total": money(period.water_total),
            "other_industrial_total": money(period.other_industrial_total),
            "total_production_cost": money(period.total_production_cost),
            "entry_total_production_cost": money(entry_total),
            "cost_total_reconciles": allocated_total == period.total_production_cost,
            "entry_total_reconciles": entry_total == period.total_production_cost,
            "allocations_reconcile": allocated_total == entry_total,
            "formula": (
                "material_cost_assigned_to_good + labor_total + electricity_total + "
                "water_total + other_industrial_total = total_production_cost"
            ),
            "source": "Sum of persisted ProductCostingEntry rows and persisted CostingPeriod totals.",
            "entry_count": stored["entry_count"] or 0,
            "incomplete_entry_count": incomplete_entries,
        },
        "fallback_allocation_amount_available": False,
        "fallback_allocation_note": (
            "Fallback/equal allocations are flagged through calculation_status=INCOMPLETE "
            "and the persisted missing-data notes. The costing service does not persist a "
            "separate quantified fallback amount, so no such amount is invented here. "
            "entries_pending_review_value is the total cost of incomplete entries, not the "
            "fallback portion alone."
        ),
    }


def costing_report(start, end, recent_limit=5, dimension_limit=20):
    """Read persisted monthly costing outputs and their reconciliation fields.

    No product cost, allocation, or expense amount is recalculated here. Only
    arithmetic reconciliation of already persisted totals is performed.
    """
    periods = list(_costing_periods_for_range(start, end))
    period_rows = [_costing_period_row(period) for period in periods]
    recent_periods = list(CostingPeriod.objects.order_by("-year", "-month")[:6])
    recent_period_rows = [_costing_period_row(period) for period in recent_periods]
    latest_entries = []
    latest_order_entries = []
    if periods:
        latest = periods[0]
        for entry in ProductCostingEntry.objects.filter(period=latest).select_related(
            "product", "product__base_uom", "production_line"
        ).order_by("product__sku"):
            latest_entries.append({
                "product_sku": entry.product.sku,
                "product_name_ar": entry.product.name_ar,
                "base_uom": entry.product.base_uom.code,
                "base_uom_name_ar": entry.product.base_uom.name_ar,
                "production_line": (
                    entry.production_line.code if entry.production_line else None
                ),
                "good_units_produced": quantity(entry.good_units_produced),
                "scrap_units_produced": quantity(entry.scrap_units_produced),
                "actual_material_cost": money(entry.actual_material_cost),
                "material_cost_assigned_to_good": money(
                    entry.material_cost_assigned_to_good
                ),
                "waste_material_cost": money(entry.waste_material_cost),
                "waste_cost_basis": "ORDER_LEVEL_ALLOCATION_ESTIMATE",
                "waste_cost_valuation_status": (
                    "UNAVAILABLE_MATERIAL_ATTRIBUTION"
                    if entry.scrap_units_produced > 0
                    else "NO_RECORDED_WASTE"
                ),
                "allocated_labor_cost": money(entry.allocated_labor_cost),
                "allocated_electricity_cost": money(entry.allocated_electricity_cost),
                "allocated_water_cost": money(entry.allocated_water_cost),
                "allocated_other_cost": money(entry.allocated_other_cost),
                "total_production_cost": money(entry.total_production_cost),
                "cost_per_good_unit": format(
                    _decimal(entry.cost_per_good_unit).quantize(
                        Decimal("0.0001"), rounding=ROUND_HALF_UP
                    ),
                    "f",
                ) if entry.cost_per_good_unit is not None else None,
                "selling_price_snapshot": (
                    format(
                        _decimal(entry.selling_price_snapshot).quantize(
                            Decimal("0.0001"), rounding=ROUND_HALF_UP
                        ),
                        "f",
                    )
                    if entry.selling_price_snapshot is not None
                    else None
                ),
                "calculation_status": entry.calculation_status,
                "notes": entry.notes,
                "material_breakdown": entry.material_breakdown,
            })
        for entry in ProductionOrderCostingEntry.objects.filter(period=latest).select_related(
            "order", "product", "production_line"
        ).order_by("order__order_number")[:dimension_limit]:
            latest_order_entries.append({
                "order_number": entry.order.order_number,
                "product_sku": entry.product.sku,
                "production_line": (
                    entry.production_line.code if entry.production_line else None
                ),
                "good_units_produced": quantity(entry.good_units_produced),
                "scrap_units_produced": quantity(entry.scrap_units_produced),
                "actual_material_cost": money(entry.actual_material_cost),
                "material_cost_assigned_to_good": money(
                    entry.material_cost_assigned_to_good
                ),
                "waste_material_cost": money(entry.waste_material_cost),
                "waste_cost_basis": "ORDER_LEVEL_ALLOCATION_ESTIMATE",
                "waste_cost_valuation_status": (
                    "UNAVAILABLE_MATERIAL_ATTRIBUTION"
                    if entry.scrap_units_produced > 0
                    else "NO_RECORDED_WASTE"
                ),
                "total_production_cost": money(entry.total_production_cost),
                "cost_per_good_unit": (
                    format(
                        _decimal(entry.cost_per_good_unit).quantize(
                            Decimal("0.0001"), rounding=ROUND_HALF_UP
                        ),
                        "f",
                    )
                    if entry.cost_per_good_unit is not None
                    else None
                ),
                "calculation_status": entry.calculation_status,
            })
    return {
        "periods": period_rows[:dimension_limit],
        "periods_count": len(period_rows),
        "recent_periods": recent_period_rows,
        "latest_period_entries": latest_entries,
        "latest_order_entries": latest_order_entries,
        "latest_order_entries_limit": dimension_limit,
        "source": "Persisted monthly costing outputs; the report never recalculates product cost.",
        "review_summary": {
            "periods_in_range": len(period_rows),
            "incomplete_periods": sum(1 for row in period_rows if not row["is_complete"]),
            "review_required_periods": sum(
                1 for row in period_rows if row["review_required"]
            ),
            "missing_labor_periods": sum(
                1 for row in period_rows if "LABOR" in row["missing_required_expenses"]
            ),
            "missing_electricity_periods": sum(
                1
                for row in period_rows
                if "ELECTRICITY" in row["missing_required_expenses"]
            ),
            "missing_water_periods": sum(
                1 for row in period_rows if "WATER" in row["missing_required_expenses"]
            ),
            "incomplete_entries": sum(
                row["incomplete_entries_count"] for row in period_rows
            ),
            "fallback_amount_available": False,
        },
        "allocation_review": {
            "expense_allocation": (
                "Labor, electricity, water, and other industrial costs are allocated by the "
                "monthly costing service and read from persisted period entries."
            ),
            "independent_recalculation": False,
            "accounting_final": False,
            "fallback_amount_available": False,
            "reconciliation_basis": (
                "Stored period totals and product-entry allocations only; arithmetic checks "
                "do not recalculate unit costs, expenses, or allocation drivers."
            ),
            "note": (
                "Costing remains provisional until the owner/accountant approves the "
                "valuation and treatment."
            ),
        },
    }


def _profit_notes(notes):
    return [line.strip() for line in (notes or "").splitlines() if line.strip()]


def _profit_period_row(period):
    cogs_lines = []
    for line in period.cogs_lines.select_related(
        "product", "source_period", "source_costing_entry"
    ).order_by("product__sku"):
        cogs_lines.append(
            {
                "id": line.pk,
                "product_id": line.product_id,
                "product_sku": line.product.sku,
                "product_name_ar": line.product.name_ar,
                "base_uom_code": line.base_uom_code,
                "base_uom_name_ar": line.base_uom_name_ar,
                "quantity_sold_base": quantity(line.quantity_sold_base),
                "revenue": money(line.revenue),
                "cost_per_base_unit": (
                    format(
                        _decimal(line.cost_per_base_unit).quantize(
                            Decimal("0.000001"), rounding=ROUND_HALF_UP
                        ),
                        "f",
                    )
                    if line.cost_per_base_unit is not None
                    else None
                ),
                "cogs": money(line.cogs),
                "shipment_count": line.shipment_count,
                "source_period_id": line.source_period_id,
                "source_period": (
                    f"{line.source_period.year}/{line.source_period.month:02d}"
                    if line.source_period
                    else None
                ),
                "source_costing_entry_id": line.source_costing_entry_id,
                "calculation_status": line.calculation_status,
                "calculation_status_label": line.get_calculation_status_display(),
                "shipment_references": line.shipment_references or [],
                "notes": line.notes,
            }
        )

    expense_records = []
    for expense in MonthlyExpense.objects.filter(
        year=period.year, month=period.month
    ).select_related("production_line", "product", "created_by", "reversed_by").order_by(
        "expense_type", "expense_date", "pk"
    ):
        expense_records.append(
            {
                "id": expense.pk,
                "expense_type": expense.expense_type,
                "expense_type_label": expense.get_expense_type_display(),
                "amount": money(expense.amount),
                "status": expense.status,
                "status_label": expense.get_status_display(),
                "expense_date": expense.expense_date,
                "payment_date": expense.payment_date,
                "description": expense.description,
                "reference": expense.reference,
                "scope": expense.scope,
                "production_line": (
                    expense.production_line.code if expense.production_line else None
                ),
                "product_sku": expense.product.sku if expense.product else None,
                "reversal_reason": expense.reversal_reason,
            }
        )

    return {
        "id": period.pk,
        "year": period.year,
        "month": period.month,
        "period_label": f"{period.year}/{period.month:02d}",
        "start_date": period.start_date,
        "end_date": period.end_date,
        "status": period.status,
        "status_label": period.get_status_display(),
        "is_finalized": period.status == MonthlyProfitPeriod.Status.FINALIZED,
        "is_complete": period.is_complete,
        "recovery_state": period.recovery_state,
        "costing_period_id": period.costing_period_id,
        "revenue": money(period.revenue),
        "cogs": money(period.cogs),
        "gross_profit": money(period.gross_profit),
        "labor_expense": money(period.labor_expense),
        "electricity_expense": money(period.electricity_expense),
        "maintenance_expense": money(period.maintenance_expense),
        "other_expense": money(period.other_expense),
        "operating_expenses_total": money(period.operating_expenses_total),
        "separated_scrap_loss": money(period.separated_scrap_loss),
        "absorbed_scrap_cost": money(period.absorbed_scrap_cost),
        "unreconciled_scrap_cost": money(period.unreconciled_scrap_cost),
        "scrap_treatment": period.scrap_treatment,
        "scrap_treatment_label": period.get_scrap_treatment_display(),
        "has_scrap_deduction": _decimal(period.separated_scrap_loss) > 0,
        "monthly_result": money(period.monthly_result),
        "opening_accumulated_loss": money(period.opening_accumulated_loss),
        "loss_recovered": money(period.loss_recovered),
        "closing_accumulated_loss": money(period.closing_accumulated_loss),
        "profit_after_loss_recovery": money(period.profit_after_loss_recovery),
        "is_loss_month": _decimal(period.monthly_result) < 0,
        "labor_recorded": period.labor_recorded,
        "electricity_recorded": period.electricity_recorded,
        "maintenance_recorded": period.maintenance_recorded,
        "other_recorded": period.other_recorded,
        "cogs_complete": period.cogs_complete,
        "missing_data_notes": period.missing_data_notes,
        "missing_data_notes_list": _profit_notes(period.missing_data_notes),
        "active_shipment_count": period.active_shipment_count,
        "active_shipment_line_count": period.active_shipment_line_count,
        "revenue_product_count": period.revenue_product_count,
        "expense_record_count": period.expense_record_count,
        "calculated_at": period.calculated_at,
        "calculated_by": getattr(period.calculated_by, "username", None),
        "finalized_at": period.finalized_at,
        "finalized_by": getattr(period.finalized_by, "username", None),
        "reopened_at": period.reopened_at,
        "reopened_by": getattr(period.reopened_by, "username", None),
        "reopen_reason": period.reopen_reason,
        "cogs_lines": cogs_lines,
        "expense_records": expense_records,
        "formula": (
            "gross_profit = revenue - cogs; monthly_result = gross_profit - "
            "(labor + electricity + maintenance + other operating expenses) - "
            "separated_scrap_loss (deducted only under the separate-scrap policy "
            "for completed, fully-costed orders); "
            "loss_recovered = min(max(monthly_result, 0), opening_accumulated_loss); "
            "closing_accumulated_loss = opening_accumulated_loss - loss_recovered + "
            "max(-monthly_result, 0)"
        ),
        "result_label": "نتيجة الشهر قبل تغطية الخسائر",
        "remaining_profit_label": "الربح المتبقي بعد تغطية الخسائر",
        "is_final_net_profit": False,
    }


def _profit_periods_for_range(start, end):
    query = Q()
    for year, month in _month_keys(start, end):
        query |= Q(year=year, month=month)
    if not query:
        return MonthlyProfitPeriod.objects.none()
    return (
        MonthlyProfitPeriod.objects.filter(query)
        .select_related(
            "costing_period", "calculated_by", "finalized_by", "reopened_by"
        )
        .prefetch_related("cogs_lines")
        .order_by("-year", "-month")
    )


def profit_report(start, end, dimension_limit=25):
    """Read persisted monthly management profit and loss carry-forward.

    No expense or product cost is recalculated. The report only formats persisted
    monthly results and their COGS/expense detail, then performs display-level
    checks for missing data.
    """
    periods = list(_profit_periods_for_range(start, end))
    rows = [_profit_period_row(period) for period in periods[:dimension_limit]]
    months = _month_keys(start, end)
    present = {(period.year, period.month) for period in periods}
    uncalculated_months = [
        {"year": year, "month": month, "period_label": f"{year}/{month:02d}"}
        for year, month in months
        if (year, month) not in present
    ]

    range_totals = {
        "revenue": money(sum(_decimal(period.revenue) for period in periods)),
        "cogs": money(sum(_decimal(period.cogs) for period in periods)),
        "gross_profit": money(sum(_decimal(period.gross_profit) for period in periods)),
        "operating_expenses_total": money(
            sum(_decimal(period.operating_expenses_total) for period in periods)
        ),
        "separated_scrap_loss": money(
            sum(_decimal(period.separated_scrap_loss) for period in periods)
        ),
        "absorbed_scrap_cost": money(
            sum(_decimal(period.absorbed_scrap_cost) for period in periods)
        ),
        "unreconciled_scrap_cost": money(
            sum(_decimal(period.unreconciled_scrap_cost) for period in periods)
        ),
        "monthly_result": money(sum(_decimal(period.monthly_result) for period in periods)),
        "loss_recovered": money(
            sum(_decimal(period.loss_recovered) for period in periods)
        ),
        "latest_closing_accumulated_loss": (
            money(periods[0].closing_accumulated_loss) if periods else None
        ),
        "latest_profit_after_loss_recovery": (
            money(periods[0].profit_after_loss_recovery) if periods else None
        ),
    }

    latest_period = rows[0] if rows else None
    return {
        "periods": rows,
        "periods_count": len(periods),
        "displayed_periods_limit": dimension_limit,
        "latest_period": latest_period,
        "uncalculated_months": uncalculated_months,
        "uncalculated_months_count": len(uncalculated_months),
        "range_totals": range_totals,
        "review_summary": {
            "periods_in_range": len(periods),
            "finalized_periods": sum(
                1 for period in periods if period.status == MonthlyProfitPeriod.Status.FINALIZED
            ),
            "draft_periods": sum(
                1 for period in periods if period.status == MonthlyProfitPeriod.Status.DRAFT
            ),
            "incomplete_periods": sum(1 for period in periods if not period.is_complete),
            "missing_cogs_periods": sum(1 for period in periods if not period.cogs_complete),
            "missing_expense_periods": sum(
                1
                for period in periods
                if not all(
                    (
                        period.labor_recorded,
                        period.electricity_recorded,
                        period.maintenance_recorded,
                        period.other_recorded,
                    )
                )
            ),
            "uncalculated_months": len(uncalculated_months),
        },
        "formula": {
            "gross_profit": "صافي إيراد المبيعات − تكلفة البضاعة المباعة",
            "monthly_result": (
                "إجمالي الربح − العمالة − الكهرباء − الصيانة − المصاريف التشغيلية "
                "الأخرى − خسارة الهالك المفصلة (تُخصم مرة واحدة فقط)"
            ),
            "loss_recovery": (
                "الربح المتبقي بعد التغطية = نتيجة الشهر الموجبة − ما استُرد من رصيد "
                "الخسائر المتراكمة الافتتاحي"
            ),
            "loss_carry_forward": (
                "الرصيد الختامي = الرصيد الافتتاحي − المسترد + خسارة الشهر الجديدة"
            ),
        },
        "source": (
            "Persisted MonthlyProfitPeriod rows, active shipment lines, persisted "
            "ProductCostingEntry direct-material snapshots, and active MonthlyExpense rows."
        ),
        "basis_notes": [
            "COGS uses the persisted direct-material cost per good unit for quantities actually shipped; it excludes allocated labor/electricity/water/maintenance/other overhead so those period expenses are not deducted twice.",
            "Labor is deducted once as an operating expense in the month and is not embedded in the COGS number.",
            "Operating expenses are summed once per active expense record and are not reallocated across production lines.",
            "Water expense is recorded for costing completeness but is outside the agreed monthly profit calculation; it is not deducted again here.",
            "A recorded expense of 0.00 is a confirmed zero; an absent expense category is missing data and is never silently treated as confirmed zero.",
            "Credit sales are recognized by active shipment, not by collection, because the project has no payment/collection ledger.",
            "No discounts, sales returns, or customer credit notes are implemented in the current data model; none are invented here. Cancelled orders and reversed shipment movements are excluded from active revenue and COGS.",
            "The monthly result is a management operating result before recovery of prior losses and is not accounting net profit, tax, or a distribution.",
            "Scrap treatment: material cost of recorded scrap is separated from good-unit cost only under the period's SEPARATE_ABNORMAL policy; that separated amount is deducted exactly once as a loss line and only for completed, fully-costed orders. Scrap under the normal ABSORBED policy stays inside material cost per good unit and therefore inside COGS — it is never deducted again. Unfinished or uncomputed orders' separated scrap is reported as unreconciled and is never deducted or fabricated.",
        ],
        "accounting_final": False,
        "label_warning": (
            "استخدم «نتيجة الشهر» و«الربح المتبقي بعد تغطية الخسائر»؛ لا تسمِّ أي منهما "
            "صافي الربح النهائي."
        ),
    }


def _customers_section(start, end):
    return {
        "total": Customer.objects.count(),
        "active": Customer.objects.filter(active=True).count(),
        "with_orders_in_period": Customer.objects.filter(
            sales_orders__order_date__range=(start, end),
            sales_orders__status__in=SALES_ACTIVE_STATUSES,
        ).distinct().count(),
        "note": "Contact master data only; no credit, tax, or payment terms are implemented.",
    }


def _suppliers_section():
    return {
        "total": Supplier.objects.count(),
        "active": Supplier.objects.filter(active=True).count(),
        "note": (
            "Contact master data only; no bank, tax, invoice, or payment terms are stored."
        ),
    }


def _financial_integrity():
    return {
        "accounting_available": False,
        "currency": None,
        "profit": None,
        "net_profit": None,
        "monthly_management_profit_available": True,
        "monthly_management_profit_is_accounting_net_profit": False,
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
        "note": (
            "No authoritative accounting net profit, receivable, payable, or cash metric is "
            "reported by this API. A persisted monthly management operating result is "
            "reported separately and is explicitly not final accounting net profit."
        ),
    }


def data_quality_warnings(sales, purchases, inventory, production, costing, profit=None):
    warnings = []
    low_stock_count = inventory["low_stock"]["count"]
    if low_stock_count:
        warnings.append({
            "code": "LOW_STOCK",
            "severity": "WARNING",
            "count": low_stock_count,
            "message_ar": "أصناف وصلت إلى حد المخزون المنخفض المكوّن.",
        })
    if sales["customer_price_review_pending_count"]:
        warnings.append({
            "code": "CUSTOMER_ORDER_PRICE_REVIEW",
            "severity": "WARNING",
            "count": sales["customer_price_review_pending_count"],
            "message_ar": (
                "طلبات عملاء مسودة تحتوي بنودًا بسعر صفر وتنتظر مراجعة السعر قبل التأكيد."
            ),
        })
    if sales["legacy_unpriced_confirmed_orders_count"]:
        warnings.append({
            "code": "LEGACY_UNPRICED_ORDER",
            "severity": "WARNING",
            "count": sales["legacy_unpriced_confirmed_orders_count"],
            "message_ar": (
                "أوامر عملاء غير مسودة ما زالت تحتوي بنودًا بسعر صفر؛ لا تُعالج كإيراد محقق."
            ),
        })
    negative_count = inventory["reconciliation"]["negative_balance_count"]
    if negative_count:
        warnings.append({
            "code": "NEGATIVE_STOCK",
            "severity": "ERROR",
            "count": negative_count,
            "message_ar": "أرصدة مخزون سالبة ظاهرة في دفتر المخزون.",
        })
    missing_labor = costing["review_summary"]["missing_labor_periods"]
    missing_electricity = costing["review_summary"]["missing_electricity_periods"]
    missing_water = costing["review_summary"]["missing_water_periods"]
    if missing_labor:
        warnings.append({
            "code": "COSTING_MISSING_LABOR",
            "severity": "WARNING",
            "count": missing_labor,
            "message_ar": "فترات تكاليف بها إنتاج دون تسجيل مصاريف العمالة.",
        })
    if missing_electricity:
        warnings.append({
            "code": "COSTING_MISSING_ELECTRICITY",
            "severity": "WARNING",
            "count": missing_electricity,
            "message_ar": "فترات تكاليف بها إنتاج دون تسجيل مصاريف الكهرباء.",
        })
    if missing_water:
        warnings.append({
            "code": "COSTING_MISSING_WATER",
            "severity": "WARNING",
            "count": missing_water,
            "message_ar": "فترات تكاليف بها إنتاج دون تسجيل مصاريف المياه.",
        })
    incomplete_periods = costing["review_summary"]["incomplete_periods"]
    if incomplete_periods:
        warnings.append({
            "code": "COSTING_INCOMPLETE",
            "severity": "WARNING",
            "count": incomplete_periods,
            "message_ar": "فترات تكاليف غير مكتملة البيانات أو الاحتساب.",
        })
    incomplete_entries = costing["review_summary"]["incomplete_entries"]
    if incomplete_entries:
        warnings.append({
            "code": "COSTING_FALLBACK_REVIEW",
            "severity": "WARNING",
            "count": incomplete_entries,
            "message_ar": (
                "بنود تكلفة محسوبة بطريقة احتياطية/غير مكتملة وتحتاج مراجعة. "
                "المبلغ الكمي للجزء الاحتياطي غير محفوظ منفصلًا."
            ),
        })
    if sales["orders_by_status"].get(SalesOrder.Status.DRAFT, 0):
        warnings.append({
            "code": "DRAFT_SALES_ORDERS",
            "severity": "INFO",
            "count": sales["orders_by_status"][SalesOrder.Status.DRAFT],
            "message_ar": "أوامر بيع مسودة بانتظار المراجعة أو التأكيد.",
        })
    if purchases["orders_by_status"].get(PurchaseOrder.Status.DRAFT, 0):
        warnings.append({
            "code": "DRAFT_PURCHASE_ORDERS",
            "severity": "INFO",
            "count": purchases["orders_by_status"][PurchaseOrder.Status.DRAFT],
            "message_ar": "أوامر شراء مسودة بانتظار المراجعة أو التأكيد.",
        })
    if production["open_orders_count"]:
        warnings.append({
            "code": "OPEN_PRODUCTION",
            "severity": "INFO",
            "count": production["open_orders_count"],
            "message_ar": "أوامر إنتاج مفتوحة لم تكتمل بعد.",
        })
    if production["orders_without_results_count"]:
        warnings.append({
            "code": "PRODUCTION_WITHOUT_OUTPUT",
            "severity": "INFO",
            "count": production["orders_without_results_count"],
            "message_ar": "أوامر إنتاج أُنشئت في الفترة دون أي نتيجة إنتاج مسجلة.",
        })
    if profit is not None:
        review = profit["review_summary"]
        if review["missing_expense_periods"]:
            warnings.append({
                "code": "PROFIT_MISSING_OPERATING_EXPENSES",
                "severity": "WARNING",
                "count": review["missing_expense_periods"],
                "message_ar": (
                    "فترات أرباح بها فئة مصاريف تشغيلية غير مسجلة. غياب السجل لا يعني "
                    "قيمة صفر مؤكدة، ولا تُعتمد النتيجة قبل التسجيل أو تسجيل 0.00 صريحًا."
                ),
            })
        if review["missing_cogs_periods"]:
            warnings.append({
                "code": "PROFIT_INCOMPLETE_COGS",
                "severity": "WARNING",
                "count": review["missing_cogs_periods"],
                "message_ar": (
                    "فترات أرباح بها منتجات مشحونة دون تكلفة مواد معتمدة محفوظة؛ لم يُفترض "
                    "صفر لتكلفة البضاعة المباعة."
                ),
            })
        if review["incomplete_periods"]:
            warnings.append({
                "code": "PROFIT_INCOMPLETE_PERIODS",
                "severity": "WARNING",
                "count": review["incomplete_periods"],
                "message_ar": "فترات أرباح وخسائر غير مكتملة البيانات ولا يمكن اعتمادها.",
            })
        if review["draft_periods"]:
            warnings.append({
                "code": "PROFIT_DRAFT_PERIODS",
                "severity": "INFO",
                "count": review["draft_periods"],
                "message_ar": "نتائج أرباح مسودة لم تُعتمد نهائيًا بعد.",
            })
        if review["uncalculated_months"]:
            warnings.append({
                "code": "PROFIT_MONTHS_NOT_CALCULATED",
                "severity": "INFO",
                "count": review["uncalculated_months"],
                "message_ar": (
                    "أشهر داخل الفترة المحددة ليس لها نتيجة أرباح محفوظة؛ لا تُعرض كصفر "
                    "مؤكد ولا تُستخدم لتغيير رصيد الخسائر المتراكمة."
                ),
            })
        warnings.append({
            "code": "PROFIT_MANAGEMENT_RESULT_ONLY",
            "severity": "INFO",
            "count": None,
            "message_ar": (
                "نتيجة الشهر إدارية تشغيلية وليست صافي ربح محاسبيًا نهائيًا. المياه "
                "مستبعدة من حساب الربح المتفق عليه، والعمالة لا تُخصم مرتين."
            ),
        })
    warnings.append({
        "code": "ACCOUNTING_UNAVAILABLE",
        "severity": "INFO",
        "count": None,
        "message_ar": (
            "لا يوجد دفتر محاسبي أو فواتير أو مدفوعات؛ قيم البيع والشراء والتكلفة ليست "
            "إيرادًا أو ربحًا محاسبيًا نهائيًا."
        ),
    })
    warnings.append({
        "code": "HISTORICAL_STOCK_UNAVAILABLE",
        "severity": "INFO",
        "count": None,
        "message_ar": (
            "أرصدة المخزون المعروضة حالية فقط؛ لا يُعرض رصيد تاريخي بتاريخ محدد."
        ),
    })
    return warnings


def build_operational_alerts(sales, purchases, production, costing, inventory):
    # Keep the existing alert codes for mobile/client compatibility.
    return [
        {
            "code": "LOW_STOCK",
            "severity": "WARNING",
            "count": inventory["low_stock"]["count"],
            "message_ar": "أصناف وصلت إلى حد المخزون المنخفض المكوّن.",
        },
        {
            "code": "DRAFT_SALES_ORDERS",
            "severity": "INFO",
            "count": sales["orders_by_status"].get(SalesOrder.Status.DRAFT, 0),
            "message_ar": "أوامر بيع مسودة بانتظار المراجعة أو التأكيد.",
        },
        {
            "code": "DRAFT_PURCHASE_ORDERS",
            "severity": "INFO",
            "count": purchases["orders_by_status"].get(PurchaseOrder.Status.DRAFT, 0),
            "message_ar": "أوامر شراء مسودة بانتظار المراجعة أو التأكيد.",
        },
        {
            "code": "PRODUCTION_IN_PROGRESS",
            "severity": "INFO",
            "count": production["open_orders_count"],
            "message_ar": "أوامر إنتاج مفتوحة أو قيد التنفيذ.",
        },
        {
            "code": "COSTING_DRAFT_PERIODS",
            "severity": "INFO",
            "count": sum(
                1
                for row in costing["periods"]
                if row["status"] == CostingPeriod.Status.DRAFT
            ),
            "message_ar": "فترات تكاليف لم تُعتمد نهائيًا.",
        },
    ]
