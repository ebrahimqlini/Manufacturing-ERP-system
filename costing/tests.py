from decimal import Decimal
import calendar
import uuid
from datetime import date, datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product, ProductCategory, ProductUnitConversion, UnitOfMeasure
from inventory.models import StockLedgerEntry, StockMovement, StorageLocation, Warehouse
from inventory.services import post_movement, to_base_quantity
from manufacturing.models import (
    BillOfMaterials,
    BOMComponent,
    FinishedGoodsReceipt,
    MaterialIssue,
    ProductionLine,
    ProductionOperation,
    ProductionOrder,
    ProductionOrderOperation,
    ProductionResult,
    Routing,
    RoutingStep,
)
from manufacturing.services import (
    complete_operation,
    complete_order,
    confirm_no_material_waste,
    issue_material,
    receive_finished_goods,
    record_production_material_waste,
    record_operation_progress,
    record_production_result,
    release_order,
    save_draft_order,
)

from costing.models import (
    CostingPeriod,
    MonthlyExpense,
    ProductCostingEntry,
    ProductSellingPrice,
    RawMaterialPrice,
    RollYieldStandard,
)
from costing.services import (
    calculate_monthly_costing,
    finalize_costing_period,
    get_material_price,
    reopen_costing_period,
)


class CostingFixtureMixin:
    def create_costing_fixture(self):
        User = get_user_model()
        from django.contrib.auth.models import Permission
        suffix = uuid.uuid4().hex[:8].upper()
        self.staff_user = User.objects.create_user(
            username=f"cost-staff-{suffix}",
            password="test-password",
            is_staff=True,
        )
        perms = Permission.objects.filter(
            content_type__app_label__in=[
                "manufacturing", "inventory", "costing", "purchasing", "sales"
            ]
        )
        self.staff_user.user_permissions.set(perms)
        self.regular_user = User.objects.create_user(
            username=f"cost-regular-{suffix}",
            password="test-password",
            is_staff=False,
        )

        # UOMs
        self.pcs = UnitOfMeasure.objects.create(code=f"PC{suffix}", name_ar="كرتونة", dimension="count")
        self.kg = UnitOfMeasure.objects.create(code=f"KG{suffix}", name_ar="كجم", dimension="mass")
        self.ton = UnitOfMeasure.objects.create(code=f"TN{suffix}", name_ar="طن", dimension="mass")
        self.roll = UnitOfMeasure.objects.create(code=f"RL{suffix}", name_ar="رول", dimension="count")

        # Categories
        self.finished_cat = ProductCategory.objects.get_or_create(code="finished-goods", defaults={"name_ar": "منتج تام"})[0]
        self.raw_cat = ProductCategory.objects.get_or_create(code="raw-material", defaults={"name_ar": "مادة خام"})[0]

        # Products
        self.raw_paper = Product.objects.create(
            sku=f"RAW-PAPER-{suffix}",
            name_ar="رول ورق خام 100جم",
            category=self.raw_cat,
            base_uom=self.kg,
        )
        # Unit conversion for raw_paper: 1 ton = 1000 kg
        ProductUnitConversion.objects.create(
            product=self.raw_paper,
            unit=self.ton,
            factor=Decimal("1000.0000"),
        )

        self.raw_foil = Product.objects.create(
            sku=f"RAW-FOIL-{suffix}",
            name_ar="رول سلوفان تغليف",
            category=self.raw_cat,
            base_uom=self.roll,
        )

        self.product_a = Product.objects.create(
            sku=f"PROD-NAPKIN-{suffix}",
            name_ar="مناديل جيب كرتونة",
            category=self.finished_cat,
            base_uom=self.pcs,
        )

        self.product_b = Product.objects.create(
            sku=f"PROD-TOWEL-{suffix}",
            name_ar="مناديل مطبخ كرتونة",
            category=self.finished_cat,
            base_uom=self.pcs,
        )

        # Production Lines & Operations
        self.line_1 = ProductionLine.objects.get_or_create(code="napkin-line-1", defaults={"name_ar": "خط مناديل 1"})[0]
        self.line_2 = ProductionLine.objects.get_or_create(code="towel-line-2", defaults={"name_ar": "خط رولات 2"})[0]
        self.op_cut = ProductionOperation.objects.get_or_create(code="cut-fold", defaults={"name_ar": "تقطيع وتطبيق"})[0]
        self.op_pack = ProductionOperation.objects.get_or_create(code="pack-box", defaults={"name_ar": "تعبئة وكرتنة"})[0]

        # Warehouse & Locations
        wh = Warehouse.objects.create(code=f"WH-{suffix}", name_ar="مخزن المصنع")
        self.raw_location = StorageLocation.objects.create(warehouse=wh, code="RAW", name_ar="موقع الخامات")
        self.fg_location = StorageLocation.objects.create(warehouse=wh, code="FG", name_ar="موقع التام")

        # Initial raw material stock
        post_movement(
            reference=f"INIT-PAPER-{suffix}",
            product=self.raw_paper,
            source_location=None,
            destination_location=self.raw_location,
            quantity=Decimal("10000"),
            unit=self.kg,
            actor=self.staff_user,
            movement_type=StockMovement.Type.RECEIPT,
            notes="Initial raw paper stock",
        )
        post_movement(
            reference=f"INIT-FOIL-{suffix}",
            product=self.raw_foil,
            source_location=None,
            destination_location=self.raw_location,
            quantity=Decimal("500"),
            unit=self.roll,
            actor=self.staff_user,
            movement_type=StockMovement.Type.RECEIPT,
            notes="Initial raw foil stock",
        )

        # Set default raw material prices:
        # Paper: 50 EGP per kg
        self.price_paper = RawMaterialPrice.objects.create(
            material=self.raw_paper,
            price_per_unit=Decimal("50.00"),
            unit=self.kg,
            effective_date=timezone.now().date(),
            created_by=self.staff_user,
        )
        # Foil: 200 EGP per roll
        self.price_foil = RawMaterialPrice.objects.create(
            material=self.raw_foil,
            price_per_unit=Decimal("200.00"),
            unit=self.roll,
            effective_date=timezone.now().date(),
            created_by=self.staff_user,
        )

        # Roll yield standard: 1 roll of raw_foil -> 100 cartons of product_a
        self.yield_std_a = RollYieldStandard.objects.create(
            raw_material=self.raw_foil,
            product=self.product_a,
            production_line=self.line_1,
            expected_cartons_per_roll=Decimal("100.00"),
            roll_unit=self.roll,
            carton_unit=self.pcs,
            created_by=self.staff_user,
        )


