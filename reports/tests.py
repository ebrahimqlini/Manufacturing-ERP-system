from datetime import datetime, time, timedelta
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product, ProductCategory, UnitOfMeasure
from costing.models import (
    CostingPeriod,
    MonthlyProfitPeriod,
    ProductCostingEntry,
    ProductionOrderCostingEntry,
)
from inventory.models import StockMovement
from inventory.services import ledger_balances, post_movement, reverse_movement
from manufacturing.models import (
    BOMComponent,
    BillOfMaterials,
    MaterialIssue,
    ProductionLine,
    ProductionOrder,
    ProductionMaterialRequirement,
    ProductionResult,
)
from purchasing.models import PurchaseOrder, Supplier
from purchasing.services import (
    cancel_order as cancel_purchase_order,
    confirm_order as confirm_purchase_order,
    create_receipt,
    save_draft_order as save_purchase_draft,
)
from sales.models import Customer, SalesOrder
from sales.services import (
    cancel_order as cancel_sales_order,
    confirm_order as confirm_sales_order,
    create_shipment,
    save_draft_order as save_sales_draft,
)

from .services import (
    costing_report,
    current_stock_rows,
    inventory_report,
    production_report,
    purchases_report,
    sales_report,
)


class ConsolidatedReportFixture(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.month_start = self.today.replace(day=1)
        self.suffix = uuid.uuid4().hex[:8]
        User = get_user_model()
        from django.contrib.auth.models import Permission
        self.owner = User.objects.create_user(
            username=f"report-owner-{self.suffix}",
            password="owner-test-password",
            is_staff=True,
        )
        perms = Permission.objects.filter(
            content_type__app_label__in=[
                "manufacturing", "inventory", "costing", "purchasing", "sales"
            ]
        )
        self.owner.user_permissions.set(perms)
        self.non_staff = User.objects.create_user(
            username=f"report-viewer-{self.suffix}",
            password="viewer-test-password",
        )

        self.pcs = UnitOfMeasure.objects.create(
            code=f"PCS{self.suffix}", name_ar="قطعة", dimension="count"
        )
        self.kg = UnitOfMeasure.objects.create(
            code=f"KG{self.suffix}", name_ar="كيلو", dimension="weight"
        )
        self.finished_category = ProductCategory.objects.create(
            code=f"finished-{self.suffix}", name_ar="منتجات تامة"
        )
        self.raw_category = ProductCategory.objects.create(
            code=f"raw-{self.suffix}", name_ar="مواد خام"
        )
        self.scrap_category = ProductCategory.objects.create(
            code=f"scrap-{self.suffix}", name_ar="هالك"
        )
        self.finished = Product.objects.create(
            sku=f"FG-{self.suffix}",
            name_ar="كوب ورقي",
            category=self.finished_category,
            base_uom=self.pcs,
            available_to_customers=True,
            low_stock_threshold=Decimal("150"),
        )
        self.raw = Product.objects.create(
            sku=f"RAW-{self.suffix}",
            name_ar="ورق خام",
            category=self.raw_category,
            base_uom=self.kg,
        )
        self.scrap = Product.objects.create(
            sku=f"SCRAP-{self.suffix}",
            name_ar="هالك ورقي",
            category=self.scrap_category,
            base_uom=self.kg,
        )
        self.warehouse_code = f"WH-{self.suffix}"
        from inventory.models import StorageLocation, Warehouse

        self.warehouse = Warehouse.objects.create(
            code=self.warehouse_code, name_ar="المخزن الرئيسي"
        )
        self.location = StorageLocation.objects.create(
            warehouse=self.warehouse,
            code=f"LOC-{self.suffix}",
            name_ar="موقع الإنتاج",
        )
        self._opening(self.finished, Decimal("100"), self.pcs, "FG")
        self._opening(self.raw, Decimal("1000"), self.kg, "RAW")

        self.customer = Customer.objects.create(
            code=f"C-{self.suffix}",
            name_ar="عميل الجملة",
            created_by=self.owner,
        )
        self.supplier = Supplier.objects.create(
            code=f"S-{self.suffix}",
            name_ar="مورد الورق",
            created_by=self.owner,
        )
        self.line = ProductionLine.objects.create(
            code=f"line-{self.suffix}", name_ar="خط الأكواب"
        )

        self.sales_partial = self._create_sales_order(
            quantity=Decimal("10"), price=Decimal("20"), confirm=True
        )
        self._ship(self.sales_partial, Decimal("4"))
        self.sales_pending = self._create_sales_order(
            quantity=Decimal("3"),
            price=Decimal("0"),
            customer_submitted=True,
        )
        self.sales_cancelled = self._create_sales_order(
            quantity=Decimal("1"), price=Decimal("5")
        )
        cancel_sales_order(
            order=self.sales_cancelled, actor=self.owner, reason="اختبار الإلغاء"
        )
        self.sales_reversed = self._create_sales_order(
            quantity=Decimal("2"), price=Decimal("10"), confirm=True
        )
        shipment = self._ship(self.sales_reversed, Decimal("2"))
        reverse_movement(
            movement=shipment.lines.get().movement,
            reference=f"REV-SAL-{self.suffix}",
            actor=self.owner,
            reason="تصحيح شحن",
        )

        self.purchase_partial = self._create_purchase_order(Decimal("100"))
        self._receive(self.purchase_partial, Decimal("40"))
        self.purchase_draft = save_purchase_draft(
            actor=self.owner,
            supplier=self.supplier,
            order_date=self.today,
            lines=[{
                "material": self.raw,
                "quantity": Decimal("10"),
                "unit": self.kg,
                "unit_price": Decimal("5.0000"),
                "notes": "",
            }],
        )
        self.purchase_cancelled = self._create_purchase_order(Decimal("5"))
        cancel_purchase_order(
            order=self.purchase_cancelled, actor=self.owner, reason="اختبار الإلغاء"
        )
        self.purchase_reversed = self._create_purchase_order(Decimal("10"))
        receipt = self._receive(self.purchase_reversed, Decimal("10"))
        reverse_movement(
            movement=receipt.lines.get().movement,
            reference=f"REV-PO-{self.suffix}",
            actor=self.owner,
            reason="تصحيح استلام",
        )

        self.production_prior = self._create_production_output(
            planned=Decimal("20"), good=Decimal("8"), scrap=Decimal("2"),
            created_at=timezone.make_aware(
                datetime.combine(
                    (self.today.replace(day=1) - timedelta(days=1)).replace(day=15),
                    time(12, 0),
                )
            ),
            occurred_at=timezone.make_aware(datetime.combine(self.today, time(12, 0))),
        )
        self.production_open = ProductionOrder.objects.create(
            order_number=f"PRD2-{self.suffix}",
            product=self.finished,
            production_line=self.line,
            source_location=self.location,
            planned_quantity=Decimal("5"),
            planned_unit=self.pcs,
            planned_quantity_base=Decimal("5"),
            base_unit_snapshot=self.pcs,
            status=ProductionOrder.Status.DRAFT,
            created_by=self.owner,
        )

        self.costing = CostingPeriod.objects.create(
            year=self.today.year,
            month=self.today.month,
            start_date=self.month_start,
            end_date=self.today,
            status=CostingPeriod.Status.DRAFT,
            labor_recorded=True,
            electricity_recorded=True,
            water_recorded=True,
            labor_total=Decimal("10.00"),
            electricity_total=Decimal("5.00"),
            water_total=Decimal("2.00"),
            other_industrial_total=Decimal("3.00"),
            materials_total=Decimal("100.00"),
            total_production_cost=Decimal("100.00"),
            is_complete=True,
            calculated_at=timezone.now(),
        )
        ProductCostingEntry.objects.create(
            period=self.costing,
            product=self.finished,
            production_line=self.line,
            good_units_produced=Decimal("8.0000"),
            scrap_units_produced=Decimal("2.0000"),
            actual_material_cost=Decimal("100.00"),
            material_cost_assigned_to_good=Decimal("80.00"),
            waste_material_cost=Decimal("20.00"),
            allocated_labor_cost=Decimal("10.00"),
            allocated_electricity_cost=Decimal("5.00"),
            allocated_water_cost=Decimal("2.00"),
            allocated_other_cost=Decimal("3.00"),
            total_production_cost=Decimal("100.00"),
            cost_per_good_unit=Decimal("12.5000"),
            calculation_status="COMPLETE",
        )
        ProductionOrderCostingEntry.objects.create(
            period=self.costing,
            order=self.production_prior,
            product=self.finished,
            production_line=self.line,
            good_units_produced=Decimal("8.0000"),
            scrap_units_produced=Decimal("2.0000"),
            actual_material_cost=Decimal("100.00"),
            material_cost_assigned_to_good=Decimal("80.00"),
            waste_material_cost=Decimal("20.00"),
            allocated_labor_cost=Decimal("10.00"),
            allocated_electricity_cost=Decimal("5.00"),
            allocated_water_cost=Decimal("2.00"),
            allocated_other_cost=Decimal("3.00"),
            total_production_cost=Decimal("100.00"),
            cost_per_good_unit=Decimal("12.5000"),
            calculation_status="COMPLETE",
        )

    def _opening(self, product, value, unit, prefix):
        post_movement(
            reference=f"OPEN-{prefix}-{self.suffix}",
            movement_type=StockMovement.Type.OPENING,
            product=product,
            quantity=value,
            unit=unit,
            actor=self.owner,
            destination_location=self.location,
        )

    def _create_sales_order(self, *, quantity, price, confirm=False, customer_submitted=False):
        order = save_sales_draft(
            actor=self.owner,
            customer=self.customer,
            order_date=self.today,
            customer_submitted=customer_submitted,
            lines=[{
                "product": self.finished,
                "quantity": quantity,
                "unit": self.pcs,
                "unit_price": price,
                "notes": "",
            }],
        )
        if confirm:
            order = confirm_sales_order(order=order, actor=self.owner)
        return order

    def _ship(self, order, value):
        order.refresh_from_db()
        shipment = create_shipment(
            order=order,
            actor=self.owner,
            reference=f"SAL-{uuid.uuid4().hex[:14]}",
            shipment_date=self.today,
            source_location=self.location,
            lines=[{
                "order_line": order.lines.get(),
                "quantity": value,
                "unit": self.pcs,
            }],
        )
        order.refresh_from_db()
        return shipment

    def _create_purchase_order(self, quantity):
        order = save_purchase_draft(
            actor=self.owner,
            supplier=self.supplier,
            order_date=self.today,
            lines=[{
                "material": self.raw,
                "quantity": quantity,
                "unit": self.kg,
                "unit_price": Decimal("5.0000"),
                "notes": "",
            }],
        )
        return confirm_purchase_order(order=order, actor=self.owner)

    def _receive(self, order, value):
        receipt = create_receipt(
            order=order,
            actor=self.owner,
            reference=f"RCV-{uuid.uuid4().hex[:14]}",
            received_date=self.today,
            destination_location=self.location,
            lines=[{
                "order_line": order.lines.get(),
                "quantity": value,
                "unit": self.kg,
            }],
        )
        return receipt

    def _create_production_output(self, *, planned, good, scrap, created_at=None, occurred_at=None):
        from django.db import connection
        order = ProductionOrder.objects.create(
            order_number=f"PRD-{uuid.uuid4().hex[:10]}",
            product=self.finished,
            production_line=self.line,
            source_location=self.location,
            planned_quantity=planned,
            planned_unit=self.pcs,
            planned_quantity_base=planned,
            base_unit_snapshot=self.pcs,
            status=ProductionOrder.Status.IN_PROGRESS,
            created_by=self.owner,
        )
        if created_at is not None:
            # Bypass ORM update protection (auto_now_add prevents create() from setting created_at)
            with connection.cursor() as cursor:
                cursor.execute(
                    'UPDATE manufacturing_productionorder SET created_at = %s WHERE id = %s',
                    [created_at, order.pk]
                )
            order.refresh_from_db()
        bom = BillOfMaterials.objects.create(
            product=self.finished,
            version=1,
            output_quantity=Decimal("1"),
            output_unit=self.pcs,
            created_by=self.owner,
        )
        component = BOMComponent.objects.create(
            bom=bom,
            material=self.raw,
            quantity=Decimal("2"),
            unit=self.kg,
            sequence=10,
        )
        requirement = ProductionMaterialRequirement.objects.create(
            order=order,
            source_component=component,
            material=self.raw,
            base_unit_snapshot=self.kg,
            sequence=10,
            planned_quantity_base=Decimal("40"),
        )
        movement = post_movement(
            reference=f"ISSUE-{uuid.uuid4().hex[:14]}",
            movement_type=StockMovement.Type.ISSUE,
            product=self.raw,
            quantity=Decimal("30"),
            unit=self.kg,
            actor=self.owner,
            source_location=self.location,
            reason="صرف إنتاج",
        )
        MaterialIssue.objects.create(
            order=order,
            requirement=requirement,
            reference=f"MI-{uuid.uuid4().hex[:14]}",
            movement=movement,
            quantity=Decimal("30"),
            unit=self.kg,
            quantity_base=Decimal("30"),
            actor=self.owner,
        )
        ProductionResult.objects.create(
            order=order,
            good_quantity=good,
            scrap_quantity=scrap,
            unit=self.pcs,
            good_quantity_base=good,
            scrap_quantity_base=scrap,
            actor=self.owner,
            occurred_at=occurred_at or created_at or timezone.now(),
        )
        return order

    def period_kwargs(self):
        return {
            "start_date": self.month_start.isoformat(),
            "end_date": self.today.isoformat(),
        }


class SalesReportTests(ConsolidatedReportFixture):
    def test_sales_reconciles_statuses_values_quantities_and_reversals(self):
        report = sales_report(self.month_start, self.today, "day", 5, 20)

        self.assertEqual(report["orders_total"], 4)
        self.assertEqual(report["non_cancelled_orders_count"], 3)
        self.assertEqual(report["cancelled_orders_count"], 1)
        self.assertEqual(report["customer_price_review_pending_count"], 1)
        self.assertEqual(report["commercial_order_value"], "220.00")
        self.assertEqual(report["net_shipped_value"], "80.00")
        self.assertEqual(report["reversed_shipment_value"], "20.00")
        self.assertEqual(report["total_shipment_value"], "100.00")
        self.assertEqual(report["shipments_count"], 2)
        self.assertEqual(report["active_shipments_count"], 1)
        self.assertEqual(report["fully_reversed_shipments_count"], 1)
        self.assertEqual(report["partially_shipped_orders_count"], 1)
        self.assertEqual(report["unshipped_orders_count"], 2)
        self.assertEqual(report["fully_shipped_orders_count"], 0)

        quantities = {row["base_uom"]: row for row in report["quantity_summary"]}
        self.assertEqual(quantities[self.pcs.code]["ordered_quantity_base"], "15.000000000000")
        self.assertEqual(quantities[self.pcs.code]["shipped_quantity_base"], "4.000000000000")
        self.assertEqual(quantities[self.pcs.code]["reversed_quantity_base"], "2.000000000000")
        self.assertEqual(quantities[self.pcs.code]["outstanding_quantity_base"], "11.000000000000")
        self.assertEqual(report["by_customer"][0]["order_count"], 3)
        self.assertEqual(report["by_customer"][0]["ordered_value"], "220.00")
        self.assertEqual(report["top_products"][0]["quantity_base"], "15.000000000000")


class PurchaseReportTests(ConsolidatedReportFixture):
    def test_purchases_reconcile_order_value_receipts_and_reversals(self):
        report = purchases_report(self.month_start, self.today, "day", 5, 20)

        self.assertEqual(report["orders_total"], 4)
        self.assertEqual(report["non_cancelled_orders_count"], 3)
        self.assertEqual(report["cancelled_orders_count"], 1)
        self.assertEqual(report["commercial_order_value"], "600.00")
        self.assertEqual(report["net_received_value"], "200.00")
        self.assertEqual(report["reversed_receipt_value"], "50.00")
        self.assertEqual(report["total_receipt_value"], "250.00")
        self.assertEqual(report["receipts_count"], 2)
        self.assertEqual(report["active_receipts_count"], 1)
        self.assertEqual(report["fully_reversed_receipts_count"], 1)
        self.assertEqual(report["receipt_state_counts"]["PARTIALLY_RECEIVED"], 1)
        self.assertEqual(report["receipt_state_counts"]["NOT_RECEIVED"], 2)

        quantities = {row["base_uom"]: row for row in report["quantity_summary"]}
        self.assertEqual(quantities[self.kg.code]["ordered_quantity_base"], "120.000000000000")
        self.assertEqual(quantities[self.kg.code]["received_quantity_base"], "40.000000000000")
        self.assertEqual(quantities[self.kg.code]["reversed_quantity_base"], "10.000000000000")
        self.assertEqual(quantities[self.kg.code]["outstanding_quantity_base"], "80.000000000000")
        self.assertEqual(report["by_supplier"][0]["ordered_value"], "600.00")
        self.assertEqual(report["by_supplier"][0]["received_value"], "200.00")


class InventoryReportTests(ConsolidatedReportFixture):
    def test_current_stock_matches_ledger_and_keeps_units_separate(self):
        report = inventory_report(self.month_start, self.today, "day", 8, 20)
        rows = {row["sku"]: row for row in current_stock_rows()}
        self.assertEqual(rows[self.finished.sku]["quantity_base"], "96.000000000000")
        self.assertEqual(rows[self.raw.sku]["quantity_base"], "1010.000000000000")
        self.assertEqual(
            rows[self.finished.sku]["base_uom"], self.pcs.code
        )
        self.assertEqual(rows[self.raw.sku]["base_uom"], self.kg.code)
        self.assertFalse(report["reconciliation"]["historical_balance_available"])
        self.assertFalse(report["reconciliation"]["valuation_available"])
        self.assertEqual(report["low_stock"]["count"], 1)
        self.assertEqual(report["low_stock"]["alerts"][0]["sku"], self.finished.sku)
        self.assertEqual(report["movement_summary"]["reversals_in_period_count"], 2)
        self.assertEqual(
            report["movement_summary"]["original_movements_with_reversal_count"], 2
        )
        expected_by_product = {}
        for row in ledger_balances():
            expected_by_product[row["product_id"]] = (
                expected_by_product.get(row["product_id"], Decimal("0"))
                + row["balance"]
            )
        self.assertEqual(
            Decimal(rows[self.finished.sku]["quantity_base"]),
            expected_by_product[self.finished.pk],
        )
        self.assertEqual(
            Decimal(rows[self.raw.sku]["quantity_base"]),
            expected_by_product[self.raw.pk],
        )


class ProductionReportTests(ConsolidatedReportFixture):
    def test_output_consumption_yield_and_cross_period_bases_are_explicit(self):
        report = production_report(self.month_start, self.today, "day", 5, 20)

        self.assertEqual(report["cross_period_output_count"], 1)
        self.assertEqual(report["orders_without_results_count"], 1)
        self.assertEqual(report["open_orders_count"], 2)
        rows = {row["base_uom"]: row for row in report["total_output_by_uom"]}
        self.assertEqual(rows[self.pcs.code]["quantity_base"], "10.000000000000")
        consumption = {row["base_uom"]: row for row in report["total_consumption_by_uom"]}
        self.assertEqual(consumption[self.kg.code]["quantity_base"], "30.000000000000")
        self.assertEqual(report["yield_rows"][0]["yield_percent"], "80.00")
        self.assertIn("ProductionOrder.created_at", report["status_counts_basis"])
        self.assertIn("ProductionResult.occurred_at", report["output_basis"])
        current_line = next(row for row in report["lines"] if row["code"] == self.line.code)
        self.assertEqual(current_line["status_counts"], {"DRAFT": 1})
        self.assertEqual(
            current_line["planned_quantities"][0]["planned_quantity_base"],
            "5.000000000000",
        )
        reconciliation = {
            row["base_uom"]: row for row in report["reconciliation"]["output_by_uom"]
        }
        self.assertEqual(
            reconciliation[self.pcs.code]["total_quantity_base"],
            "10.000000000000",
        )
        self.assertEqual(
            reconciliation[self.pcs.code]["attributed_to_lines_base"],
            "10.000000000000",
        )
        self.assertEqual(
            reconciliation[self.pcs.code]["unallocated_base"],
            "0.000000000000",
        )
        self.assertTrue(report["reconciliation"]["all_output_attributed_to_line"])

    def test_line_filter_is_scoped_and_does_not_duplicate_shared_records(self):
        second_line = ProductionLine.objects.create(
            code=f"line-b-{self.suffix}", name_ar="خط الحاويات"
        )
        order = ProductionOrder.objects.create(
            order_number=f"PRD-B-{self.suffix}",
            product=self.finished,
            production_line=second_line,
            source_location=self.location,
            planned_quantity=Decimal("6"),
            planned_unit=self.pcs,
            planned_quantity_base=Decimal("6"),
            base_unit_snapshot=self.pcs,
            status=ProductionOrder.Status.IN_PROGRESS,
            created_by=self.owner,
        )
        ProductionResult.objects.create(
            order=order,
            good_quantity=Decimal("5"),
            scrap_quantity=Decimal("1"),
            unit=self.pcs,
            good_quantity_base=Decimal("5"),
            scrap_quantity_base=Decimal("1"),
            actor=self.owner,
            occurred_at=timezone.make_aware(datetime.combine(self.today, time(12, 0))),
        )

        all_report = production_report(self.month_start, self.today, "day", 5, 20)
        filtered = production_report(
            self.month_start,
            self.today,
            "day",
            5,
            20,
            second_line.code,
        )

        self.assertEqual(filtered["line_filter"]["production_line"], second_line.code)
        self.assertTrue(filtered["line_filter"]["is_filtered"])
        self.assertEqual(filtered["orders_total"], 1)
        self.assertEqual([line["code"] for line in filtered["lines"]], [second_line.code])
        self.assertEqual(
            filtered["total_output_by_uom"],
            [{
                "base_uom": self.pcs.code,
                "base_uom_name_ar": "قطعة",
                "quantity_base": "6.000000000000",
            }],
        )
        self.assertEqual(filtered["total_consumption_by_uom"], [])
        self.assertEqual(filtered["reconciliation"]["output_by_uom"][0]["unallocated_base"], "0.000000000000")
        self.assertIn("shared ledgers", filtered["reconciliation"]["shared_inventory_note"])
        all_output = {
            row["base_uom"]: row for row in all_report["reconciliation"]["output_by_uom"]
        }
        self.assertEqual(
            all_output[self.pcs.code]["total_quantity_base"], "16.000000000000"
        )
        self.assertEqual(
            all_output[self.pcs.code]["attributed_to_lines_base"], "16.000000000000"
        )


class CostingReportTests(ConsolidatedReportFixture):
    def test_persisted_costing_components_and_reconciliation_are_returned(self):
        report = costing_report(self.month_start, self.today, 5, 20)
        row = report["periods"][0]
        self.assertEqual(row["entry_count"], 1)
        self.assertEqual(row["actual_material_cost"], "100.00")
        self.assertEqual(row["material_cost_assigned_to_good"], "80.00")
        self.assertEqual(row["waste_material_cost"], "20.00")
        self.assertEqual(row["overhead_allocation_total"], "20.00")
        self.assertEqual(row["total_production_cost"], "100.00")
        self.assertTrue(row["reconciliation"]["materials_reconcile"])
        self.assertTrue(row["reconciliation"]["cost_total_reconciles"])
        self.assertTrue(row["reconciliation"]["entry_total_reconciles"])
        self.assertFalse(row["review_required"])
        self.assertEqual(row["good_quantities_by_uom"][0]["base_uom"], self.pcs.code)
        entry = report["latest_period_entries"][0]
        self.assertEqual(entry["cost_per_good_unit"], "12.5000")
        self.assertEqual(entry["base_uom"], self.pcs.code)
        self.assertEqual(len(report["latest_order_entries"]), 1)

    def test_missing_utilities_and_incomplete_entries_are_reported_not_invented(self):
        CostingPeriod.objects.filter(pk=self.costing.pk).update(
            electricity_recorded=False,
            water_recorded=False,
            is_complete=False,
            missing_data_notes="نقص كهرباء\nنقص مياه",
        )
        ProductCostingEntry.objects.filter(period=self.costing).update(
            calculation_status="INCOMPLETE"
        )
        report = costing_report(self.month_start, self.today, 5, 20)
        row = report["periods"][0]
        self.assertEqual(row["missing_required_expenses"], ["ELECTRICITY", "WATER"])
        self.assertTrue(row["review_required"])
        self.assertTrue(row["finalization_blocked"])
        self.assertEqual(row["incomplete_entries_count"], 1)
        self.assertEqual(row["entries_pending_review_value"], "100.00")
        self.assertFalse(row["fallback_allocation_amount_available"])
        self.assertEqual(row["missing_data_notes_list"], ["نقص كهرباء", "نقص مياه"])
        self.assertEqual(report["review_summary"]["incomplete_periods"], 1)
        self.assertEqual(report["review_summary"]["incomplete_entries"], 1)

    def test_report_does_not_recalculate_stored_period_total(self):
        CostingPeriod.objects.filter(pk=self.costing.pk).update(
            total_production_cost=Decimal("999.99")
        )
        report = costing_report(self.month_start, self.today, 5, 20)
        row = report["periods"][0]
        self.assertEqual(row["total_production_cost"], "999.99")
        self.assertTrue(row["reconciliation"]["entry_total_reconciles"] is False)
        self.assertFalse(row["reconciliation"]["cost_total_reconciles"])
        self.assertEqual(report["latest_period_entries"][0]["cost_per_good_unit"], "12.5000")


class ReportWebTests(ConsolidatedReportFixture):
    def test_staff_report_pages_render_and_are_protected(self):
        self.client.force_login(self.owner)
        params = self.period_kwargs()
        for name in (
            "reports:overview",
            "reports:sales",
            "reports:purchases",
            "reports:inventory",
            "reports:production",
            "reports:costing",
            "reports:profit",
        ):
            response = self.client.get(reverse(name), params)
            self.assertEqual(response.status_code, 200, name)
        stock = self.client.get(
            reverse("reports:inventory"), {**params, "q": self.finished.sku}
        )
        self.assertContains(stock, self.finished.sku)
        self.assertEqual(
            [row["sku"] for row in stock.context["stock_rows"]], [self.finished.sku]
        )

    def test_production_line_filter_and_invalid_line_fallback(self):
        self.client.force_login(self.owner)
        params = self.period_kwargs()
        response = self.client.get(
            reverse("reports:production"), {**params, "line": self.line.code}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_line"], self.line.code)
        self.assertTrue(response.context["production"]["line_filter"]["is_filtered"])
        self.assertContains(response, "كل الخطوط")

        invalid = self.client.get(
            reverse("reports:production"), {**params, "line": "missing-line"}
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertEqual(invalid.context["selected_line"], "")
        self.assertFalse(invalid.context["production"]["line_filter"]["is_filtered"])
        self.assertContains(invalid, "خط الإنتاج المحدد غير موجود")

    def test_report_pages_require_staff(self):
        response = self.client.get(reverse("reports:overview"))
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.non_staff)
        response = self.client.get(reverse("reports:overview"))
        self.assertEqual(response.status_code, 403)

    def test_profit_report_exposes_management_result_and_missing_data_warnings(self):
        MonthlyProfitPeriod.objects.create(
            year=self.today.year,
            month=self.today.month,
            status=MonthlyProfitPeriod.Status.DRAFT,
            is_complete=False,
            cogs_complete=False,
            labor_recorded=False,
            electricity_recorded=False,
            maintenance_recorded=False,
            other_recorded=False,
            missing_data_notes="بيانات تشغيلية ناقصة للاختبار.",
        )
        self.client.force_login(self.owner)
        params = self.period_kwargs()
        response = self.client.get(reverse("reports:profit"), params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_report"], "profit")
        self.assertEqual(response.context["profit"]["periods_count"], 1)
        self.assertFalse(response.context["profit"]["accounting_final"])
        self.assertContains(response, "نتيجة الشهر")
        self.assertContains(response, "الربح المتبقي بعد تغطية الخسائر")
        self.assertNotContains(response, "net_profit")

        overview = self.client.get(reverse("reports:overview"), params)
        self.assertEqual(overview.status_code, 200)
        codes = {row["code"] for row in overview.context["data_quality"]}
        self.assertIn("PROFIT_MISSING_OPERATING_EXPENSES", codes)
        self.assertIn("PROFIT_INCOMPLETE_COGS", codes)
        self.assertIn("PROFIT_INCOMPLETE_PERIODS", codes)
        self.assertIn("PROFIT_MANAGEMENT_RESULT_ONLY", codes)

    def test_invalid_period_falls_back_without_server_error(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("reports:overview"),
            {"start_date": "2026-12-31", "end_date": "2026-01-01"},
        )
        self.assertEqual(response.status_code, 200)
