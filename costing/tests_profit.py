from calendar import monthrange
from datetime import date
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.models import Product, ProductCategory, UnitOfMeasure
from costing.models import (
    CostingPeriod,
    MonthlyExpense,
    MonthlyProfitCOGSLine,
    MonthlyProfitPeriod,
    ProductCostingEntry,
)
from costing.profit_services import (
    calculate_monthly_profit,
    finalize_monthly_profit,
    reopen_monthly_profit,
    reverse_monthly_expense,
)
from costing.services import finalize_costing_period, reopen_costing_period
from reports.services import profit_report
from inventory.models import StockMovement, StorageLocation, Warehouse
from inventory.services import post_movement, reverse_movement
from manufacturing.models import ProductionLine
from sales.models import Customer, SalesOrder
from sales.services import (
    cancel_order as cancel_sales_order,
    confirm_order as confirm_sales_order,
    create_shipment,
    save_draft_order,
)


class ProfitFixtureMixin:
    def setup_profit_fixture(self):
        suffix = uuid.uuid4().hex[:8]
        User = get_user_model()
        from django.contrib.auth.models import Permission
        self.owner = User.objects.create_user(
            username=f"profit-owner-{suffix}",
            password="test-password",
            is_staff=True,
        )
        self.owner.groups.add(Group.objects.get_or_create(name="ERP Owners")[0])
        perms = Permission.objects.filter(
            content_type__app_label__in=[
                "manufacturing", "inventory", "costing", "purchasing", "sales"
            ]
        )
        self.owner.user_permissions.set(perms)
        self.viewer = User.objects.create_user(
            username=f"profit-viewer-{suffix}",
            password="test-password",
            is_staff=False,
        )
        self.pcs = UnitOfMeasure.objects.create(
            code=f"P{suffix}", name_ar="قطعة", dimension="count"
        )
        self.finished_category = ProductCategory.objects.get_or_create(
            code="finished-goods", defaults={"name_ar": "منتجات تامة"}
        )[0]
        self.product = Product.objects.create(
            sku=f"FG-{suffix}",
            name_ar="منتج اختبار الأرباح",
            category=self.finished_category,
            base_uom=self.pcs,
            available_to_customers=True,
        )
        self.product_b = Product.objects.create(
            sku=f"FG2-{suffix}",
            name_ar="منتج اختبار ثانٍ",
            category=self.finished_category,
            base_uom=self.pcs,
            available_to_customers=True,
        )
        self.warehouse = Warehouse.objects.create(
            code=f"WH-{suffix}", name_ar="مخزن الأرباح"
        )
        self.location = StorageLocation.objects.create(
            warehouse=self.warehouse,
            code=f"LOC-{suffix}",
            name_ar="موقع الشحن",
        )
        self.line = ProductionLine.objects.create(
            code=f"LINE-{suffix}", name_ar="خط الأرباح"
        )
        self.customer = Customer.objects.create(
            code=f"CUST-{suffix}",
            name_ar="عميل الأرباح",
            created_by=self.owner,
        )
        for product in (self.product, self.product_b):
            post_movement(
                reference=f"OPEN-{product.sku}",
                product=product,
                source_location=None,
                destination_location=self.location,
                quantity=Decimal("5000"),
                unit=self.pcs,
                actor=self.owner,
                movement_type=StockMovement.Type.RECEIPT,
                notes="افتتاحي لاختبار الأرباح",
            )
        self._order_counter = 0

    def month_dates(self, year, month):
        return date(year, month, 1), date(year, month, monthrange(year, month)[1])

    def create_cost_period(self, year, month, *, cost_per_unit=Decimal("70.0000"), finalize=False):
        start, end = self.month_dates(year, month)
        period = CostingPeriod.objects.create(
            year=year,
            month=month,
            start_date=start,
            end_date=end,
            status=CostingPeriod.Status.DRAFT,
            is_complete=True,
            labor_recorded=True,
            electricity_recorded=True,
            water_recorded=True,
            calculated_at=timezone.now(),
            calculated_by=self.owner,
        )
        ProductCostingEntry.objects.create(
            period=period,
            product=self.product,
            production_line=self.line,
            good_units_produced=Decimal("1000.0000"),
            actual_material_cost=cost_per_unit * Decimal("1000"),
            material_cost_assigned_to_good=cost_per_unit * Decimal("1000"),
            allocated_labor_cost=Decimal("0.00"),
            allocated_electricity_cost=Decimal("0.00"),
            allocated_water_cost=Decimal("0.00"),
            allocated_other_cost=Decimal("0.00"),
            total_production_cost=cost_per_unit * Decimal("1000"),
            cost_per_good_unit=cost_per_unit,
            material_cost_per_good_unit=cost_per_unit,
            calculation_status="COMPLETE",
        )
        if finalize:
            self.finalize_cost_period(period)
        return period

    def finalize_cost_period(self, period):
        CostingPeriod.objects.filter(pk=period.pk).update(
            status=CostingPeriod.Status.FINALIZED,
            finalized_at=timezone.now(),
            finalized_by=self.owner,
        )
        period.refresh_from_db()
        return period

    def add_expense(
        self,
        year,
        month,
        expense_type,
        amount,
        *,
        status=MonthlyExpense.Status.ACTIVE,
        scope=MonthlyExpense.AllocationScope.FACTORY,
        production_line=None,
        description=None,
        reference="",
    ):
        if scope == MonthlyExpense.AllocationScope.LINE and production_line is None:
            production_line = self.line
        return MonthlyExpense.objects.create(
            year=year,
            month=month,
            expense_type=expense_type,
            amount=Decimal(amount),
            scope=scope,
            production_line=production_line if scope == MonthlyExpense.AllocationScope.LINE else None,
            description=description or f"{expense_type} {year}/{month}",
            reference=reference,
            expense_date=date(year, month, 1),
            status=status,
            created_by=self.owner,
            updated_by=self.owner,
        )

    def add_all_operating_expenses(self, year, month, labor, electricity, maintenance, other):
        self.add_expense(year, month, MonthlyExpense.ExpenseType.LABOR, labor)
        self.add_expense(year, month, MonthlyExpense.ExpenseType.ELECTRICITY, electricity)
        self.add_expense(year, month, MonthlyExpense.ExpenseType.MAINTENANCE, maintenance)
        self.add_expense(year, month, MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL, other)

    def ship(self, year, month, quantity, price, *, product=None, shipment_day=None):
        product = product or self.product
        self._order_counter += 1
        day = shipment_day or 15
        shipment_date = date(year, month, day)
        order = save_draft_order(
            actor=self.owner,
            customer=self.customer,
            order_date=shipment_date,
            lines=[
                {
                    "product": product,
                    "quantity": Decimal(quantity),
                    "unit": self.pcs,
                    "unit_price": Decimal(price),
                    "notes": "",
                }
            ],
        )
        confirm_sales_order(order=order, actor=self.owner)
        order_line = order.lines.get()
        shipment = create_shipment(
            order=order,
            actor=self.owner,
            reference=f"SHIP-{uuid.uuid4().hex[:12].upper()}",
            shipment_date=shipment_date,
            source_location=self.location,
            lines=[
                {
                    "order_line": order_line,
                    "quantity": Decimal(quantity),
                    "unit": self.pcs,
                }
            ],
        )
        return shipment