class CostingModelTests(CostingFixtureMixin, TestCase):
    def setUp(self):
        self.create_costing_fixture()

    def test_raw_material_price_creation_and_conversion(self):
        # Test converting price per ton to base unit (kg)
        # 40,000 EGP per ton -> 40 EGP per kg
        price_ton = RawMaterialPrice.objects.create(
            material=self.raw_paper,
            price_per_unit=Decimal("40000.00"),
            unit=self.ton,
            effective_date=timezone.now().date(),
            created_by=self.staff_user,
        )
        self.assertEqual(price_ton.price_per_base_unit, Decimal("40.000000"))

    def test_raw_material_price_validation_negative(self):
        with self.assertRaises(ValidationError):
            p = RawMaterialPrice(
                material=self.raw_paper,
                price_per_unit=Decimal("-10.00"),
                unit=self.kg,
                effective_date=timezone.now().date(),
                created_by=self.staff_user,
            )
            p.full_clean()

    def test_monthly_expense_validation(self):
        # Negative expense amount must be rejected
        with self.assertRaises(ValidationError):
            exp = MonthlyExpense(
                year=2026,
                month=10,
                expense_type=MonthlyExpense.ExpenseType.LABOR,
                scope=MonthlyExpense.AllocationScope.FACTORY,
                amount=Decimal("-500.00"),
                description="x",
                created_by=self.staff_user,
            )
            exp.full_clean()

        # Invalid month rejected
        with self.assertRaises(ValidationError):
            exp = MonthlyExpense(
                year=2026,
                month=13,
                expense_type=MonthlyExpense.ExpenseType.ELECTRICITY,
                scope=MonthlyExpense.AllocationScope.FACTORY,
                amount=Decimal("1000.00"),
                description="x",
                created_by=self.staff_user,
            )
            exp.full_clean()

    def test_zero_amount_expense_allowed_and_negative_rejected(self):
        """A confirmed zero-cost bill is valid; a negative amount is not."""
        zero = MonthlyExpense(
            year=2026, month=1,
            expense_type=MonthlyExpense.ExpenseType.ELECTRICITY,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("0.00"), description="لا استهلاك", created_by=self.staff_user,
        )
        zero.full_clean()
        zero.save()
        self.assertEqual(zero.amount, Decimal("0.00"))

        with self.assertRaises(ValidationError):
            MonthlyExpense(
                year=2026, month=1,
                expense_type=MonthlyExpense.ExpenseType.WATER,
                scope=MonthlyExpense.AllocationScope.FACTORY,
                amount=Decimal("-0.01"), description="x", created_by=self.staff_user,
            ).full_clean()

    def test_roll_yield_standard_validation(self):
        # Non-positive expected cartons per roll
        with self.assertRaises(ValidationError):
            std = RollYieldStandard(
                raw_material=self.raw_foil,
                product=self.product_a,
                production_line=self.line_1,
                expected_cartons_per_roll=Decimal("0.00"),
                roll_unit=self.roll,
                carton_unit=self.pcs,
                created_by=self.staff_user,
            )
            std.full_clean()

    def test_product_selling_price(self):
        sp = ProductSellingPrice.objects.create(
            product=self.product_a,
            selling_price=Decimal("120.00"),
            unit=self.pcs,
            updated_by=self.staff_user,
        )
        self.assertEqual(sp.selling_price, Decimal("120.00"))

    def test_finalized_period_blocks_orm_and_bulk_writes(self):
        """A finalized month must reject instance and queryset writes."""
        # Open month: normal create/save/delete still works.
        expense = MonthlyExpense.objects.create(
            year=2026, month=3,
            expense_type=MonthlyExpense.ExpenseType.LABOR,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("100.00"), description="labor", created_by=self.staff_user,
        )
        expense.amount = Decimal("150.00")
        expense.save()
        expense.delete()

        # Finalize a month that already holds an expense.
        expense = MonthlyExpense.objects.create(
            year=2026, month=4,
            expense_type=MonthlyExpense.ExpenseType.ELECTRICITY,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("500.00"), description="electricity", created_by=self.staff_user,
        )
        period = CostingPeriod.objects.create(year=2026, month=4, status=CostingPeriod.Status.FINALIZED)

        with self.assertRaises(ValidationError):
            MonthlyExpense.objects.create(
                year=2026, month=4,
                expense_type=MonthlyExpense.ExpenseType.WATER,
                scope=MonthlyExpense.AllocationScope.FACTORY,
                amount=Decimal("50.00"), description="water", created_by=self.staff_user,
            )
        with self.assertRaises(ValidationError):
            MonthlyExpense.objects.filter(pk=expense.pk).update(amount=Decimal("1.00"))
        with self.assertRaises(ValidationError):
            MonthlyExpense.objects.filter(pk=expense.pk).delete()
        with self.assertRaises(ValidationError):
            expense.save()
        with self.assertRaises(ValidationError):
            expense.delete()
        with self.assertRaises(ValidationError):
            CostingPeriod.objects.filter(pk=period.pk).delete()
        with self.assertRaises(ValidationError):
            period.delete()

        # Nothing was changed by the rejected bulk write.
        expense.refresh_from_db()
        self.assertEqual(expense.amount, Decimal("500.00"))


class CostingProductionMixin(CostingFixtureMixin):
    """Routing/BOM fixture plus a realistic completed production-run helper.

    Shared by the calculation tests and the electricity/water workflow tests so
    both exercise the same production and inventory workflows.
    """

    def setUp(self):
        self.create_costing_fixture()
        self.now = timezone.now()
        self.year = self.now.year
        self.month = self.now.month

        # Create Routing and BOM for product_a
        self.routing_a = Routing.objects.create(product=self.product_a, production_line=self.line_1, version=1, created_by=self.staff_user)
        RoutingStep.objects.create(
            routing=self.routing_a,
            operation=self.op_cut,
            sequence=10,
        )
        self.bom_a = BillOfMaterials.objects.create(product=self.product_a, version=1, output_quantity=Decimal("1"), output_unit=self.pcs, created_by=self.staff_user)
        BOMComponent.objects.create(
            bom=self.bom_a,
            material=self.raw_paper,
            quantity=Decimal("3.0"),
            unit=self.kg,
            sequence=10,
        )
        BOMComponent.objects.create(
            bom=self.bom_a,
            material=self.raw_foil,
            quantity=Decimal("0.02"),
            unit=self.roll,
            sequence=20,
        )

        # Create Routing and BOM for product_b
        self.routing_b = Routing.objects.create(product=self.product_b, production_line=self.line_2, version=1, created_by=self.staff_user)
        RoutingStep.objects.create(
            routing=self.routing_b,
            operation=self.op_cut,
            sequence=10,
        )
        self.bom_b = BillOfMaterials.objects.create(product=self.product_b, version=1, output_quantity=Decimal("1"), output_unit=self.pcs, created_by=self.staff_user)
        BOMComponent.objects.create(
            bom=self.bom_b,
            material=self.raw_paper,
            quantity=Decimal("5.0"),
            unit=self.kg,
            sequence=10,
        )

    def _create_production_run(
        self, product, line, planned_qty, actual_paper_kg, actual_foil_rolls,
        good_qty, scrap_qty, *, measured_waste_base=None, review_material_waste=True,
    ):
        """Helper to create and complete a realistic production run with actual issues and outputs."""
        order = save_draft_order(
            actor=self.staff_user,
            product=product,
            planned_quantity=planned_qty,
            planned_unit=self.pcs,
            production_line=line,
            routing=self.routing_a if product == self.product_a else self.routing_b,
            bom=self.bom_a if product == self.product_a else self.bom_b,
            source_location=self.raw_location,
        )
        release_order(order=order, actor=self.staff_user)

        # Issue actual materials (NOT planned BOM amounts)
        if actual_paper_kg > 0:
            req_paper = order.requirements.filter(material=self.raw_paper).first()
            issue_material(
                order=order,
                requirement=req_paper,
                quantity=actual_paper_kg,
                unit=self.kg,
                reference=f"ISSUE-PAPER-{uuid.uuid4().hex[:6]}",
                actor=self.staff_user,
            )
        if actual_foil_rolls > 0:
            req_foil = order.requirements.filter(material=self.raw_foil).first()
            issue_material(
                order=order,
                requirement=req_foil,
                quantity=actual_foil_rolls,
                unit=self.roll,
                reference=f"ISSUE-FOIL-{uuid.uuid4().hex[:6]}",
                actor=self.staff_user,
            )

        # Record operation progress and output
        op_step = order.operations.first()
        record_operation_progress(
            order=order,
            order_operation=op_step,
            quantity=good_qty + scrap_qty,
            unit=self.pcs,
            notes="Run result",
            actor=self.staff_user,
        )
        complete_operation(order=order, order_operation=op_step, actor=self.staff_user)
        result, _ = record_production_result(
            order=order,
            good_quantity=good_qty,
            scrap_quantity=scrap_qty,
            unit=self.pcs,
            actor=self.staff_user,
            notes="Run result",
        )
        if measured_waste_base is not None:
            material = self.raw_paper
            material_issue = order.material_issues.get(requirement__material=material)
            record_production_material_waste(
                result=result, material=material, quantity_base=Decimal(measured_waste_base),
                material_issue=material_issue, actor=self.staff_user,
            )
        elif review_material_waste:
            confirm_no_material_waste(result=result, actor=self.staff_user)
        if good_qty > 0:
            receive_finished_goods(
                order=order,
                quantity=good_qty,
                unit=self.pcs,
                destination_location=self.fg_location,
                reference=f"RCV-{order.order_number}-{uuid.uuid4().hex[:6]}",
                actor=self.staff_user,
            )
            complete_order(order=order, actor=self.staff_user)
        return order


