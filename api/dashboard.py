"""Owner API projections over the consolidated read-only reporting layer.

All calculations are read from persisted operational records and the shared
inventory ledger. Monetary formatting and arithmetic reconciliation happen here
only at the display boundary; costing outputs are never recalculated.
"""

from django.utils import timezone

from reports.services import (
    build_operational_alerts,
    costing_report,
    data_quality_warnings,
    inventory_report,
    period_payload,
    production_report,
    profit_report,
    purchases_report,
    sales_report,
    _customers_section,
    _financial_integrity,
    _suppliers_section,
)


def _base(validated):
    start = validated["start_date"]
    end = validated["end_date"]
    granularity = validated["granularity"]
    recent_limit = validated.get("recent_limit") or 5
    dimension_limit = validated.get("dimension_limit") or 20
    return start, end, granularity, recent_limit, dimension_limit


def _reports(validated):
    start, end, granularity, recent_limit, dimension_limit = _base(validated)
    sales = sales_report(
        start, end, granularity, recent_limit, dimension_limit
    )
    purchases = purchases_report(
        start, end, granularity, recent_limit, dimension_limit
    )
    inventory = inventory_report(
        start, end, granularity, recent_limit, dimension_limit
    )
    production = production_report(
        start, end, granularity, recent_limit, dimension_limit
    )
    costing = costing_report(start, end, recent_limit, dimension_limit)
    profit = profit_report(start, end, dimension_limit)
    return {
        "start": start,
        "end": end,
        "granularity": granularity,
        "sales": sales,
        "purchases": purchases,
        "inventory": inventory,
        "production": production,
        "costing": costing,
        "profit": profit,
    }


def _warnings(data):
    return data_quality_warnings(
        data["sales"],
        data["purchases"],
        data["inventory"],
        data["production"],
        data["costing"],
        data["profit"],
    )


def inventory_payload(validated):
    data = _reports(validated)
    return {
        "generated_at": timezone.now(),
        "period": period_payload(data["start"], data["end"], data["granularity"]),
        "inventory": data["inventory"]["inventory"],
        "low_stock": data["inventory"]["low_stock"],
        "movement_summary": data["inventory"]["movement_summary"],
        "recent_movements": data["inventory"]["recent_movements"],
        "inventory_reconciliation": data["inventory"]["reconciliation"],
        "data_quality": _warnings(data),
        "financial_integrity": _financial_integrity(),
    }


def sales_payload(validated):
    data = _reports(validated)
    return {
        "generated_at": timezone.now(),
        "period": period_payload(data["start"], data["end"], data["granularity"]),
        "sales": data["sales"],
        "recent_shipments": data["sales"]["recent_shipments"],
        "data_quality": _warnings(data),
        "financial_integrity": _financial_integrity(),
    }


def purchases_payload(validated):
    data = _reports(validated)
    return {
        "generated_at": timezone.now(),
        "period": period_payload(data["start"], data["end"], data["granularity"]),
        "purchases": data["purchases"],
        "data_quality": _warnings(data),
        "financial_integrity": _financial_integrity(),
    }


def production_payload(validated):
    data = _reports(validated)
    return {
        "generated_at": timezone.now(),
        "period": period_payload(data["start"], data["end"], data["granularity"]),
        "production": data["production"],
        "data_quality": _warnings(data),
        "financial_integrity": _financial_integrity(),
    }


def costing_payload(validated):
    data = _reports(validated)
    return {
        "generated_at": timezone.now(),
        "period": period_payload(data["start"], data["end"], data["granularity"]),
        "costing": data["costing"],
        "data_quality": _warnings(data),
        "financial_integrity": _financial_integrity(),
    }


def profit_payload(validated):
    data = _reports(validated)
    return {
        "generated_at": timezone.now(),
        "period": period_payload(data["start"], data["end"], data["granularity"]),
        "profit_loss": data["profit"],
        "data_quality": _warnings(data),
        "financial_integrity": _financial_integrity(),
    }


def dashboard_payload(validated):
    data = _reports(validated)
    sales = data["sales"]
    purchases = data["purchases"]
    inventory = data["inventory"]
    production = data["production"]
    costing = data["costing"]
    profit = data["profit"]
    warnings = _warnings(data)
    return {
        "generated_at": timezone.now(),
        "period": period_payload(data["start"], data["end"], data["granularity"]),
        "sales": sales,
        "purchases": purchases,
        "inventory": inventory["inventory"],
        "low_stock_alerts": inventory["low_stock"]["alerts"],
        "inventory_movements": inventory["movement_summary"],
        "production": production,
        "recent": {
            "sales_orders": sales["recent_orders"],
            "purchase_orders": purchases["recent_orders"],
            "shipments": sales["recent_shipments"],
            "receipts": purchases["recent_receipts"],
            "production_results": production["recent_results"],
        },
        "customers": _customers_section(data["start"], data["end"]),
        "suppliers": _suppliers_section(),
        "costing": costing,
        "profit_loss": profit,
        "operational_alerts": build_operational_alerts(
            sales, purchases, production, costing, inventory
        ),
        "data_quality": warnings,
        "financial_integrity": _financial_integrity(),
        "data_sources": {
            "sales": "sales.SalesOrder, SalesOrderLine, Shipment, ShipmentLine",
            "purchases": "purchasing.PurchaseOrder, PurchaseOrderLine, PurchaseReceipt, PurchaseReceiptLine",
            "inventory": "shared inventory ledger through inventory.services.ledger_balances",
            "production": "manufacturing.ProductionOrder, MaterialIssue, and ProductionResult",
            "costing": "persisted costing.CostingPeriod, ProductCostingEntry, and ProductionOrderCostingEntry",
            "profit_loss": "persisted costing.MonthlyProfitPeriod, MonthlyProfitCOGSLine, and active MonthlyExpense rows",
            "accounting": "unavailable; not implemented",
        },
    }