class MonthlyProfitScenarioTests(ProfitFixtureMixin, TestCase):
    def setUp(self):
        self.setup_profit_fixture()

    def test_required_loss_then_recovery_scenario_across_year_boundary(self):
        cost = self.create_cost_period(2025, 12)
        self.add_all_operating_expenses(
            2025, 12, Decimal("30000"), Decimal("20000"), Decimal("10000"), Decimal("10000")
        )
        self.finalize_cost_period(cost)
        self.ship(2025, 12, "1000", "120.00")
        december = calculate_monthly_profit(year=2025, month=12, actor=self.owner)
        self.assertEqual(december.revenue, Decimal("120000.00"))
        self.assertEqual(december.cogs, Decimal("70000.00"))
        self.assertEqual(december.gross_profit, Decimal("50000.00"))
        self.assertEqual(december.monthly_result, Decimal("-20000.00"))
        self.assertEqual(december.closing_accumulated_loss, Decimal("20000.00"))
        self.assertEqual(december.profit_after_loss_recovery, Decimal("0.00"))
        finalize_monthly_profit(period=december, actor=self.owner)

        cost = self.create_cost_period(2026, 1)
        self.add_all_operating_expenses(
            2026, 1, Decimal("20000"), Decimal("10000"), Decimal("5000"), Decimal("10000")
        )
        self.finalize_cost_period(cost)
        self.ship(2026, 1, "1000", "150.00")
        january = calculate_monthly_profit(year=2026, month=1, actor=self.owner)
        self.assertEqual(january.revenue, Decimal("150000.00"))
        self.assertEqual(january.cogs, Decimal("70000.00"))
        self.assertEqual(january.monthly_result, Decimal("35000.00"))
        self.assertEqual(january.opening_accumulated_loss, Decimal("20000.00"))
        self.assertEqual(january.loss_recovered, Decimal("20000.00"))
        self.assertEqual(january.closing_accumulated_loss, Decimal("0.00"))
        self.assertEqual(january.profit_after_loss_recovery, Decimal("15000.00"))
        finalize_monthly_profit(period=january, actor=self.owner)
        january.refresh_from_db()
        self.assertEqual(january.status, MonthlyProfitPeriod.Status.FINALIZED)

    def test_partial_recovery_and_no_remaining_profit(self):
        cost = self.create_cost_period(2025, 12)
        self.add_all_operating_expenses(
            2025, 12, Decimal("30000"), Decimal("20000"), Decimal("10000"), Decimal("10000")
        )
        self.finalize_cost_period(cost)
        self.ship(2025, 12, "1000", "120.00")
        december = calculate_monthly_profit(year=2025, month=12, actor=self.owner)
        finalize_monthly_profit(period=december, actor=self.owner)

        cost = self.create_cost_period(2026, 1)
        self.add_all_operating_expenses(
            2026, 1, Decimal("10000"), Decimal("5000"), Decimal("3000"), Decimal("2000")
        )
        self.finalize_cost_period(cost)
        self.ship(2026, 1, "1000", "100.00")
        january = calculate_monthly_profit(year=2026, month=1, actor=self.owner)
        self.assertEqual(january.monthly_result, Decimal("10000.00"))
        self.assertEqual(january.opening_accumulated_loss, Decimal("20000.00"))
        self.assertEqual(january.loss_recovered, Decimal("10000.00"))
        self.assertEqual(january.closing_accumulated_loss, Decimal("10000.00"))
        self.assertEqual(january.profit_after_loss_recovery, Decimal("0.00"))

    def test_consecutive_losses_zero_result_and_carry_forward(self):
        cost = self.create_cost_period(2025, 12)
        self.add_all_operating_expenses(
            2025, 12, Decimal("30000"), Decimal("20000"), Decimal("10000"), Decimal("10000")
        )
        self.finalize_cost_period(cost)
        self.ship(2025, 12, "1000", "120.00")
        december = calculate_monthly_profit(year=2025, month=12, actor=self.owner)
        finalize_monthly_profit(period=december, actor=self.owner)

        cost = self.create_cost_period(2026, 1)
        self.add_all_operating_expenses(
            2026, 1, Decimal("20000"), Decimal("10000"), Decimal("5000"), Decimal("10000")
        )
        self.finalize_cost_period(cost)
        self.ship(2026, 1, "1000", "110.00")
        january = calculate_monthly_profit(year=2026, month=1, actor=self.owner)
        self.assertEqual(january.monthly_result, Decimal("-5000.00"))
        self.assertEqual(january.closing_accumulated_loss, Decimal("25000.00"))
        finalize_monthly_profit(period=january, actor=self.owner)

        cost = self.create_cost_period(2026, 2)
        self.add_all_operating_expenses(
            2026, 2, Decimal("30000"), Decimal("20000"), Decimal("10000"), Decimal("10000")
        )
        self.finalize_cost_period(cost)
        self.ship(2026, 2, "1000", "140.00")
        february = calculate_monthly_profit(year=2026, month=2, actor=self.owner)
        self.assertEqual(february.monthly_result, Decimal("0.00"))
        self.assertEqual(february.opening_accumulated_loss, Decimal("25000.00"))
        self.assertEqual(february.loss_recovered, Decimal("0.00"))
        self.assertEqual(february.closing_accumulated_loss, Decimal("25000.00"))
        self.assertEqual(february.profit_after_loss_recovery, Decimal("0.00"))

    def test_profitable_month_without_prior_loss_keeps_all_profit(self):
        cost = self.create_cost_period(2026, 3)
        self.add_all_operating_expenses(
            2026, 3, Decimal("5000"), Decimal("3000"), Decimal("1000"), Decimal("1000")
        )
        self.finalize_cost_period(cost)
        self.ship(2026, 3, "1000", "90.00")
        period = calculate_monthly_profit(year=2026, month=3, actor=self.owner)
        self.assertEqual(period.monthly_result, Decimal("10000.00"))
        self.assertEqual(period.opening_accumulated_loss, Decimal("0.00"))
        self.assertEqual(period.loss_recovered, Decimal("0.00"))
        self.assertEqual(period.closing_accumulated_loss, Decimal("0.00"))
        self.assertEqual(period.profit_after_loss_recovery, Decimal("10000.00"))

    def test_missing_expense_is_not_zero_and_explicit_zero_is_complete(self):
        period = self.create_cost_period(2026, 4, finalize=False)
        self.add_expense(2026, 4, MonthlyExpense.ExpenseType.LABOR, "1000")
        self.add_expense(2026, 4, MonthlyExpense.ExpenseType.ELECTRICITY, "500")
        self.add_expense(2026, 4, MonthlyExpense.ExpenseType.MAINTENANCE, "0")
        self.finalize_cost_period(period)
        self.ship(2026, 4, "100", "90.00")
        draft = calculate_monthly_profit(year=2026, month=4, actor=self.owner)
        self.assertFalse(draft.other_recorded)
        self.assertFalse(draft.is_complete)
        self.assertIn("لم تُسجل", draft.missing_data_notes)
        self.assertIn("مصاريف تشغيلية أخرى", draft.missing_data_notes)
        with self.assertRaises(ValidationError):
            finalize_monthly_profit(period=draft, actor=self.owner)

        CostingPeriod.objects.filter(pk=period.pk).update(status=CostingPeriod.Status.DRAFT)
        self.add_expense(2026, 4, MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL, "0")
        self.finalize_cost_period(period)
        complete = calculate_monthly_profit(year=2026, month=4, actor=self.owner)
        self.assertTrue(complete.other_recorded)
        self.assertTrue(complete.is_complete)
        self.assertEqual(complete.other_expense, Decimal("0.00"))
        finalize_monthly_profit(period=complete, actor=self.owner)

    def test_water_is_excluded_from_agreed_operating_expenses(self):
        period = self.create_cost_period(2026, 5, finalize=False)
        self.add_all_operating_expenses(
            2026, 5, Decimal("1000"), Decimal("500"), Decimal("300"), Decimal("200")
        )
        self.add_expense(2026, 5, MonthlyExpense.ExpenseType.WATER, "999")
        self.finalize_cost_period(period)
        self.ship(2026, 5, "10", "100.00")
        result = calculate_monthly_profit(year=2026, month=5, actor=self.owner)
        self.assertEqual(result.operating_expenses_total, Decimal("2000.00"))
        self.assertIn("المياه", result.missing_data_notes)
        self.assertNotIn("999", result.missing_data_notes)

    def test_unsold_inventory_is_not_expensed_as_cogs(self):
        cost = self.create_cost_period(2026, 6)
        self.add_all_operating_expenses(
            2026, 6, Decimal("1000"), Decimal("500"), Decimal("300"), Decimal("200")
        )
        self.finalize_cost_period(cost)
        self.ship(2026, 6, "100", "100.00")
        result = calculate_monthly_profit(year=2026, month=6, actor=self.owner)
        self.assertEqual(result.revenue, Decimal("10000.00"))
        self.assertEqual(result.cogs, Decimal("7000.00"))
        self.assertEqual(result.gross_profit, Decimal("3000.00"))
        self.assertEqual(result.cogs_lines.get().quantity_sold_base, Decimal("100.000000000000"))

    def test_reversed_shipment_and_cancelled_order_are_excluded(self):
        cost = self.create_cost_period(2026, 7)
        self.add_all_operating_expenses(
            2026, 7, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        self.finalize_cost_period(cost)
        shipment = self.ship(2026, 7, "10", "100.00")
        reverse_movement(
            movement=shipment.lines.get().movement,
            reference=f"REV-{uuid.uuid4().hex[:10]}",
            actor=self.owner,
            reason="اختبار عكس شحن",
        )
        cancelled = save_draft_order(
            actor=self.owner,
            customer=self.customer,
            order_date=date(2026, 7, 10),
            lines=[
                {
                    "product": self.product,
                    "quantity": Decimal("10"),
                    "unit": self.pcs,
                    "unit_price": Decimal("100.00"),
                    "notes": "",
                }
            ],
        )
        cancel_sales_order(order=cancelled, actor=self.owner, reason="اختبار إلغاء")
        result = calculate_monthly_profit(year=2026, month=7, actor=self.owner)
        self.assertEqual(result.revenue, Decimal("0.00"))
        self.assertEqual(result.cogs, Decimal("0.00"))
        self.assertEqual(result.active_shipment_count, 0)
        self.assertTrue(result.is_complete)

    def test_credit_sales_are_recognized_on_shipment_not_collection(self):
        cost = self.create_cost_period(2026, 8)
        self.add_all_operating_expenses(
            2026, 8, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        self.finalize_cost_period(cost)
        shipment = self.ship(2026, 8, "10", "100.00")
        self.assertIsNotNone(shipment)
        result = calculate_monthly_profit(year=2026, month=8, actor=self.owner)
        self.assertEqual(result.revenue, Decimal("1000.00"))
        self.assertEqual(result.monthly_result, Decimal("300.00"))
        # No payment/collection model exists, so shipment recognition is the
        # documented basis and no collection amount is fabricated.
        self.assertEqual(result.active_shipment_count, 1)

    def test_labor_appears_once_not_twice_and_does_not_enter_cogs(self):
        period = self.create_cost_period(2026, 9, finalize=False)
        entry = period.product_entries.get()
        entry.allocated_labor_cost = Decimal("1000.00")
        entry.total_production_cost = entry.total_production_cost + Decimal("1000.00")
        entry.save(update_fields=["allocated_labor_cost", "total_production_cost"])
        self.add_all_operating_expenses(
            2026, 9, Decimal("1000"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        self.finalize_cost_period(period)
        self.ship(2026, 9, "10", "100.00")
        result = calculate_monthly_profit(year=2026, month=9, actor=self.owner)
        self.assertEqual(result.cogs, Decimal("700.00"))
        self.assertEqual(result.labor_expense, Decimal("1000.00"))
        self.assertEqual(result.gross_profit, Decimal("300.00"))
        self.assertEqual(result.monthly_result, Decimal("-700.00"))

    def test_expenses_count_once_even_with_line_scope_and_multiple_records(self):
        cost = self.create_cost_period(2026, 10)
        self.add_expense(
            2026, 10, MonthlyExpense.ExpenseType.LABOR, "1000",
            scope=MonthlyExpense.AllocationScope.LINE,
        )
        self.add_expense(
            2026, 10, MonthlyExpense.ExpenseType.LABOR, "500",
            scope=MonthlyExpense.AllocationScope.FACTORY,
        )
        self.add_expense(2026, 10, MonthlyExpense.ExpenseType.ELECTRICITY, "100")
        self.add_expense(2026, 10, MonthlyExpense.ExpenseType.MAINTENANCE, "50")
        self.add_expense(2026, 10, MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL, "25")
        self.finalize_cost_period(cost)
        self.ship(2026, 10, "10", "100.00")
        result = calculate_monthly_profit(year=2026, month=10, actor=self.owner)
        self.assertEqual(result.labor_expense, Decimal("1500.00"))
        self.assertEqual(result.operating_expenses_total, Decimal("1675.00"))
        self.assertEqual(result.expense_record_count, 5)
        self.assertEqual(result.monthly_result, Decimal("-1375.00"))

    def test_multiple_production_lines_are_summed_without_duplicate_allocation(self):
        line_b = ProductionLine.objects.create(
            code=f"{self.line.code}-B", name_ar="خط أرباح ثانٍ"
        )
        cost = self.create_cost_period(2026, 10)
        self.add_expense(
            2026, 10, MonthlyExpense.ExpenseType.LABOR, "1000",
            scope=MonthlyExpense.AllocationScope.LINE,
        )
        self.add_expense(
            2026, 10, MonthlyExpense.ExpenseType.LABOR, "250",
            scope=MonthlyExpense.AllocationScope.LINE,
            production_line=line_b,
        )
        self.add_all_operating_expenses(
            2026, 10, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        self.finalize_cost_period(cost)
        self.ship(2026, 10, "10", "100.00")
        result = calculate_monthly_profit(year=2026, month=10, actor=self.owner)
        self.assertEqual(result.labor_expense, Decimal("1250.00"))
        self.assertEqual(result.operating_expenses_total, Decimal("1250.00"))
        self.assertEqual(result.expense_record_count, 6)
        self.assertEqual(result.cogs, Decimal("700.00"))
        self.assertEqual(result.monthly_result, Decimal("-950.00"))

    def test_missing_costing_is_not_treated_as_zero_and_blocks_finalization(self):
        cost = self.create_cost_period(2026, 11)
        self.add_all_operating_expenses(
            2026, 11, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        self.finalize_cost_period(cost)
        # A second product is shipped, but no persisted costing entry exists for it.
        self.ship(2026, 11, "10", "100.00", product=self.product_b)
        result = calculate_monthly_profit(year=2026, month=11, actor=self.owner)
        line = result.cogs_lines.get(product=self.product_b)
        self.assertEqual(line.calculation_status, MonthlyProfitCOGSLine.CalculationStatus.MISSING_COST)
        self.assertEqual(line.cogs, Decimal("0.00"))
        self.assertFalse(result.cogs_complete)
        self.assertFalse(result.is_complete)
        with self.assertRaises(ValidationError):
            finalize_monthly_profit(period=result, actor=self.owner)

    def test_incomplete_costing_is_flagged_and_blocks_finalization(self):
        period = self.create_cost_period(2026, 12, finalize=False)
        ProductCostingEntry.objects.filter(period=period).update(
            calculation_status="INCOMPLETE", material_cost_per_good_unit=Decimal("70.0000")
        )
        self.add_all_operating_expenses(
            2026, 12, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        self.ship(2026, 12, "10", "100.00")
        CostingPeriod.objects.filter(pk=period.pk).update(
            status=CostingPeriod.Status.FINALIZED, is_complete=False
        )
        result = calculate_monthly_profit(year=2026, month=12, actor=self.owner)
        self.assertFalse(result.cogs_complete)
        self.assertFalse(result.is_complete)
        with self.assertRaises(ValidationError):
            finalize_monthly_profit(period=result, actor=self.owner)

    def test_decimal_precision_is_rounded_to_money_at_line_level(self):
        cost = self.create_cost_period(2026, 1, cost_per_unit=Decimal("0.3333"))
        self.add_all_operating_expenses(
            2026, 1, Decimal("0.01"), Decimal("0.01"), Decimal("0.00"), Decimal("0.00")
        )
        self.finalize_cost_period(cost)
        self.ship(2026, 1, "3", "1.0000")
        result = calculate_monthly_profit(year=2026, month=1, actor=self.owner)
        self.assertEqual(result.cogs, Decimal("1.00"))
        self.assertEqual(result.revenue, Decimal("3.00"))
        self.assertEqual(result.gross_profit, Decimal("2.00"))
        self.assertEqual(result.monthly_result, Decimal("1.98"))

    def test_finalization_recalculates_a_stale_draft_before_freezing_it(self):
        cost = self.create_cost_period(2026, 2)
        self.add_all_operating_expenses(
            2026, 2, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        self.finalize_cost_period(cost)
        result = calculate_monthly_profit(year=2026, month=2, actor=self.owner)
        self.assertEqual(result.revenue, Decimal("0.00"))
        # A shipment arrives after the draft was calculated but before it was
        # finalized. Finalization must not freeze the stale zero-revenue result.
        self.ship(2026, 2, "10", "100.00")
        finalized = finalize_monthly_profit(period=result, actor=self.owner)
        self.assertEqual(finalized.status, MonthlyProfitPeriod.Status.FINALIZED)
        self.assertEqual(finalized.revenue, Decimal("1000.00"))
        self.assertEqual(finalized.cogs, Decimal("700.00"))
        self.assertEqual(finalized.monthly_result, Decimal("300.00"))

    def test_finalized_month_blocks_late_shipments_and_shipment_reversal(self):
        cost = self.create_cost_period(2026, 3)
        self.add_all_operating_expenses(
            2026, 3, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        self.finalize_cost_period(cost)
        shipment = self.ship(2026, 3, "10", "100.00")
        period = calculate_monthly_profit(year=2026, month=3, actor=self.owner)
        finalize_monthly_profit(period=period, actor=self.owner)

        with self.assertRaises(ValidationError):
            self.ship(2026, 3, "1", "100.00")
        with self.assertRaises(ValidationError):
            reverse_movement(
                movement=shipment.lines.get().movement,
                reference=f"REV-{uuid.uuid4().hex[:10]}",
                actor=self.owner,
                reason="عكس بعد الاعتماد",
            )

        reopen_monthly_profit(period=period, actor=self.owner, reason="تصحيح شحنة")
        reverse_movement(
            movement=shipment.lines.get().movement,
            reference=f"REV-{uuid.uuid4().hex[:10]}",
            actor=self.owner,
            reason="تصحيح بعد إعادة الفتح",
        )
        corrected = calculate_monthly_profit(year=2026, month=3, actor=self.owner)
        self.assertEqual(corrected.revenue, Decimal("0.00"))
        self.assertEqual(corrected.active_shipment_count, 0)


class MonthlyProfitAuthorizationAndCorrectionTests(ProfitFixtureMixin, TestCase):
    def setUp(self):
        self.setup_profit_fixture()

    def test_permissions_and_duplicate_finalization_protection(self):
        cost = self.create_cost_period(2026, 1)
        self.add_all_operating_expenses(
            2026, 1, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        self.finalize_cost_period(cost)
        self.ship(2026, 1, "10", "100.00")
        with self.assertRaises(ValidationError):
            calculate_monthly_profit(year=2026, month=1, actor=self.viewer)
        result = calculate_monthly_profit(year=2026, month=1, actor=self.owner)
        with self.assertRaises(ValidationError):
            finalize_monthly_profit(period=result, actor=self.viewer)
        finalize_monthly_profit(period=result, actor=self.owner)
        with self.assertRaises(ValidationError):
            finalize_monthly_profit(period=result, actor=self.owner)
        with self.assertRaises(ValidationError):
            calculate_monthly_profit(year=2026, month=1, actor=self.owner)
        with self.assertRaises(ValidationError):
            result.monthly_result = Decimal("999.00")
            result.save()
        with self.assertRaises(ValidationError):
            MonthlyProfitPeriod.objects.filter(pk=result.pk).update(
                monthly_result=Decimal("999.00")
            )
        with self.assertRaises(ValidationError):
            result.delete()
        cogs_line = result.cogs_lines.get()
        with self.assertRaises(ValidationError):
            cogs_line.delete()
        with self.assertRaises(ValidationError):
            MonthlyProfitCOGSLine.objects.filter(pk=cogs_line.pk).update(
                cogs=Decimal("0.00")
            )

    def test_finalized_period_protects_expense_edit_and_requires_reason_to_reopen(self):
        cost = self.create_cost_period(2026, 1)
        expense = self.add_expense(
            2026, 1, MonthlyExpense.ExpenseType.LABOR, "100"
        )
        self.add_all_operating_expenses(
            2026, 1, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        self.finalize_cost_period(cost)
        self.ship(2026, 1, "10", "100.00")
        result = calculate_monthly_profit(year=2026, month=1, actor=self.owner)
        finalize_monthly_profit(period=result, actor=self.owner)
        with self.assertRaises(ValidationError):
            MonthlyExpense.objects.create(
                year=2026, month=1,
                expense_type=MonthlyExpense.ExpenseType.ELECTRICITY,
                amount=Decimal("1"),
                scope=MonthlyExpense.AllocationScope.FACTORY,
                description="محاولة تعديل بعد الاعتماد",
                created_by=self.owner,
            )
        with self.assertRaises(ValidationError):
            reverse_monthly_expense(expense=expense, actor=self.owner, reason="")
        reopened = reopen_monthly_profit(
            period=result, actor=self.owner, reason="تصحيح مصروف موثق"
        )
        self.assertEqual(reopened.status, MonthlyProfitPeriod.Status.DRAFT)
        self.assertEqual(reopened.reopen_reason, "تصحيح مصروف موثق")

    def test_reopen_blocks_if_later_period_is_finalized(self):
        cost = self.create_cost_period(2025, 12)
        self.add_all_operating_expenses(
            2025, 12, Decimal("30000"), Decimal("20000"), Decimal("10000"), Decimal("10000")
        )
        self.finalize_cost_period(cost)
        self.ship(2025, 12, "1000", "120.00")
        december = calculate_monthly_profit(year=2025, month=12, actor=self.owner)
        finalize_monthly_profit(period=december, actor=self.owner)

        cost = self.create_cost_period(2026, 1)
        self.add_all_operating_expenses(
            2026, 1, Decimal("10000"), Decimal("5000"), Decimal("3000"), Decimal("2000")
        )
        self.finalize_cost_period(cost)
        self.ship(2026, 1, "1000", "100.00")
        january = calculate_monthly_profit(year=2026, month=1, actor=self.owner)
        finalize_monthly_profit(period=january, actor=self.owner)

        with self.assertRaises(ValidationError):
            reopen_monthly_profit(
                period=december, actor=self.owner, reason="محاولة تعديل شهر أقدم"
            )
        reopen_monthly_profit(
            period=january, actor=self.owner, reason="إعادة فتح الشهر اللاحق أولًا"
        )
        reopen_monthly_profit(
            period=december, actor=self.owner, reason="تصحيح موثق للشهر الأقدم"
        )

    def test_authorized_correction_recalculates_subsequent_balances(self):
        cost = self.create_cost_period(2025, 12)
        self.add_all_operating_expenses(
            2025, 12, Decimal("30000"), Decimal("20000"), Decimal("10000"), Decimal("10000")
        )
        self.finalize_cost_period(cost)
        self.ship(2025, 12, "1000", "120.00")
        december = calculate_monthly_profit(year=2025, month=12, actor=self.owner)
        finalize_monthly_profit(period=december, actor=self.owner)

        cost = self.create_cost_period(2026, 1)
        self.add_all_operating_expenses(
            2026, 1, Decimal("20000"), Decimal("10000"), Decimal("5000"), Decimal("10000")
        )
        self.finalize_cost_period(cost)
        self.ship(2026, 1, "1000", "150.00")
        january = calculate_monthly_profit(year=2026, month=1, actor=self.owner)
        finalize_monthly_profit(period=january, actor=self.owner)
        self.assertEqual(january.closing_accumulated_loss, Decimal("0.00"))

        # Reopen later month first, then correct the older month's expense.
        reopen_monthly_profit(period=january, actor=self.owner, reason="فتح للتصحيح")
        reopen_monthly_profit(period=december, actor=self.owner, reason="تصحيح مصروف ديسمبر")
        december_cost = CostingPeriod.objects.get(year=2025, month=12)
        reopen_costing_period(
            period=december_cost, actor=self.owner, reason="تصحيح مصروف ديسمبر"
        )
        correction = self.add_expense(
            2025, 12, MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL, "5000",
            description="تصحيح مصروف ديسمبر",
        )
        self.assertEqual(
            correction.expense_type, MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL
        )
        finalize_costing_period(period=december_cost, actor=self.owner)
        december = calculate_monthly_profit(year=2025, month=12, actor=self.owner)
        self.assertEqual(december.monthly_result, Decimal("-25000.00"))
        finalize_monthly_profit(period=december, actor=self.owner)
        january = calculate_monthly_profit(year=2026, month=1, actor=self.owner)
        self.assertEqual(january.opening_accumulated_loss, Decimal("25000.00"))
        self.assertEqual(january.loss_recovered, Decimal("25000.00"))
        self.assertEqual(january.closing_accumulated_loss, Decimal("0.00"))

    def test_reversing_expense_excludes_it_without_deleting_history(self):
        period = self.create_cost_period(2026, 2, finalize=False)
        self.add_all_operating_expenses(
            2026, 2, Decimal("1000"), Decimal("500"), Decimal("300"), Decimal("200")
        )
        expense = MonthlyExpense.objects.get(
            year=2026, month=2, expense_type=MonthlyExpense.ExpenseType.LABOR
        )
        reverse_monthly_expense(
            expense=expense, actor=self.owner, reason="فاتورة عمالة مكررة"
        )
        self.finalize_cost_period(period)
        self.ship(2026, 2, "10", "100.00")
        result = calculate_monthly_profit(year=2026, month=2, actor=self.owner)
        self.assertEqual(result.labor_expense, Decimal("0.00"))
        self.assertEqual(result.operating_expenses_total, Decimal("1000.00"))
        expense.refresh_from_db()
        self.assertEqual(expense.status, MonthlyExpense.Status.REVERSED)
        self.assertEqual(expense.reversal_reason, "فاتورة عمالة مكررة")
        self.assertTrue(MonthlyExpense.objects.filter(pk=expense.pk).exists())
        # Reversed water/other records remain in the audit list but not totals.
        self.assertEqual(MonthlyExpense.objects.filter(year=2026, month=2).count(), 4)

    def test_cross_period_expense_is_recorded_in_selected_month(self):
        cost = self.create_cost_period(2026, 3)
        self.add_expense(
            2026, 3, MonthlyExpense.ExpenseType.ELECTRICITY, "777",
            description="فاتورة كهرباء عن فترة سابقة",
        )
        self.add_all_operating_expenses(
            2026, 3, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        self.finalize_cost_period(cost)
        self.ship(2026, 3, "10", "100.00")
        result = calculate_monthly_profit(year=2026, month=3, actor=self.owner)
        # The electricity record already exists; adding another 0 record in the
        # helper makes the total 777, not 777 + 0.
        self.assertEqual(result.electricity_expense, Decimal("777.00"))
        self.assertEqual(result.operating_expenses_total, Decimal("777.00"))

    def test_report_and_view_expose_monthly_result_without_calling_it_net_profit(self):
        cost = self.create_cost_period(2026, 4)
        self.add_all_operating_expenses(
            2026, 4, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        self.finalize_cost_period(cost)
        self.ship(2026, 4, "10", "100.00")
        result = calculate_monthly_profit(year=2026, month=4, actor=self.owner)
        finalize_monthly_profit(period=result, actor=self.owner)
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("reports:profit"),
            {"start_date": "2026-04-01", "end_date": "2026-04-30"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "نتيجة الشهر")
        self.assertContains(response, "الربح المتبقي بعد تغطية الخسائر")
        self.assertContains(response, "ليست صافي ربح محاسبيًا نهائيًا")

        response = self.client.get(
            reverse("costing:profit_period_detail", args=[result.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ليست صافي ربح محاسبيًا نهائيًا")

    def test_finalization_blocks_while_an_earlier_profit_period_is_draft(self):
        july_cost = self.create_cost_period(2026, 7)
        self.add_all_operating_expenses(
            2026, 7, Decimal("500"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        self.finalize_cost_period(july_cost)
        self.ship(2026, 7, "10", "100.00")
        july = calculate_monthly_profit(year=2026, month=7, actor=self.owner)
        self.assertEqual(july.monthly_result, Decimal("-200.00"))
        self.assertEqual(july.closing_accumulated_loss, Decimal("200.00"))

        august_cost = self.create_cost_period(2026, 8)
        self.add_all_operating_expenses(
            2026, 8, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        self.finalize_cost_period(august_cost)
        self.ship(2026, 8, "10", "100.00")
        august = calculate_monthly_profit(year=2026, month=8, actor=self.owner)

        with self.assertRaisesMessage(ValidationError, "فترة أرباح سابقة"):
            finalize_monthly_profit(period=august, actor=self.owner)

        finalize_monthly_profit(period=july, actor=self.owner)
        august = finalize_monthly_profit(period=august, actor=self.owner)
        self.assertEqual(august.opening_accumulated_loss, Decimal("200.00"))
        self.assertEqual(august.loss_recovered, Decimal("200.00"))
        self.assertEqual(august.closing_accumulated_loss, Decimal("0.00"))
        self.assertEqual(august.profit_after_loss_recovery, Decimal("100.00"))

    def test_reopening_a_finalized_month_refreshes_later_draft_balances(self):
        december_cost = self.create_cost_period(2025, 12)
        self.add_all_operating_expenses(
            2025, 12, Decimal("30000"), Decimal("20000"), Decimal("10000"), Decimal("10000")
        )
        self.finalize_cost_period(december_cost)
        self.ship(2025, 12, "1000", "120.00")
        december = calculate_monthly_profit(year=2025, month=12, actor=self.owner)
        finalize_monthly_profit(period=december, actor=self.owner)

        january_cost = self.create_cost_period(2026, 1)
        self.add_all_operating_expenses(
            2026, 1, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        self.finalize_cost_period(january_cost)
        self.ship(2026, 1, "1000", "150.00")
        january = calculate_monthly_profit(year=2026, month=1, actor=self.owner)
        self.assertEqual(january.opening_accumulated_loss, Decimal("20000.00"))

        reopen_monthly_profit(
            period=december, actor=self.owner, reason="تصحيح موثق لديسمبر"
        )
        january.refresh_from_db()
        self.assertEqual(january.opening_accumulated_loss, Decimal("0.00"))
        self.assertEqual(january.monthly_result, Decimal("80000.00"))
        self.assertEqual(january.closing_accumulated_loss, Decimal("0.00"))

        finalize_monthly_profit(period=december, actor=self.owner)
        january.refresh_from_db()
        self.assertEqual(january.opening_accumulated_loss, Decimal("20000.00"))
        self.assertEqual(january.loss_recovered, Decimal("20000.00"))
        self.assertEqual(january.profit_after_loss_recovery, Decimal("60000.00"))

    def test_reopening_costing_source_requires_reopening_later_finalized_profit(self):
        december_cost = self.create_cost_period(2025, 12)
        self.finalize_cost_period(december_cost)
        self.add_all_operating_expenses(
            2026, 1, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        self.ship(2026, 1, "10", "100.00")
        january = calculate_monthly_profit(year=2026, month=1, actor=self.owner)
        self.assertEqual(january.cogs, Decimal("700.00"))
        finalize_monthly_profit(period=january, actor=self.owner)

        with self.assertRaisesMessage(ValidationError, "مصدر تكلفة"):
            reopen_costing_period(
                period=december_cost,
                actor=self.owner,
                reason="محاولة تغيير مصدر تكلفة معتمد",
            )
        reopen_monthly_profit(
            period=january, actor=self.owner, reason="فتح نتيجة يناير قبل التصحيح"
        )
        reopened = reopen_costing_period(
            period=december_cost,
            actor=self.owner,
            reason="تصحيح بعد فتح نتيجة يناير",
        )
        self.assertEqual(reopened.status, CostingPeriod.Status.DRAFT)

    def test_owner_profit_api_matches_staff_profit_report(self):
        april_cost = self.create_cost_period(2026, 4)
        self.add_all_operating_expenses(
            2026, 4, Decimal("1000"), Decimal("500"), Decimal("300"), Decimal("200")
        )
        self.finalize_cost_period(april_cost)
        self.ship(2026, 4, "10", "100.00")
        result = calculate_monthly_profit(year=2026, month=4, actor=self.owner)
        finalize_monthly_profit(period=result, actor=self.owner)

        report = profit_report(date(2026, 4, 1), date(2026, 4, 30))
        api = APIClient()
        api.force_authenticate(self.owner)
        response = api.get(
            reverse("api:owner-profit"),
            {"start_date": "2026-04-01", "end_date": "2026-04-30"},
        )
        self.assertEqual(response.status_code, 200)
        api_period = response.data["profit_loss"]["periods"][0]
        report_period = report["periods"][0]
        for field in (
            "revenue",
            "cogs",
            "operating_expenses_total",
            "monthly_result",
            "opening_accumulated_loss",
            "loss_recovered",
            "closing_accumulated_loss",
            "profit_after_loss_recovery",
        ):
            self.assertEqual(api_period[field], report_period[field], field)
        self.assertEqual(api_period["status"], report_period["status"])


class MonthlyProfitWebWorkflowTests(ProfitFixtureMixin, TestCase):
    def setUp(self):
        self.setup_profit_fixture()

    def test_staff_workflow_calculates_finalizes_and_reopens_through_views(self):
        cost = self.create_cost_period(2026, 5)
        self.add_all_operating_expenses(
            2026, 5, Decimal("1000"), Decimal("500"), Decimal("300"), Decimal("200")
        )
        self.finalize_cost_period(cost)
        self.ship(2026, 5, "10", "100.00")
        self.client.force_login(self.owner)

        listing = self.client.get(reverse("costing:profit_period_list"))
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, "الأرباح والخسائر الشهرية")

        calculated = self.client.post(
            reverse("costing:profit_period_calculate"),
            {"year": 2026, "month": 5},
        )
        self.assertEqual(calculated.status_code, 302)
        period = MonthlyProfitPeriod.objects.get(year=2026, month=5)
        self.assertEqual(period.monthly_result, Decimal("-1700.00"))

        detail = self.client.get(
            reverse("costing:profit_period_detail", args=[period.pk])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "اعتماد نتيجة الشهر نهائيًا")

        finalized = self.client.post(
            reverse("costing:profit_period_finalize", args=[period.pk])
        )
        self.assertEqual(finalized.status_code, 302)
        period.refresh_from_db()
        self.assertEqual(period.status, MonthlyProfitPeriod.Status.FINALIZED)

        reopen_page = self.client.get(
            reverse("costing:profit_period_reopen", args=[period.pk])
        )
        self.assertEqual(reopen_page.status_code, 200)
        reopened = self.client.post(
            reverse("costing:profit_period_reopen", args=[period.pk]),
            {"reason": "تصحيح موثق للاختبار"},
        )
        self.assertEqual(reopened.status_code, 302)
        period.refresh_from_db()
        self.assertEqual(period.status, MonthlyProfitPeriod.Status.DRAFT)
        self.assertEqual(period.reopen_reason, "تصحيح موثق للاختبار")

    def test_expense_reversal_page_preserves_history(self):
        self.create_cost_period(2026, 6)
        expense = self.add_expense(
            2026, 6, MonthlyExpense.ExpenseType.LABOR, "250"
        )
        self.client.force_login(self.owner)
        page = self.client.get(reverse("costing:expense_reverse", args=[expense.pk]))
        self.assertEqual(page.status_code, 200)
        response = self.client.post(
            reverse("costing:expense_reverse", args=[expense.pk]),
            {"reason": "تصحيح فاتورة مكررة"},
        )
        self.assertEqual(response.status_code, 302)
        expense.refresh_from_db()
        self.assertEqual(expense.status, MonthlyExpense.Status.REVERSED)
        self.assertTrue(MonthlyExpense.objects.filter(pk=expense.pk).exists())

    def test_repeated_expense_form_submission_is_recorded_once(self):
        self.create_cost_period(2026, 7)
        self.client.force_login(self.owner)
        submission_key = uuid.uuid4()
        payload = {
            "submission_key": str(submission_key),
            "year": 2026,
            "month": 7,
            "expense_type": MonthlyExpense.ExpenseType.LABOR,
            "scope": MonthlyExpense.AllocationScope.FACTORY,
            "production_line": "",
            "product": "",
            "amount": "500.00",
            "expense_date": "2026-07-01",
            "payment_date": "",
            "reference": "INV-IDEMPOTENT",
            "description": "اختبار منع التكرار",
            "notes": "",
        }
        first = self.client.post(reverse("costing:expense_create"), payload)
        second = self.client.post(reverse("costing:expense_create"), payload)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            MonthlyExpense.objects.filter(
                year=2026,
                month=7,
                expense_type=MonthlyExpense.ExpenseType.LABOR,
                submission_key=submission_key,
            ).count(),
            1,
        )
        self.assertEqual(
            MonthlyExpense.objects.filter(
                year=2026, month=7, reference="INV-IDEMPOTENT"
            ).count(),
            1,
        )

    def test_non_staff_cannot_open_or_run_profit_pages(self):
        self.client.force_login(self.viewer)
        self.assertEqual(
            self.client.get(reverse("costing:profit_period_list")).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                reverse("costing:profit_period_calculate"),
                {"year": 2026, "month": 1},
            ).status_code,
            403,
        )