class CostingCalculationTests(CostingProductionMixin, TestCase):
    def test_actual_material_cost_calculated_from_issues_not_bom(self):
        # Planned 100 pcs, which on BOM would be 200 kg paper & 1 roll foil
        # But actual issue is 250 kg paper and 2 rolls foil
        self._create_production_run(
            product=self.product_a,
            line=self.line_1,
            planned_qty=Decimal("100"),
            actual_paper_kg=Decimal("250"),
            actual_foil_rolls=Decimal("2"),
            good_qty=Decimal("95"),
            scrap_qty=Decimal("5"),
            review_material_waste=False,
        )

        # Electricity and water are required utilities for a month with
        # production, so record them (amounts are irrelevant to this assertion).
        MonthlyExpense.objects.create(
            year=self.year, month=self.month,
            expense_type=MonthlyExpense.ExpenseType.ELECTRICITY,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("100.00"), description="electricity", created_by=self.staff_user,
        )
        MonthlyExpense.objects.create(
            year=self.year, month=self.month,
            expense_type=MonthlyExpense.ExpenseType.WATER,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("50.00"), description="water", created_by=self.staff_user,
        )
        # Labor is a required monthly record; zero is explicit, not missing.
        MonthlyExpense.objects.create(
            year=self.year, month=self.month,
            expense_type=MonthlyExpense.ExpenseType.LABOR,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("0.00"), description="labor", created_by=self.staff_user,
        )

        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        entry = period.product_entries.get(product=self.product_a)

        # Expected paper cost: 250 kg * 50 EGP/kg = 12,500 EGP
        # Expected foil cost: 2 rolls * 200 EGP/roll = 400 EGP
        # Total material cost = 12,900 EGP
        self.assertEqual(entry.actual_material_cost, Decimal("12900.00"))
        self.assertEqual(period.materials_total, Decimal("12900.00"))
        self.assertFalse(period.is_complete)
        self.assertIn("لم تُسجل قياسات الهالك المادي", period.missing_data_notes)

    def test_finalization_recalculates_a_stale_draft_before_freezing(self):
        self._create_production_run(
            product=self.product_a,
            line=self.line_1,
            planned_qty=Decimal("100"),
            actual_paper_kg=Decimal("200"),
            actual_foil_rolls=Decimal("1"),
            good_qty=Decimal("100"),
            scrap_qty=Decimal("0"),
        )
        for expense_type, amount in (
            (MonthlyExpense.ExpenseType.LABOR, "0"),
            (MonthlyExpense.ExpenseType.ELECTRICITY, "100"),
            (MonthlyExpense.ExpenseType.WATER, "50"),
        ):
            MonthlyExpense.objects.create(
                year=self.year,
                month=self.month,
                expense_type=expense_type,
                scope=MonthlyExpense.AllocationScope.FACTORY,
                amount=Decimal(amount),
                description=expense_type,
                created_by=self.staff_user,
            )

        period = calculate_monthly_costing(
            year=self.year, month=self.month, actor=self.staff_user
        )
        self.assertTrue(period.is_complete)
        self.assertEqual(
            period.product_entries.get(product=self.product_a).actual_material_cost,
            Decimal("10200.00"),
        )

        # This output and its material issue arrive after the draft calculation.
        # Finalization must include them instead of freezing the stale figures.
        self._create_production_run(
            product=self.product_a,
            line=self.line_1,
            planned_qty=Decimal("50"),
            actual_paper_kg=Decimal("100"),
            actual_foil_rolls=Decimal("1"),
            good_qty=Decimal("50"),
            scrap_qty=Decimal("0"),
        )
        period = finalize_costing_period(period=period, actor=self.staff_user)
        entry = period.product_entries.get(product=self.product_a)
        self.assertEqual(period.status, CostingPeriod.Status.FINALIZED)
        self.assertEqual(entry.actual_material_cost, Decimal("15400.00"))
        self.assertEqual(entry.good_units_produced, Decimal("150"))

    def test_missing_material_price_flags_incomplete_status(self):
        # The cost snapshot is captured at issue time, so a missing price must
        # be absent BEFORE the material is issued for the production run.
        RawMaterialPrice.objects.filter(material=self.raw_foil).delete()
        self._create_production_run(
            product=self.product_a,
            line=self.line_1,
            planned_qty=Decimal("50"),
            actual_paper_kg=Decimal("100"),
            actual_foil_rolls=Decimal("1"),
            good_qty=Decimal("50"),
            scrap_qty=Decimal("0"),
        )

        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        self.assertFalse(period.is_complete)
        self.assertIn(self.raw_foil.sku, period.missing_data_notes)

        entry = period.product_entries.get(product=self.product_a)
        self.assertEqual(entry.calculation_status, "INCOMPLETE")

        # Finalizing must fail when incomplete
        with self.assertRaises(ValidationError):
            finalize_costing_period(period=period, actor=self.staff_user)

    def test_roll_yield_and_waste_shortfall_calculation(self):
        # Standard: 1 roll foil -> 100 cartons of product_a
        # Actual: 5 rolls consumed, good output = 450 cartons, scrap = 10
        # Expected cartons = 5 * 100 = 500 cartons
        # Shortfall = (500 - 450) / 500 * 100 = 10.00 %
        # Variance cartons = 450 - 500 = -50 cartons
        self._create_production_run(
            product=self.product_a,
            line=self.line_1,
            planned_qty=Decimal("500"),
            actual_paper_kg=Decimal("1000"),
            actual_foil_rolls=Decimal("5"),
            good_qty=Decimal("450"),
            scrap_qty=Decimal("10"),
        )

        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        entry = period.product_entries.get(product=self.product_a)

        self.assertEqual(entry.rolls_consumed, Decimal("5.0000"))
        self.assertEqual(entry.expected_cartons, Decimal("500.00"))
        self.assertEqual(entry.actual_good_cartons, Decimal("450.00"))
        self.assertEqual(entry.yield_shortfall_percent, Decimal("10.00"))
        self.assertEqual(entry.yield_variance_cartons, Decimal("-50.00"))

    def test_roll_yield_positive_variance(self):
        # 2 rolls consumed, expected 200 cartons, actual good = 210
        # Shortfall should be 0%, variance = +10 cartons
        self._create_production_run(
            product=self.product_a,
            line=self.line_1,
            planned_qty=Decimal("250"),
            actual_paper_kg=Decimal("400"),
            actual_foil_rolls=Decimal("2"),
            good_qty=Decimal("210"),
            scrap_qty=Decimal("0"),
        )

        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        entry = period.product_entries.get(product=self.product_a)

        self.assertEqual(entry.yield_shortfall_percent, Decimal("0.00"))
        self.assertEqual(entry.yield_variance_cartons, Decimal("10.00"))

    def test_division_by_zero_protection_when_zero_good_units(self):
        # Only scrap produced, 0 good units
        self._create_production_run(
            product=self.product_a,
            line=self.line_1,
            planned_qty=Decimal("100"),
            actual_paper_kg=Decimal("200"),
            actual_foil_rolls=Decimal("1"),
            good_qty=Decimal("0"),
            scrap_qty=Decimal("50"),
        )

        # Monthly expenses: all required buckets are present, but this product
        # has no good output and therefore no usable good-unit cost.
        MonthlyExpense.objects.create(
            year=self.year,
            month=self.month,
            expense_type=MonthlyExpense.ExpenseType.LABOR,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("1000.00"),
            description="labor",
            created_by=self.staff_user,
        )
        MonthlyExpense.objects.create(
            year=self.year, month=self.month,
            expense_type=MonthlyExpense.ExpenseType.ELECTRICITY,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("300.00"), description="electricity", created_by=self.staff_user,
        )
        MonthlyExpense.objects.create(
            year=self.year, month=self.month,
            expense_type=MonthlyExpense.ExpenseType.WATER,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("120.00"), description="water", created_by=self.staff_user,
        )

        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        entry = period.product_entries.get(product=self.product_a)

        self.assertEqual(entry.good_units_produced, Decimal("0"))
        self.assertIsNone(entry.cost_per_good_unit)
        self.assertGreater(entry.total_production_cost, Decimal("0"))
        self.assertEqual(entry.calculation_status, "INCOMPLETE")
        self.assertFalse(period.is_complete)
        with self.assertRaises(ValidationError):
            finalize_costing_period(period=period, actor=self.staff_user)

    def test_expense_allocation_policies_and_exact_cent_reconciliation(self):
        # Create runs for two products on two lines:
        # Product A: 300 good units, 600 kg paper (600 * 50 = 30,000 EGP)
        self._create_production_run(
            product=self.product_a,
            line=self.line_1,
            planned_qty=Decimal("300"),
            actual_paper_kg=Decimal("600"),
            actual_foil_rolls=Decimal("3"),
            good_qty=Decimal("300"),
            scrap_qty=Decimal("0"),
        )
        # Product B: 100 good units, 500 kg paper (500 * 50 = 25,000 EGP)
        self._create_production_run(
            product=self.product_b,
            line=self.line_2,
            planned_qty=Decimal("100"),
            actual_paper_kg=Decimal("500"),
            actual_foil_rolls=Decimal("0"),
            good_qty=Decimal("100"),
            scrap_qty=Decimal("0"),
        )

        # Expenses:
        # Factory-wide labor: 10,000.33 EGP
        MonthlyExpense.objects.create(
            year=self.year,
            month=self.month,
            expense_type=MonthlyExpense.ExpenseType.LABOR,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("10000.33"),
            description="labor",
            created_by=self.staff_user,
        )
        # Factory-wide electricity: 5,000.77 EGP
        MonthlyExpense.objects.create(
            year=self.year,
            month=self.month,
            expense_type=MonthlyExpense.ExpenseType.ELECTRICITY,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("5000.77"),
            description="electricity",
            created_by=self.staff_user,
        )
        # Line-specific other industrial: 1,500.00 on Line 1
        MonthlyExpense.objects.create(
            year=self.year,
            month=self.month,
            expense_type=MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL,
            scope=MonthlyExpense.AllocationScope.LINE,
            production_line=self.line_1,
            amount=Decimal("1500.00"),
            description="other",
            created_by=self.staff_user,
        )

        # Test each allocation policy
        policies = [
            CostingPeriod.AllocationPolicy.UNITS_PRODUCED,
            CostingPeriod.AllocationPolicy.MATERIAL_COST,
            CostingPeriod.AllocationPolicy.MATERIAL_WEIGHT,
            CostingPeriod.AllocationPolicy.EQUAL_PER_LINE,
        ]

        for policy in policies:
            period = calculate_monthly_costing(
                year=self.year,
                month=self.month,
                allocation_policy=policy,
                actor=self.staff_user,
            )

            # Exact reconciliation check: sum of allocated labor == total labor
            total_allocated_labor = sum(e.allocated_labor_cost for e in period.product_entries.all())
            total_allocated_electricity = sum(e.allocated_electricity_cost for e in period.product_entries.all())
            total_allocated_other = sum(e.allocated_other_cost for e in period.product_entries.all())

            self.assertEqual(total_allocated_labor, Decimal("10000.33"))
            self.assertEqual(total_allocated_electricity, Decimal("5000.77"))
            self.assertEqual(total_allocated_other, Decimal("1500.00"))

            # Line-specific expense on Line 1 should only be allocated to Product A (Line 1)
            entry_a = period.product_entries.get(product=self.product_a)
            entry_b = period.product_entries.get(product=self.product_b)
            self.assertEqual(entry_a.allocated_other_cost, Decimal("1500.00"))
            self.assertEqual(entry_b.allocated_other_cost, Decimal("0.00"))

    def test_line_scoped_expense_survives_shared_allocation_in_same_category(self):
        """A line-scoped expense must not be erased by a later shared expense step.

        Regression guard: factory-wide and line-scoped expenses of the *same*
        category share one running-total field, so the shared rounding pass must
        reconcile only its own bucket instead of rewriting the whole column.
        """
        self._create_production_run(
            product=self.product_a,
            line=self.line_1,
            planned_qty=Decimal("300"),
            actual_paper_kg=Decimal("600"),
            actual_foil_rolls=Decimal("3"),
            good_qty=Decimal("300"),
            scrap_qty=Decimal("0"),
        )
        self._create_production_run(
            product=self.product_b,
            line=self.line_2,
            planned_qty=Decimal("100"),
            actual_paper_kg=Decimal("500"),
            actual_foil_rolls=Decimal("0"),
            good_qty=Decimal("100"),
            scrap_qty=Decimal("0"),
        )

        # Same category (OTHER_INDUSTRIAL) registered once factory-wide and once
        # line-scoped, plus a factory-wide labor expense of a different category.
        MonthlyExpense.objects.create(
            year=self.year, month=self.month,
            expense_type=MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("100.00"), description="shared other", created_by=self.staff_user,
        )
        MonthlyExpense.objects.create(
            year=self.year, month=self.month,
            expense_type=MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL,
            scope=MonthlyExpense.AllocationScope.LINE,
            production_line=self.line_1,
            amount=Decimal("1500.00"), description="line other", created_by=self.staff_user,
        )
        MonthlyExpense.objects.create(
            year=self.year, month=self.month,
            expense_type=MonthlyExpense.ExpenseType.LABOR,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("10000.33"), description="labor", created_by=self.staff_user,
        )

        period = calculate_monthly_costing(
            year=self.year, month=self.month,
            allocation_policy=CostingPeriod.AllocationPolicy.UNITS_PRODUCED,
            actor=self.staff_user,
        )
        entry_a = period.product_entries.get(product=self.product_a)
        entry_b = period.product_entries.get(product=self.product_b)

        # Exact reconciliation of the whole category: 1500 (line) + 100 (shared).
        total_other = sum(e.allocated_other_cost for e in period.product_entries.all())
        self.assertEqual(total_other, Decimal("1600.00"))
        self.assertEqual(period.other_industrial_total, Decimal("1600.00"))
        # Product A (line 1) carries the entire line-scoped amount plus its
        # 300/400 = 0.75 share of the shared 100.00 => 75.00 => 1575.00.
        self.assertEqual(entry_a.allocated_other_cost, Decimal("1575.00"))
        # Product B (line 2) receives only its 0.25 share of the shared amount.
        self.assertEqual(entry_b.allocated_other_cost, Decimal("25.00"))
        # The unrelated labor category still reconciles exactly.
        self.assertEqual(
            sum(e.allocated_labor_cost for e in period.product_entries.all()),
            Decimal("10000.33"),
        )

    def test_recalculation_replaces_entries_without_duplicate_allocations(self):
        """Repeated calculation must be idempotent: no duplicated rows or amounts."""
        self._create_production_run(
            product=self.product_a,
            line=self.line_1,
            planned_qty=Decimal("100"),
            actual_paper_kg=Decimal("200"),
            actual_foil_rolls=Decimal("1"),
            good_qty=Decimal("100"),
            scrap_qty=Decimal("0"),
        )
        MonthlyExpense.objects.create(
            year=self.year, month=self.month,
            expense_type=MonthlyExpense.ExpenseType.LABOR,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("100.00"), description="labor", created_by=self.staff_user,
        )

        first = calculate_monthly_costing(
            year=self.year, month=self.month,
            allocation_policy=CostingPeriod.AllocationPolicy.UNITS_PRODUCED,
            actor=self.staff_user,
        )
        first_total = first.total_production_cost
        self.assertEqual(first.product_entries.count(), 1)
        self.assertEqual(first.order_entries.count(), 1)

        for policy in (
            CostingPeriod.AllocationPolicy.MATERIAL_COST,
            CostingPeriod.AllocationPolicy.MATERIAL_WEIGHT,
            CostingPeriod.AllocationPolicy.EQUAL_PER_LINE,
            CostingPeriod.AllocationPolicy.UNITS_PRODUCED,
        ):
            period = calculate_monthly_costing(
                year=self.year, month=self.month,
                allocation_policy=policy,
                actor=self.staff_user,
            )
            # Entries are replaced, never accumulated.
            self.assertEqual(period.product_entries.count(), 1)
            self.assertEqual(period.order_entries.count(), 1)
            self.assertEqual(
                sum(e.allocated_labor_cost for e in period.product_entries.all()),
                Decimal("100.00"),
            )
            self.assertEqual(period.total_production_cost, first_total)

    def test_period_lifecycle_finalize_and_reopen(self):
        self._create_production_run(
            product=self.product_a,
            line=self.line_1,
            planned_qty=Decimal("100"),
            actual_paper_kg=Decimal("200"),
            actual_foil_rolls=Decimal("1"),
            good_qty=Decimal("100"),
            scrap_qty=Decimal("0"),
        )

        # A month with production requires recorded electricity and water bills,
        # otherwise the period is intentionally flagged incomplete and cannot be
        # finalized. Record them so the lifecycle under test can proceed.
        MonthlyExpense.objects.create(
            year=self.year, month=self.month,
            expense_type=MonthlyExpense.ExpenseType.ELECTRICITY,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("300.00"), description="electricity", created_by=self.staff_user,
        )
        MonthlyExpense.objects.create(
            year=self.year, month=self.month,
            expense_type=MonthlyExpense.ExpenseType.WATER,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("120.00"), description="water", created_by=self.staff_user,
        )
        MonthlyExpense.objects.create(
            year=self.year, month=self.month,
            expense_type=MonthlyExpense.ExpenseType.LABOR,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("0.00"), description="labor", created_by=self.staff_user,
        )

        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        self.assertEqual(period.status, CostingPeriod.Status.DRAFT)

        # Finalize period
        finalized_period = finalize_costing_period(period=period, actor=self.staff_user)
        self.assertEqual(finalized_period.status, CostingPeriod.Status.FINALIZED)
        self.assertIsNotNone(finalized_period.finalized_at)
        self.assertEqual(finalized_period.finalized_by, self.staff_user)

        # Recalculating finalized period must raise ValidationError
        with self.assertRaises(ValidationError):
            calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)

        # Reopening without reason must fail
        with self.assertRaises(ValidationError):
            reopen_costing_period(period=finalized_period, reason="", actor=self.staff_user)

        # Reopening with valid reason succeeds
        reopened = reopen_costing_period(
            period=finalized_period,
            reason="تعديل فاتورة كهرباء متأخرة لشهر أكتوبر",
            actor=self.staff_user,
        )
        self.assertEqual(reopened.status, CostingPeriod.Status.DRAFT)
        self.assertEqual(reopened.reopen_reason, "تعديل فاتورة كهرباء متأخرة لشهر أكتوبر")
        self.assertEqual(reopened.reopened_by, self.staff_user)


class CostingViewAndPermissionTests(CostingFixtureMixin, TestCase):
    def setUp(self):
        self.create_costing_fixture()
        self.now = timezone.now()
        self.year = self.now.year
        self.month = self.now.month

    def _financial_routes(self, period=None, entry=None):
        """Every costing page that exposes financial data."""
        return [
            ("costing:period_list", {}),
            ("costing:period_compare", {}),
            ("costing:expense_list", {}),
            ("costing:price_list", {}),
            ("costing:selling_price_list", {}),
            ("costing:yield_standard_list", {}),
            ("costing:period_detail", {"pk": period.pk if period else 1}),
            ("costing:order_breakdown", {"pk": period.pk if period else 1}),
            ("costing:product_breakdown", {"pk": entry.pk if entry else 1}),
        ]

    def test_anonymous_redirected_to_login(self):
        for name, kwargs in self._financial_routes():
            resp = self.client.get(reverse(name, kwargs=kwargs))
            self.assertEqual(resp.status_code, 302, name)
            self.assertIn("login", resp.url)

    def test_regular_user_cannot_access_financial_views(self):
        # An ordinary authenticated user must not see costing financial data.
        self.client.login(username=self.regular_user.username, password="test-password")
        for name, kwargs in self._financial_routes():
            resp = self.client.get(reverse(name, kwargs=kwargs))
            self.assertEqual(resp.status_code, 403, f"{name} must be forbidden for non-finance users")

    def test_regular_user_cannot_access_staff_write_views(self):
        self.client.login(username=self.regular_user.username, password="test-password")
        for name in ("costing:expense_create", "costing:price_create", "costing:yield_standard_create"):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 403, name)

    def test_staff_user_can_view_financial_views(self):
        period = CostingPeriod.objects.create(year=2026, month=1)
        entry = ProductCostingEntry.objects.create(period=period, product=self.product_a)
        self.client.login(username=self.staff_user.username, password="test-password")
        for name, kwargs in self._financial_routes(period=period, entry=entry):
            resp = self.client.get(reverse(name, kwargs=kwargs))
            self.assertEqual(resp.status_code, 200, name)

    def test_finance_user_with_explicit_permission_can_view_but_not_write(self):
        # An explicitly permitted non-staff finance user keeps read access ...
        self.regular_user.user_permissions.add(Permission.objects.get(codename="view_costing_financials"))
        self.client.login(username=self.regular_user.username, password="test-password")
        for name, kwargs in self._financial_routes():
            resp = self.client.get(reverse(name, kwargs=kwargs))
            self.assertIn(resp.status_code, (200, 404), name)

        # ... but the financial read permission never grants write access.
        for name in ("costing:expense_create", "costing:price_create", "costing:yield_standard_create"):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 403, name)

    def test_staff_user_can_create_expense_and_calculate_period(self):
        self.client.login(username=self.staff_user.username, password="test-password")

        # Add expense via view
        resp = self.client.post(
            reverse("costing:expense_create"),
            {
                "year": self.year,
                "month": self.month,
                "expense_type": MonthlyExpense.ExpenseType.LABOR,
                "scope": MonthlyExpense.AllocationScope.FACTORY,
                "amount": "15000.00",
                "description": "رواتب عمال الإنتاج",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(MonthlyExpense.objects.filter(amount=Decimal("15000.00")).exists())

        # Trigger calculation via POST
        calc_url = reverse("costing:period_calculate")
        resp = self.client.post(
            calc_url,
            {
                "year": self.year,
                "month": self.month,
                "allocation_policy": CostingPeriod.AllocationPolicy.UNITS_PRODUCED,
            },
        )
        self.assertEqual(resp.status_code, 302)

        period = CostingPeriod.objects.get(year=self.year, month=self.month)
        self.assertEqual(period.labor_total, Decimal("15000.00"))


class CostingUtilityExpenseTests(CostingProductionMixin, TestCase):
    """Electricity/water expense workflow, scopes, cross-month isolation, comparison."""

    def _add_expense(self, year, month, expense_type, amount, scope=None, line=None, product=None):
        return MonthlyExpense.objects.create(
            year=year,
            month=month,
            expense_type=expense_type,
            scope=scope or MonthlyExpense.AllocationScope.FACTORY,
            production_line=line,
            product=product,
            amount=amount,
            description="اختبار",
            created_by=self.staff_user,
        )

    def _previous_month(self):
        if self.month > 1:
            return self.year, self.month - 1
        return self.year - 1, 12

    def _create_backdated_run(self, product, line, actual_paper_kg, actual_foil_rolls, good_qty, scrap_qty, year, month):
        """Create a completed run whose issues/output are dated inside a past month.

        Posted movements cannot be updated (the ORM manager and the PostgreSQL
        trigger both refuse), so the issue movement is inserted with the target
        ``occurred_at`` and the production result is stamped afterwards. The
        existing manufacturing services do not accept a date, hence the direct
        construction of the same records they create.
        """
        target = timezone.make_aware(datetime(year, month, 15, 12, 0))
        order = save_draft_order(
            actor=self.staff_user, product=product, planned_quantity=Decimal("1000"), planned_unit=self.pcs,
            production_line=line, routing=self.routing_a if product == self.product_a else self.routing_b,
            bom=self.bom_a if product == self.product_a else self.bom_b, source_location=self.raw_location,
        )
        release_order(order=order, actor=self.staff_user)
        for material, quantity, unit in (
            (self.raw_paper, actual_paper_kg, self.kg),
            (self.raw_foil, actual_foil_rolls, self.roll),
        ):
            if quantity <= 0:
                continue
            requirement = order.requirements.filter(material=material).first()
            issue_id = uuid.uuid4()
            with mock.patch("django.utils.timezone.now", return_value=target):
                movement = post_movement(
                    reference=f"MFG-I-{issue_id.hex}", movement_type=StockMovement.Type.ISSUE,
                    product=material, quantity=quantity, unit=unit, actor=self.staff_user,
                    source_location=self.raw_location, occurred_at=target,
                    source_reference_type="manufacturing.material_issue", source_reference_id=str(issue_id),
                    reason=f"صرف تاريخي {order.order_number}",
                )
                unit_cost_snapshot = None
                cost_source = MaterialIssue.CostSource.UNAVAILABLE
                applicable_price = (
                    RawMaterialPrice.objects.filter(material=material, effective_date__lte=target.date())
                    .order_by("-effective_date", "-created_at")
                    .first()
                )
                if applicable_price is not None:
                    unit_cost_snapshot = applicable_price.price_per_base_unit
                    cost_source = MaterialIssue.CostSource.STANDARD_PRICE_FALLBACK
                MaterialIssue.objects.create(
                    id=issue_id, order=order, requirement=requirement,
                    reference=f"HIST-{issue_id.hex[:10]}", movement=movement,
                    quantity=quantity, unit=unit,
                    quantity_base=to_base_quantity(material, quantity, unit), actor=self.staff_user,
                    unit_cost_snapshot=unit_cost_snapshot, cost_source=cost_source,
                )
        with mock.patch("django.utils.timezone.now", return_value=target):
            result, _ = record_production_result(
                order=order, good_quantity=good_qty, scrap_quantity=scrap_qty, unit=self.pcs,
                actor=self.staff_user, notes="نتيجة تاريخية", occurred_at=target,
            )
            confirm_no_material_waste(result=result, actor=self.staff_user)
        return order

    def _backdate_prices(self, year):
        """Add material prices effective at the start of ``year`` for past-month tests."""
        RawMaterialPrice.objects.create(
            material=self.raw_paper, price_per_unit=Decimal("50.00"), unit=self.kg,
            effective_date=date(year, 1, 1), created_by=self.staff_user,
        )
        RawMaterialPrice.objects.create(
            material=self.raw_foil, price_per_unit=Decimal("200.00"), unit=self.roll,
            effective_date=date(year, 1, 1), created_by=self.staff_user,
        )

    def test_electricity_and_water_included_in_product_cost_and_reconcile(self):
        # Single product, absorbed waste policy: full material cost is on good units.
        self._create_production_run(
            product=self.product_a, line=self.line_1,
            planned_qty=Decimal("100"), actual_paper_kg=Decimal("200"),
            actual_foil_rolls=Decimal("1"), good_qty=Decimal("100"), scrap_qty=Decimal("0"),
        )
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.LABOR, Decimal("1000.00"))
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("300.00"))
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.WATER, Decimal("120.50"))

        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        entry = period.product_entries.get(product=self.product_a)

        # Material = 200 kg * 50 + 1 roll * 200 = 10,200
        self.assertEqual(entry.actual_material_cost, Decimal("10200.00"))
        self.assertEqual(entry.allocated_electricity_cost, Decimal("300.00"))
        self.assertEqual(entry.allocated_water_cost, Decimal("120.50"))
        self.assertEqual(period.electricity_total, Decimal("300.00"))
        self.assertEqual(period.water_total, Decimal("120.50"))
        self.assertTrue(period.electricity_recorded)
        self.assertTrue(period.water_recorded)
        # Total product cost includes electricity and water explicitly.
        self.assertEqual(
            entry.total_production_cost,
            Decimal("10200.00") + Decimal("1000.00") + Decimal("300.00") + Decimal("120.50"),
        )
        self.assertEqual(period.total_production_cost, entry.total_production_cost)
        # Water is allocated exactly once across products.
        self.assertEqual(
            sum(e.allocated_water_cost for e in period.product_entries.all()),
            Decimal("120.50"),
        )
        self.assertTrue(period.is_complete)

    def test_electricity_water_scopes_allocate_to_correct_products(self):
        self._create_production_run(
            product=self.product_a, line=self.line_1,
            planned_qty=Decimal("300"), actual_paper_kg=Decimal("600"),
            actual_foil_rolls=Decimal("3"), good_qty=Decimal("300"), scrap_qty=Decimal("0"),
        )
        self._create_production_run(
            product=self.product_b, line=self.line_2,
            planned_qty=Decimal("100"), actual_paper_kg=Decimal("500"),
            actual_foil_rolls=Decimal("0"), good_qty=Decimal("100"), scrap_qty=Decimal("0"),
        )

        # Water: 400 factory-wide + 100 line-1 + 60 product B = 560 recorded.
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.WATER, Decimal("400.00"))
        self._add_expense(
            self.year, self.month, MonthlyExpense.ExpenseType.WATER, Decimal("100.00"),
            scope=MonthlyExpense.AllocationScope.LINE, line=self.line_1,
        )
        self._add_expense(
            self.year, self.month, MonthlyExpense.ExpenseType.WATER, Decimal("60.00"),
            scope=MonthlyExpense.AllocationScope.PRODUCT, product=self.product_b,
        )
        # Keep the period complete.
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("200.00"))

        period = calculate_monthly_costing(
            year=self.year, month=self.month,
            allocation_policy=CostingPeriod.AllocationPolicy.UNITS_PRODUCED,
            actor=self.staff_user,
        )
        entry_a = period.product_entries.get(product=self.product_a)
        entry_b = period.product_entries.get(product=self.product_b)

        self.assertEqual(period.water_total, Decimal("560.00"))
        # Total allocated water equals total recorded water (no duplication/loss).
        self.assertEqual(
            sum(e.allocated_water_cost for e in period.product_entries.all()),
            Decimal("560.00"),
        )
        # Product A: full line-1 (100) + 300/400 share of factory (400) = 400.
        self.assertEqual(entry_a.allocated_water_cost, Decimal("400.00"))
        # Product B: its own product-scoped 60 + 100/400 share of factory (400) = 160.
        self.assertEqual(entry_b.allocated_water_cost, Decimal("160.00"))

    def test_each_month_uses_its_own_recorded_utility_expenses(self):
        prev_year, prev_month = self._previous_month()
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("200.00"))
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.WATER, Decimal("50.00"))
        self._add_expense(prev_year, prev_month, MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("999.00"))
        self._add_expense(prev_year, prev_month, MonthlyExpense.ExpenseType.WATER, Decimal("77.00"))

        current = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        previous = calculate_monthly_costing(year=prev_year, month=prev_month, actor=self.staff_user)

        self.assertEqual(current.electricity_total, Decimal("200.00"))
        self.assertEqual(current.water_total, Decimal("50.00"))
        self.assertEqual(previous.electricity_total, Decimal("999.00"))
        self.assertEqual(previous.water_total, Decimal("77.00"))
        # No production recorded in the previous month: costs are the utilities only.
        self.assertEqual(previous.materials_total, Decimal("0.00"))
        self.assertEqual(previous.total_production_cost, Decimal("999.00") + Decimal("77.00"))

    def test_missing_required_utilities_flag_incomplete_and_block_finalize(self):
        self._create_production_run(
            product=self.product_a, line=self.line_1,
            planned_qty=Decimal("100"), actual_paper_kg=Decimal("200"),
            actual_foil_rolls=Decimal("1"), good_qty=Decimal("100"), scrap_qty=Decimal("0"),
        )
        # Only labor recorded: electricity and water are silently absent.
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.LABOR, Decimal("500.00"))

        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        self.assertFalse(period.is_complete)
        self.assertFalse(period.electricity_recorded)
        self.assertFalse(period.water_recorded)
        self.assertIn("الكهرباء", period.missing_data_notes)
        self.assertIn("المياه", period.missing_data_notes)
        with self.assertRaises(ValidationError):
            finalize_costing_period(period=period, actor=self.staff_user)

    def test_missing_labor_is_incomplete_and_explicit_zero_labor_is_complete(self):
        self._create_production_run(
            product=self.product_a, line=self.line_1,
            planned_qty=Decimal("100"), actual_paper_kg=Decimal("200"),
            actual_foil_rolls=Decimal("1"), good_qty=Decimal("100"), scrap_qty=Decimal("0"),
        )
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("300.00"))
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.WATER, Decimal("120.50"))

        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        self.assertFalse(period.labor_recorded)
        self.assertFalse(period.is_complete)
        self.assertIn("لم تُسجل مصاريف العمالة", period.missing_data_notes)
        with self.assertRaises(ValidationError):
            finalize_costing_period(period=period, actor=self.staff_user)

        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.LABOR, Decimal("0.00"))
        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        self.assertTrue(period.labor_recorded)
        self.assertTrue(period.is_complete)
        self.assertNotIn("لم تُسجل مصاريف العمالة", period.missing_data_notes)
        self.assertEqual(period.labor_total, Decimal("0.00"))

    def test_line_fallback_allocation_requires_review_and_blocks_finalize(self):
        self._create_production_run(
            product=self.product_a, line=self.line_1,
            planned_qty=Decimal("100"), actual_paper_kg=Decimal("200"),
            actual_foil_rolls=Decimal("1"), good_qty=Decimal("100"), scrap_qty=Decimal("0"),
        )
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.LABOR, Decimal("0.00"))
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("300.00"))
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.WATER, Decimal("120.50"))
        # Line 2 has no production this month; the expense falls back to all
        # products, which is a review-required allocation, not a clean result.
        self._add_expense(
            self.year, self.month, MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL,
            Decimal("50.00"), scope=MonthlyExpense.AllocationScope.LINE, line=self.line_2,
        )

        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        self.assertFalse(period.is_complete)
        self.assertIn("خط الإنتاج", period.missing_data_notes)
        self.assertIn("مراجعة", period.missing_data_notes)
        entry = period.product_entries.get(product=self.product_a)
        self.assertEqual(entry.calculation_status, "INCOMPLETE")
        with self.assertRaises(ValidationError):
            finalize_costing_period(period=period, actor=self.staff_user)

    def test_product_scoped_fallback_allocation_requires_review(self):
        self._create_production_run(
            product=self.product_a, line=self.line_1,
            planned_qty=Decimal("100"), actual_paper_kg=Decimal("200"),
            actual_foil_rolls=Decimal("1"), good_qty=Decimal("100"), scrap_qty=Decimal("0"),
        )
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.LABOR, Decimal("0.00"))
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("300.00"))
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.WATER, Decimal("120.50"))
        self._add_expense(
            self.year, self.month, MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL,
            Decimal("50.00"), scope=MonthlyExpense.AllocationScope.PRODUCT, product=self.product_b,
        )

        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        self.assertFalse(period.is_complete)
        self.assertIn("بدون إنتاج", period.missing_data_notes)
        self.assertIn("مراجعة", period.missing_data_notes)
        entry = period.product_entries.get(product=self.product_a)
        self.assertEqual(entry.calculation_status, "INCOMPLETE")
        with self.assertRaises(ValidationError):
            finalize_costing_period(period=period, actor=self.staff_user)

    def test_period_compare_view_reads_utility_totals_across_months(self):
        self._create_production_run(
            product=self.product_a, line=self.line_1,
            planned_qty=Decimal("100"), actual_paper_kg=Decimal("200"),
            actual_foil_rolls=Decimal("1"), good_qty=Decimal("100"), scrap_qty=Decimal("0"),
        )
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("300.00"))
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.WATER, Decimal("120.50"))
        calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)

        self.client.login(username=self.staff_user.username, password="test-password")
        resp = self.client.get(reverse("costing:period_compare"))
        self.assertEqual(resp.status_code, 200)
        # Electricity and water columns/labels are rendered, with the product present.
        self.assertContains(resp, "المياه")
        self.assertContains(resp, "الكهرباء")
        self.assertContains(resp, self.product_a.sku)

    # --- Additional workflow / integrity coverage (electricity & water focus) ---

    def test_period_stores_explicit_start_and_end_dates(self):
        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        last_day = calendar.monthrange(self.year, self.month)[1]
        self.assertEqual(period.start_date, date(self.year, self.month, 1))
        self.assertEqual(period.end_date, date(self.year, self.month, last_day))
        self.assertEqual(
            period.resolved_date_range(),
            (date(self.year, self.month, 1), date(self.year, self.month, last_day)),
        )

    def test_january_and_february_utilities_are_isolated_in_product_costs(self):
        target_year = self.year - 1
        self._backdate_prices(target_year)
        self._create_backdated_run(
            self.product_a, self.line_1,
            Decimal("200"), Decimal("1"), Decimal("100"), Decimal("0"), target_year, 1,
        )

        self._add_expense(target_year, 1, MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("300.00"))
        self._add_expense(target_year, 1, MonthlyExpense.ExpenseType.WATER, Decimal("120.00"))
        self._add_expense(target_year, 1, MonthlyExpense.ExpenseType.LABOR, Decimal("0.00"))
        self._add_expense(target_year, 2, MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("999.00"))
        self._add_expense(target_year, 2, MonthlyExpense.ExpenseType.WATER, Decimal("111.00"))

        january = calculate_monthly_costing(year=target_year, month=1, actor=self.staff_user)
        entry = january.product_entries.get(product=self.product_a)
        # January's product cost carries January's own utility bills.
        self.assertEqual(entry.allocated_electricity_cost, Decimal("300.00"))
        self.assertEqual(entry.allocated_water_cost, Decimal("120.00"))
        self.assertEqual(
            entry.total_production_cost,
            Decimal("10200.00") + Decimal("300.00") + Decimal("120.00"),
        )
        self.assertTrue(january.is_complete)

        february = calculate_monthly_costing(year=target_year, month=2, actor=self.staff_user)
        # February uses only its own bills; nothing is carried forward from January.
        self.assertEqual(february.electricity_total, Decimal("999.00"))
        self.assertEqual(february.water_total, Decimal("111.00"))
        self.assertEqual(february.product_entries.count(), 0)
        self.assertEqual(february.materials_total, Decimal("0.00"))
        self.assertEqual(february.total_production_cost, Decimal("1110.00"))

        # January's stored figures are unchanged by February's calculation.
        january.refresh_from_db()
        self.assertEqual(january.electricity_total, Decimal("300.00"))
        self.assertEqual(january.water_total, Decimal("120.00"))

    def test_utility_allocation_reconciles_exactly_under_every_policy(self):
        self._create_production_run(
            product=self.product_a, line=self.line_1,
            planned_qty=Decimal("300"), actual_paper_kg=Decimal("600"),
            actual_foil_rolls=Decimal("3"), good_qty=Decimal("300"), scrap_qty=Decimal("0"),
        )
        self._create_production_run(
            product=self.product_b, line=self.line_2,
            planned_qty=Decimal("100"), actual_paper_kg=Decimal("500"),
            actual_foil_rolls=Decimal("0"), good_qty=Decimal("100"), scrap_qty=Decimal("0"),
        )
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("432.77"))
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.WATER, Decimal("263.19"))
        self._add_expense(
            self.year, self.month, MonthlyExpense.ExpenseType.WATER, Decimal("100.00"),
            scope=MonthlyExpense.AllocationScope.LINE, line=self.line_1,
        )

        for policy in (
            CostingPeriod.AllocationPolicy.UNITS_PRODUCED,
            CostingPeriod.AllocationPolicy.MATERIAL_COST,
            CostingPeriod.AllocationPolicy.MATERIAL_WEIGHT,
            CostingPeriod.AllocationPolicy.EQUAL_PER_LINE,
        ):
            period = calculate_monthly_costing(
                year=self.year, month=self.month, allocation_policy=policy, actor=self.staff_user,
            )
            self.assertEqual(period.electricity_total, Decimal("432.77"))
            self.assertEqual(period.water_total, Decimal("363.19"))
            self.assertEqual(
                sum(e.allocated_electricity_cost for e in period.product_entries.all()), Decimal("432.77")
            )
            self.assertEqual(
                sum(e.allocated_water_cost for e in period.product_entries.all()), Decimal("363.19")
            )

    def test_confirmed_zero_utility_is_recorded_but_missing_utility_blocks_complete(self):
        self._create_production_run(
            product=self.product_a, line=self.line_1,
            planned_qty=Decimal("100"), actual_paper_kg=Decimal("200"),
            actual_foil_rolls=Decimal("1"), good_qty=Decimal("100"), scrap_qty=Decimal("0"),
        )
        # Electricity recorded as an explicit, confirmed zero cost.
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("0.00"))
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.LABOR, Decimal("0.00"))

        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        self.assertEqual(period.electricity_total, Decimal("0.00"))
        # A recorded zero is NOT treated as a missing bill.
        self.assertNotIn("لم تُسجل مصاريف الكهرباء", period.missing_data_notes)
        # Water was not recorded at all → still incomplete and cannot be finalized.
        self.assertIn("لم تُسجل مصاريف المياه", period.missing_data_notes)
        self.assertFalse(period.is_complete)
        with self.assertRaises(ValidationError):
            finalize_costing_period(period=period, actor=self.staff_user)

        # An explicit water zero completes the month.
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.WATER, Decimal("0.00"))
        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        self.assertTrue(period.is_complete)

    def test_recalculation_does_not_duplicate_utility_costs(self):
        self._create_production_run(
            product=self.product_a, line=self.line_1,
            planned_qty=Decimal("100"), actual_paper_kg=Decimal("200"),
            actual_foil_rolls=Decimal("1"), good_qty=Decimal("100"), scrap_qty=Decimal("0"),
        )
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("300.00"))
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.WATER, Decimal("120.50"))

        first = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        second = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)

        self.assertEqual(second.product_entries.count(), 1)
        self.assertEqual(second.order_entries.count(), 1)
        self.assertEqual(second.electricity_total, Decimal("300.00"))
        self.assertEqual(second.water_total, Decimal("120.50"))
        self.assertEqual(
            sum(e.allocated_electricity_cost for e in second.product_entries.all()), Decimal("300.00")
        )
        self.assertEqual(sum(e.allocated_water_cost for e in second.product_entries.all()), Decimal("120.50"))
        self.assertEqual(second.total_production_cost, first.total_production_cost)

    def test_period_detail_and_product_breakdown_show_utilities_separately(self):
        self._create_production_run(
            product=self.product_a, line=self.line_1,
            planned_qty=Decimal("100"), actual_paper_kg=Decimal("200"),
            actual_foil_rolls=Decimal("1"), good_qty=Decimal("100"), scrap_qty=Decimal("0"),
        )
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("300.00"))
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.WATER, Decimal("120.50"))
        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        entry = period.product_entries.get(product=self.product_a)

        self.client.login(username=self.staff_user.username, password="test-password")
        detail = self.client.get(reverse("costing:period_detail", args=[period.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "مصاريف الكهرباء")
        self.assertContains(detail, "مصاريف المياه")

        breakdown = self.client.get(reverse("costing:product_breakdown", args=[entry.pk]))
        self.assertEqual(breakdown.status_code, 200)
        self.assertContains(breakdown, "نصيب الكهرباء")
        self.assertContains(breakdown, "نصيب المياه")

    def test_period_compare_shows_selected_product_values_and_month_deltas(self):
        target_year = self.year - 1
        self._backdate_prices(target_year)
        self._create_backdated_run(
            self.product_a, self.line_1,
            Decimal("200"), Decimal("1"), Decimal("100"), Decimal("0"), target_year, 1,
        )
        self._create_backdated_run(
            self.product_a, self.line_1,
            Decimal("300"), Decimal("2"), Decimal("100"), Decimal("0"), target_year, 2,
        )

        self._add_expense(target_year, 1, MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("300.00"))
        self._add_expense(target_year, 1, MonthlyExpense.ExpenseType.WATER, Decimal("120.00"))
        self._add_expense(target_year, 2, MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("450.00"))
        self._add_expense(target_year, 2, MonthlyExpense.ExpenseType.WATER, Decimal("90.00"))
        calculate_monthly_costing(year=target_year, month=1, actor=self.staff_user)
        calculate_monthly_costing(year=target_year, month=2, actor=self.staff_user)

        self.client.login(username=self.staff_user.username, password="test-password")
        resp = self.client.get(reverse("costing:period_compare"), {"product": self.product_a.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["selected_product"], self.product_a)

        rows = resp.context["detail_rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["entry"].allocated_electricity_cost, Decimal("300.00"))
        self.assertEqual(rows[0]["entry"].allocated_water_cost, Decimal("120.00"))
        self.assertIsNone(rows[0]["delta_total"])
        self.assertEqual(rows[1]["entry"].allocated_electricity_cost, Decimal("450.00"))
        self.assertEqual(rows[1]["delta_electricity"], Decimal("150.00"))
        self.assertEqual(rows[1]["delta_water"], Decimal("-30.00"))
        self.assertIsNotNone(rows[1]["delta_total"])
        # Electricity and water appear as their own labelled comparison columns.
        self.assertContains(resp, "Δ الكهرباء")
        self.assertContains(resp, "Δ المياه")

    def test_staff_can_record_zero_cost_utility_through_the_form(self):
        self.client.login(username=self.staff_user.username, password="test-password")
        resp = self.client.post(
            reverse("costing:expense_create"),
            {
                "year": self.year,
                "month": self.month,
                "expense_type": MonthlyExpense.ExpenseType.ELECTRICITY,
                "scope": MonthlyExpense.AllocationScope.FACTORY,
                "amount": "0.00",
                "description": "فاتورة كهرباء بدون استهلاك",
                "notes": "",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            MonthlyExpense.objects.filter(
                year=self.year,
                month=self.month,
                expense_type=MonthlyExpense.ExpenseType.ELECTRICITY,
                amount=Decimal("0.00"),
            ).exists()
        )

    def test_order_product_and_period_totals_reconcile_exactly(self):
        """Order -> product -> period totals must reconcile to the cent.

        Product A has two orders (so the order split is non-trivial), product B
        one order carrying recorded scrap under the separate-abnormal waste
        policy, and the month mixes factory-, line-, and product-scoped
        expenses across all four categories. Every figure below is compared as
        an exact Decimal: material consumed once, each expense bucket allocated
        once, and waste shown separately rather than added on top of the total.
        """
        self._create_production_run(
            product=self.product_a, line=self.line_1,
            planned_qty=Decimal("180"), actual_paper_kg=Decimal("360"),
            actual_foil_rolls=Decimal("0"), good_qty=Decimal("180"), scrap_qty=Decimal("0"),
        )
        self._create_production_run(
            product=self.product_a, line=self.line_1,
            planned_qty=Decimal("120"), actual_paper_kg=Decimal("240"),
            actual_foil_rolls=Decimal("2"), good_qty=Decimal("120"), scrap_qty=Decimal("0"),
        )
        self._create_production_run(
            product=self.product_b, line=self.line_2,
            planned_qty=Decimal("150"), actual_paper_kg=Decimal("500"),
            actual_foil_rolls=Decimal("0"), good_qty=Decimal("100"), scrap_qty=Decimal("50"),
            measured_waste_base=Decimal("166.666666666667"),
        )

        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.LABOR, Decimal("1000.00"))
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("300.00"))
        self._add_expense(self.year, self.month, MonthlyExpense.ExpenseType.WATER, Decimal("120.50"))
        self._add_expense(
            self.year, self.month, MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL, Decimal("200.00"),
            scope=MonthlyExpense.AllocationScope.LINE, line=self.line_1,
        )
        self._add_expense(
            self.year, self.month, MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL, Decimal("50.00"),
            scope=MonthlyExpense.AllocationScope.PRODUCT, product=self.product_b,
        )

        period = calculate_monthly_costing(
            year=self.year, month=self.month,
            waste_policy=CostingPeriod.WastePolicy.SEPARATE_ABNORMAL,
            actor=self.staff_user,
        )
        entries = {entry.product_id: entry for entry in period.product_entries.all()}
        order_entries = list(period.order_entries.all())
        entry_a = entries[self.product_a.pk]
        entry_b = entries[self.product_b.pk]

        def money_sum(field, rows):
            return sum((getattr(row, field) for row in rows), Decimal("0.00"))

        # --- Material: consumed once, then split into good-assigned + waste ---
        self.assertEqual(entry_a.actual_material_cost, Decimal("30400.00"))  # 600 kg*50 + 2*200
        self.assertEqual(entry_b.actual_material_cost, Decimal("25000.00"))  # 500 kg*50
        self.assertEqual(period.materials_total, entry_a.actual_material_cost + entry_b.actual_material_cost)
        # Product B: 50 of 150 recorded output units are scrap.
        self.assertEqual(entry_b.waste_material_cost, Decimal("8333.33"))
        self.assertEqual(entry_b.material_cost_assigned_to_good, Decimal("16666.67"))
        self.assertEqual(entry_a.waste_material_cost, Decimal("0.00"))
        for entry in (entry_a, entry_b):
            self.assertEqual(
                entry.material_cost_assigned_to_good + entry.waste_material_cost,
                entry.actual_material_cost,
            )

        # --- Expenses: each recorded bucket is allocated exactly once ---
        self.assertEqual(period.labor_total, Decimal("1000.00"))
        self.assertEqual(period.electricity_total, Decimal("300.00"))
        self.assertEqual(period.water_total, Decimal("120.50"))
        self.assertEqual(period.other_industrial_total, Decimal("250.00"))  # 200 line + 50 product

        # --- Order level -> product level -> period level for every category ---
        allocations = (
            ("allocated_labor_cost", period.labor_total),
            ("allocated_electricity_cost", period.electricity_total),
            ("allocated_water_cost", period.water_total),
            ("allocated_other_cost", period.other_industrial_total),
        )
        for field, bucket_total in allocations:
            self.assertEqual(money_sum(field, order_entries), bucket_total)
            self.assertEqual(money_sum(field, entries.values()), bucket_total)

        # Per product: the sum of its own orders equals its product entry exactly.
        for product_id, entry in entries.items():
            rows = [row for row in order_entries if row.product_id == product_id]
            self.assertTrue(rows)
            self.assertEqual(money_sum("total_production_cost", rows), entry.total_production_cost)
            self.assertEqual(money_sum("actual_material_cost", rows), entry.actual_material_cost)
            self.assertEqual(
                money_sum("material_cost_assigned_to_good", rows), entry.material_cost_assigned_to_good
            )
            self.assertEqual(money_sum("waste_material_cost", rows), entry.waste_material_cost)

        # Period totals equal the order roll-up and the product roll-up.
        self.assertEqual(money_sum("actual_material_cost", order_entries), period.materials_total)
        self.assertEqual(money_sum("total_production_cost", order_entries), period.total_production_cost)
        self.assertEqual(money_sum("total_production_cost", entries.values()), period.total_production_cost)

        # Waste is reported separately and never added on top of the total:
        # total production cost = good-assigned material + the four expense buckets.
        self.assertEqual(
            period.total_production_cost,
            entry_a.material_cost_assigned_to_good
            + entry_b.material_cost_assigned_to_good
            + period.labor_total
            + period.electricity_total
            + period.water_total
            + period.other_industrial_total,
        )
        # Consumed material = assigned to good + separate waste (counted once).
        self.assertEqual(
            period.materials_total,
            entry_a.material_cost_assigned_to_good
            + entry_b.material_cost_assigned_to_good
            + entry_a.waste_material_cost
            + entry_b.waste_material_cost,
        )
