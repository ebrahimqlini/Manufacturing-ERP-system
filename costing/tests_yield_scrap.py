"""Yield vs. recorded-scrap reconciliation, valuation, and snapshot tests.

Proves that shortfall and leftovers never become scrap, order-level waste cost
is only an estimate, affected costing stays incomplete, and historical yield
classification uses the threshold snapshot taken at calculation time.
"""

from datetime import date, timedelta
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product, ProductCategory, ProductUnitConversion, UnitOfMeasure
from inventory.models import StockMovement, StorageLocation, Warehouse
from inventory.services import post_movement

from manufacturing.counter_services import (
    approve_counter_reading,
    close_counter_interval,
    start_counter_interval,
)
from manufacturing.models import (
    BillOfMaterials,
    BOMComponent,
    MachineCounterReading,
    ProductionLine,
    ProductionOperation,
    ProductionOrder,
    Routing,
    RoutingStep,
    MaterialIssue,
)
from manufacturing.services import (
    complete_operation,
    complete_order,
    issue_material,
    record_production_material_waste,
    receive_finished_goods,
    record_operation_progress,
    record_production_result,
    release_order,
    save_draft_order,
)

from .models import (
    CostingPeriod,
    MonthlyExpense,
    MonthlyProfitPeriod,
    ProductCostingEntry,
    ProductionOrderCostingEntry,
    RawMaterialPrice,
    RollYieldStandard,
)
from .profit_services import calculate_monthly_profit
from .services import calculate_monthly_costing, finalize_costing_period
from .yield_services import (
    ScrapClass,
    classify_scrap_percent,
    monthly_scrap_summary,
    order_yield_report,
    yield_report,
)


class YieldScrapFixtureMixin:
    """Full real flow: released order → issue → result → monthly costing."""

    def setup_yield_fixture(self):
        User = get_user_model()
        from django.contrib.auth.models import Permission

        suffix = uuid.uuid4().hex[:8].upper()
        self.staff = User.objects.create_user(
            username=f"yield-staff-{suffix}", password="test-password", is_staff=True
        )
        perms = Permission.objects.filter(
            content_type__app_label__in=[
                "manufacturing", "inventory", "costing", "purchasing", "sales"
            ]
        )
        self.staff.user_permissions.set(perms)
        self.reader = User.objects.create_user(
            username=f"yield-reader-{suffix}", password="test-password"
        )
        self.pcs = UnitOfMeasure.objects.create(code=f"P{suffix}", name_ar="قطعة", dimension="count")
        self.kg = UnitOfMeasure.objects.create(code=f"K{suffix}", name_ar="كجم", dimension="mass")
        # A "roll" here equals 12 kg of the raw material (same dimension).
        self.roll = UnitOfMeasure.objects.create(code=f"R{suffix}", name_ar="رول", dimension="mass")
        finished_category = ProductCategory.objects.get_or_create(
            code="finished-goods", defaults={"name_ar": "منتج تام"}
        )[0]
        raw_category = ProductCategory.objects.get_or_create(
            code="raw-material", defaults={"name_ar": "مادة خام"}
        )[0]
        self.product = Product.objects.create(
            sku=f"YLD-{suffix}", name_ar="منتج العائد", category=finished_category, base_uom=self.pcs
        )
        self.material = Product.objects.create(
            sku=f"YMAT-{suffix}", name_ar="مادة العائد", category=raw_category, base_uom=self.kg
        )
        ProductUnitConversion.objects.create(
            product=self.material, unit=self.roll, factor=Decimal("12"), active=True
        )
        self.line = ProductionLine.objects.create(code=f"YL-{suffix}", name_ar="خط العائد")
        press = ProductionOperation.objects.create(code=f"YP{suffix}", name_ar="كبس")
        warehouse = Warehouse.objects.create(code=f"YW-{suffix}", name_ar="مخزن العائد")
        self.location = StorageLocation.objects.create(
            warehouse=warehouse, code="RAW", name_ar="موقع المواد"
        )
        self.bom = BillOfMaterials.objects.create(
            product=self.product, version=1, output_quantity=Decimal("100"), output_unit=self.pcs,
            created_by=self.staff,
        )
        # 12 kg per 100 pcs: one roll per order-batch of 100 cartons.
        BOMComponent.objects.create(
            bom=self.bom, material=self.material, quantity=Decimal("12"), unit=self.kg, sequence=10
        )
        self.routing = Routing.objects.create(
            product=self.product, production_line=self.line, version=1, created_by=self.staff
        )
        RoutingStep.objects.create(routing=self.routing, operation=press, sequence=1)
        today = timezone.now().date()
        self.year, self.month = today.year, today.month
        RawMaterialPrice.objects.create(
            material=self.material,
            price_per_unit=Decimal("10.0000"),
            unit=self.kg,
            effective_date=date(self.year, self.month, 1),
            created_by=self.staff,
        )
        self.standard = RollYieldStandard.objects.create(
            raw_material=self.material,
            product=self.product,
            production_line=self.line,
            expected_cartons_per_roll=Decimal("100"),
            roll_unit=self.roll,
            carton_unit=self.pcs,
            supplier_name="مورد الاختبار",
            specification_reference=f"SPEC-{suffix}",
            revision=1,
            effective_date=date(self.year, self.month, 1),
            target_yield_percent=Decimal("95.00"),
            max_scrap_percent=Decimal("5.00"),
            created_by=self.staff,
        )
        self._seq = 0

    def make_order(self, *, quantity="1000"):
        self._seq += 1
        order = save_draft_order(
            actor=self.staff, product=self.product, production_line=self.line,
            bom=self.bom, routing=self.routing, source_location=self.location,
            planned_quantity=Decimal(quantity), planned_unit=self.pcs, notes="",
        )
        release_order(order=order, actor=self.staff)
        order.refresh_from_db()
        return order

    def produce(self, order, *, good, scrap, occurred_at=None, material_waste_base=None):
        requirement = order.requirements.get()
        post_movement(
            reference=f"YLD-RCPT-{uuid.uuid4().hex[:8]}",
            movement_type=StockMovement.Type.RECEIPT,
            product=self.material,
            quantity=Decimal("500"),
            unit=self.kg,
            actor=self.staff,
            destination_location=self.location,
        )
        issue_material(
            order=order, requirement=requirement, quantity=Decimal("120"),
            unit=self.kg, reference=f"YLD-ISS-{uuid.uuid4().hex[:8]}", actor=self.staff,
        )
        result, _ = record_production_result(
            order=order, good_quantity=Decimal(good), scrap_quantity=Decimal(scrap),
            unit=self.pcs, notes="", actor=self.staff, occurred_at=occurred_at,
        )
        if material_waste_base is not None:
            record_production_material_waste(
                result=result, material=self.material,
                quantity_base=Decimal(material_waste_base),
                material_issue=MaterialIssue.objects.get(order=order, requirement=requirement),
                actor=self.staff,
            )
        return result


class ClassifyScrapPercentTests(TestCase):
    def test_exact_threshold_is_within(self):
        self.assertEqual(
            classify_scrap_percent(
                Decimal("5.00"), has_standard=True, max_scrap_percent=Decimal("5.00")
            ),
            ScrapClass.WITHIN,
        )

    def test_above_threshold(self):
        self.assertEqual(
            classify_scrap_percent(
                Decimal("5.01"), has_standard=True, max_scrap_percent=Decimal("5.00")
            ),
            ScrapClass.ABOVE,
        )

    def test_no_threshold_approved(self):
        self.assertEqual(
            classify_scrap_percent(Decimal("9.00"), has_standard=True, max_scrap_percent=None),
            ScrapClass.NO_THRESHOLD,
        )

    def test_no_standard(self):
        self.assertEqual(
            classify_scrap_percent(Decimal("1.00"), has_standard=False, max_scrap_percent=None),
            ScrapClass.NO_STANDARD,
        )

    def test_insufficient_data(self):
        self.assertEqual(
            classify_scrap_percent(None, has_standard=True, max_scrap_percent=Decimal("5")),
            ScrapClass.INSUFFICIENT_DATA,
        )

    def test_pending_approval_wins_over_data(self):
        self.assertEqual(
            classify_scrap_percent(
                Decimal("9.00"), has_standard=True, max_scrap_percent=Decimal("5"),
                entry_complete=False,
            ),
            ScrapClass.PENDING_APPROVAL,
        )


class YieldSnapshotTests(YieldScrapFixtureMixin, TestCase):
    """The costing entry stores the exact standard used at calculation time."""

    def setUp(self):
        self.setup_yield_fixture()
        self.order = self.make_order()
        self.result = self.produce(self.order, good="900", scrap="100")
        self.period = calculate_monthly_costing(
            year=self.year, month=self.month, actor=self.staff,
            waste_policy=CostingPeriod.WastePolicy.SEPARATE_ABNORMAL,
        )
        self.entry = ProductCostingEntry.objects.get(
            period=self.period, product=self.product
        )

    def test_costing_entry_snapshots_standard_revision_and_thresholds(self):
        self.assertEqual(self.entry.yield_standard, self.standard)
        self.assertEqual(self.entry.yield_standard_revision, 1)
        self.assertEqual(self.entry.yield_scrap_threshold_percent, Decimal("5.00"))
        self.assertEqual(self.entry.yield_target_percent, Decimal("95.00"))

    def test_yield_analysis_persisted(self):
        # 120 kg ÷ 12 kg/roll = 10 rolls × 100 cartons = 1000 expected.
        self.assertEqual(self.entry.rolls_consumed, Decimal("10.0000"))
        self.assertEqual(self.entry.expected_cartons, Decimal("1000.0000"))
        self.assertEqual(self.entry.actual_good_cartons, Decimal("900.0000"))
        # Shortfall 100/1000 = 10% — an efficiency measure, not scrap.
        self.assertEqual(self.entry.yield_shortfall_percent, Decimal("10.00"))

    def test_output_scrap_does_not_allocate_raw_material_cost_and_blocks_finalization(self):
        # Rejected finished output is not evidence of a damaged raw material.
        self.assertEqual(self.entry.actual_material_cost, Decimal("1200.00"))
        self.assertEqual(self.entry.waste_material_cost, Decimal("0.00"))
        self.assertEqual(self.entry.material_cost_assigned_to_good, Decimal("1200.00"))
        self.assertEqual(self.entry.calculation_status, "INCOMPLETE")
        self.assertFalse(self.period.is_complete)
        self.assertEqual(self.period.material_valuation_method, "LAST_PURCHASE_COST")
        with self.assertRaises(ValidationError):
            finalize_costing_period(period=self.period, actor=self.staff)

    def test_later_spec_revision_never_rewrites_history(self):
        # Owner revises the live spec after the month was costed.
        self.standard.revision = 2
        self.standard.max_scrap_percent = Decimal("50.00")
        self.standard.save()
        report = yield_report(year=self.year, month=self.month)
        row = next(r for r in report["rows"] if r["product"] == self.product)
        # Historical classification still uses the snapshot (threshold 5%).
        self.assertEqual(row["classification"], ScrapClass.ABOVE)
        self.assertEqual(row["threshold_percent"], Decimal("5.00"))
        self.assertEqual(row["standard_revision"], 1)
        self.assertEqual(row["scrap_percent"], Decimal("10.00"))
        # The operational per-order view follows the LIVE standard instead.
        live = order_yield_report(order=self.order)
        self.assertEqual(live["classification"], ScrapClass.WITHIN)
        self.assertEqual(live["threshold_percent"], Decimal("50.00"))

    def test_unavailable_price_yields_explicit_no_valuation_state(self):
        # Output scrap is not raw-material waste. Add a measured material event
        # while making the only dated fallback future-dated; it must stay unvalued.
        RawMaterialPrice.objects.filter(material=self.material).update(
            effective_date=date(self.year, self.month, 1) + timedelta(days=60)
        )
        record_production_material_waste(
            result=self.result, material=self.material, quantity_base=Decimal("12"),
            material_issue=MaterialIssue.objects.get(order=self.order), actor=self.staff,
        )
        self.period = calculate_monthly_costing(
            year=self.year, month=self.month, actor=self.staff,
            waste_policy=CostingPeriod.WastePolicy.SEPARATE_ABNORMAL,
        )
        entry = ProductCostingEntry.objects.get(period=self.period, product=self.product)
        self.assertEqual(entry.waste_material_cost, Decimal("0.00"))
        self.assertEqual(entry.calculation_status, "INCOMPLETE")
        report = yield_report(year=self.year, month=self.month)
        row = next(r for r in report["rows"] if r["product"] == self.product)
        self.assertIsNotNone(row["valuation"])
        self.assertIsNone(row["valuation"]["price"])
        self.assertIsNotNone(row["valuation"]["note"])
        self.assertEqual(row["separated_scrap_cost"], Decimal("0.00"))
        self.assertEqual(row["scrap_cost_basis"], "EVENT_TIME_MEASURED_MATERIAL_WASTE")
        self.assertEqual(row["scrap_cost_valuation_status"], "INCOMPLETE_EVIDENCE")
        self.assertEqual(entry.actual_material_cost, Decimal("1200.00"))
        self.assertEqual(entry.waste_material_cost, Decimal("0.00"))
        # Removing a dated price does not rewrite the unvalued event snapshot.
        RawMaterialPrice.objects.filter(material=self.material).delete()
        report = yield_report(year=self.year, month=self.month)
        row = next(r for r in report["rows"] if r["product"] == self.product)
        self.assertIsNone(row["valuation"]["price"])
        self.assertIsNotNone(row["valuation"]["note"])
        self.assertIn("تقييم موثوق وقت الترحيل", row["valuation"]["note"])
        self.assertEqual(row["separated_scrap_cost"], Decimal("0.00"))


class OrderYieldReportTests(YieldScrapFixtureMixin, TestCase):
    def setUp(self):
        self.setup_yield_fixture()
        self.order = self.make_order()
        self.produce(self.order, good="900", scrap="100")

    def test_measured_scrap_percent_never_derived_from_shortfall(self):
        # Make expected output differ from recorded output so the two measures
        # are numerically distinct: 10 rolls × 110 = 1100 expected vs 900 good
        # → shortfall 18.18%, while measured scrap stays 100/1000 = 10%.
        self.standard.expected_cartons_per_roll = Decimal("110")
        self.standard.save()
        period = calculate_monthly_costing(
            year=self.year, month=self.month, actor=self.staff,
            waste_policy=CostingPeriod.WastePolicy.SEPARATE_ABNORMAL,
        )
        entry = ProductCostingEntry.objects.get(period=period, product=self.product)
        self.assertEqual(entry.yield_shortfall_percent, Decimal("18.18"))
        report = order_yield_report(order=self.order)
        self.assertEqual(report["good_base"], Decimal("900.0000"))
        self.assertEqual(report["scrap_base"], Decimal("100.0000"))
        # Scrap % is measured scrap ÷ total measured output — not shortfall.
        self.assertEqual(report["scrap_percent"], Decimal("10.00"))
        self.assertNotEqual(report["scrap_percent"], entry.yield_shortfall_percent)
        self.assertEqual(report["yield_percent"], Decimal("90.00"))
        self.assertEqual(report["classification"], ScrapClass.ABOVE)

    def test_scrap_event_reports_system_posting_time_not_entered_production_date(self):
        order = self.make_order(quantity="1000")
        entered_date = timezone.now() - timedelta(days=60)
        result = self.produce(order, good="90", scrap="10", occurred_at=entered_date)
        self.assertEqual(result.occurred_at, entered_date)
        self.assertGreater(result.posted_at, result.occurred_at)
        self.assertNotEqual(
            (result.posted_at.year, result.posted_at.month),
            (result.occurred_at.year, result.occurred_at.month),
        )
        report = order_yield_report(order=order)
        self.assertEqual(report["scrap_event_times"][0]["posted_at"], result.posted_at)
        self.assertEqual(
            report["scrap_cost_valuation_status"], "UNAVAILABLE_MATERIAL_ATTRIBUTION"
        )

    def test_material_leftovers_are_unreconciled_not_scrap(self):
        report = order_yield_report(order=self.order)
        rows = report["materials"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # 120 kg issued; expected for 900 good pcs = 108 kg; 12 kg difference.
        self.assertEqual(row["issued_base"], Decimal("120.0000"))
        self.assertEqual(row["expected_for_recorded_output_base"], Decimal("108.0000"))
        self.assertEqual(row["unreconciled_base"], Decimal("12.0000"))
        # Deliberately unclassified: the data model cannot separate usable
        # remnants from material consumed by recorded damaged units.
        self.assertEqual(row["label"], "UNRECONCILED_MATERIAL")
        # Leftovers never inflate any scrap quantity or cost.
        self.assertEqual(report["scrap_base"], Decimal("100.0000"))
        self.assertTrue(
            any("غير مفسَّرة" in note for note in report["notes"])
        )
        # The note states BOTH possible compositions and no classification,
        # valuation, deduction, or sale/recycling rule.
        self.assertTrue(
            any("وحدات الهالك المسجلة" in note for note in report["notes"])
        )
        self.assertTrue(
            any("لا يُصنَّف هالكًا" in note and "يُخصم" in note for note in report["notes"])
        )
        self.assertTrue(
            any("بيعه أو إعادة تدويره" in note for note in report["notes"])
        )
        # No monetary figure is ever attached to the unexplained difference.
        self.assertNotIn("cost", row)
        self.assertNotIn("value", row)

    def test_counter_unconfirmed_never_presented_as_verified_output(self):
        reading = start_counter_interval(
            actor=self.staff, order=self.order, production_line=self.line,
            unit=self.pcs, start_reading=Decimal("0"),
        )
        close_counter_interval(
            reading=reading, actor=self.staff,
            end_reading=Decimal("950"), rejected_quantity=Decimal("50"),
        )
        approve_counter_reading(reading=reading, actor=self.staff)
        report = order_yield_report(order=self.order)
        counter = report["counter"]
        # Default line behavior is UNCONFIRMED: provisional measurement only.
        self.assertFalse(counter["is_verified"])
        self.assertEqual(counter["line_behavior"], "UNCONFIRMED")
        self.assertEqual(counter["confirmed_base"], Decimal("900.0000"))
        self.assertTrue(any("غير مؤكد" in note for note in report["notes"]))

    def test_counter_confirmed_when_behavior_counts_all_output(self):
        self.line.counter_behavior = ProductionLine.CounterBehavior.COUNTS_ALL_OUTPUT
        self.line.save()
        reading = start_counter_interval(
            actor=self.staff, order=self.order, production_line=self.line,
            unit=self.pcs, start_reading=Decimal("0"),
        )
        closed = close_counter_interval(
            reading=reading, actor=self.staff,
            end_reading=Decimal("950"), rejected_quantity=Decimal("50"),
        )
        approve_counter_reading(reading=closed, actor=self.staff)
        report = order_yield_report(order=self.order)
        counter = report["counter"]
        self.assertTrue(counter["is_verified"])
        # Counter counts all output: accepted = 950 − 50 rejected = 900.
        self.assertEqual(counter["confirmed_base"], Decimal("900.0000"))
        self.assertEqual(counter["variance_vs_recorded_good"], Decimal("0.0000"))

    def test_pending_counter_reading_is_provisional_not_confirmed(self):
        reading = start_counter_interval(
            actor=self.staff, order=self.order, production_line=self.line,
            unit=self.pcs, start_reading=Decimal("0"),
        )
        close_counter_interval(reading=reading, actor=self.staff, end_reading=Decimal("940"))
        report = order_yield_report(order=self.order)
        counter = report["counter"]
        self.assertIsNone(counter["confirmed_base"])
        self.assertEqual(counter["provisional_base"], Decimal("940.0000"))
        self.assertEqual(counter["pending_intervals"], 1)


class MonthlyScrapSummaryTests(YieldScrapFixtureMixin, TestCase):
    def setUp(self):
        self.setup_yield_fixture()
        self.order = self.make_order()
        self.produce(self.order, good="900", scrap="100", material_waste_base="12")
        self.period = calculate_monthly_costing(
            year=self.year, month=self.month, actor=self.staff,
            waste_policy=CostingPeriod.WastePolicy.SEPARATE_ABNORMAL,
        )

    def test_summary_exposes_authoritative_persisted_cost(self):
        summary = monthly_scrap_summary(year=self.year, month=self.month)
        self.assertEqual(summary["good_units"], Decimal("900.0000"))
        self.assertEqual(summary["scrap_units"], Decimal("100.0000"))
        self.assertEqual(summary["scrap_percent"], Decimal("10.00"))
        self.assertEqual(summary["allocated_scrap_cost_estimate"], Decimal("120.00"))
        self.assertEqual(summary["scrap_cost_basis"], "EVENT_TIME_MEASURED_MATERIAL_WASTE")

    def test_summary_for_missing_period_never_invents_values(self):
        summary = monthly_scrap_summary(year=1999, month=1)
        self.assertIsNone(summary["period"])
        self.assertEqual(summary["authoritative_scrap_cost"], Decimal("0.00"))
        self.assertTrue(summary["notes"])


class YieldReportViewTests(YieldScrapFixtureMixin, TestCase):
    def setUp(self):
        self.setup_yield_fixture()
        self.order = self.make_order()
        self.produce(self.order, good="900", scrap="100", material_waste_base="12")
        calculate_monthly_costing(
            year=self.year, month=self.month, actor=self.staff,
            waste_policy=CostingPeriod.WastePolicy.SEPARATE_ABNORMAL,
        )

    def test_report_view_renders_arabic_rtl_with_classification(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("costing:yield_report"),
            {"year": self.year, "month": self.month},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تقرير العائد والهالك الشهري")
        self.assertContains(response, "فوق الحد المعتمد")
        # Decimals render localized (120,00) — the app-wide rendering style.
        self.assertContains(response, "120,00")  # authoritative scrap cost

    def test_report_view_requires_financials_access(self):
        self.client.force_login(self.reader)
        response = self.client.get(reverse("costing:yield_report"))
        self.assertEqual(response.status_code, 403)

    def test_yield_standard_form_exposes_spec_metadata(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("costing:yield_standard_edit", args=[self.standard.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "المورد أو مصدر المواصفة")
        self.assertContains(response, "أقصى نسبة هالك مقبولة %")

    def test_standard_edit_records_updated_by(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("costing:yield_standard_edit", args=[self.standard.pk]),
            {
                "raw_material": self.material.pk,
                "product": self.product.pk,
                "production_line": self.line.pk,
                "expected_cartons_per_roll": "100",
                "roll_unit": self.roll.pk,
                "carton_unit": self.pcs.pk,
                "supplier_name": "مورد محدَّث",
                "specification_reference": "SPEC-2",
                "revision": "2",
                "effective_date": "",
                "target_yield_percent": "95",
                "max_scrap_percent": "6",
                "notes": "",
                "active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.standard.refresh_from_db()
        self.assertEqual(self.standard.updated_by, self.staff)
        self.assertEqual(self.standard.revision, 2)


class LeftoverMaterialIsNeverWasteTests(YieldScrapFixtureMixin, TestCase):
    """Unexplained/leftover material alone must never become a waste deduction.

    Zero recorded damaged output plus a large issued-minus-expected difference:
    even with every deduction gate open (SEPARATE policy, finalized complete
    costing period, COMPLETE entry, COMPLETED order), the profit deduction
    stays zero — only confirmed recorded damage can ever be deducted, and the
    leftover is never valued, deducted, classified, or assumed sellable.
    """

    def setUp(self):
        self.setup_yield_fixture()
        self.order = self.make_order()
        self.result = self.produce(self.order, good="900", scrap="0")
        from manufacturing.services import confirm_no_material_waste
        confirm_no_material_waste(result=self.result, actor=self.staff)

    def test_leftovers_and_unexplained_differences_never_become_waste_cost(self):
        # 120 kg issued; expected for 900 good pcs = 108 kg → 12 kg difference.
        report = order_yield_report(order=self.order)
        row = report["materials"][0]
        self.assertEqual(row["unreconciled_base"], Decimal("12.0000"))
        self.assertEqual(row["label"], "UNRECONCILED_MATERIAL")
        self.assertEqual(report["scrap_base"], Decimal("0.0000"))

        # Explicit 0.00 records for the required monthly expense confirmations:
        # an unrecorded expense would force is_complete=False (never assumed 0).
        for expense_type in (
            MonthlyExpense.ExpenseType.LABOR,
            MonthlyExpense.ExpenseType.ELECTRICITY,
            MonthlyExpense.ExpenseType.WATER,
        ):
            MonthlyExpense.objects.create(
                year=self.year,
                month=self.month,
                expense_type=expense_type,
                amount=Decimal("0.00"),
                scope=MonthlyExpense.AllocationScope.FACTORY,
                description=f"{expense_type} {self.year}/{self.month}",
                expense_date=date(self.year, self.month, 1),
                status=MonthlyExpense.Status.ACTIVE,
                created_by=self.staff,
            )
        period = calculate_monthly_costing(
            year=self.year, month=self.month, actor=self.staff,
            waste_policy=CostingPeriod.WastePolicy.SEPARATE_ABNORMAL,
        )
        entry = ProductCostingEntry.objects.get(period=period, product=self.product)
        order_entry = ProductionOrderCostingEntry.objects.get(
            period=period, order=self.order
        )
        # The full issued material cost stays on the good units: without
        # recorded damaged output nothing is separated as waste.
        self.assertEqual(entry.actual_material_cost, Decimal("1200.00"))
        self.assertEqual(entry.waste_material_cost, Decimal("0.00"))
        self.assertEqual(entry.material_cost_assigned_to_good, Decimal("1200.00"))
        self.assertEqual(order_entry.waste_material_cost, Decimal("0.00"))
        self.assertEqual(order_entry.calculation_status, "COMPLETE")
        summary = monthly_scrap_summary(year=self.year, month=self.month)
        self.assertEqual(summary["authoritative_scrap_cost"], Decimal("0.00"))

        # Open every deduction gate through the real service workflow.
        # 1) Period must be genuinely complete (it is, with explicit 0.00
        #    expense confirmations recorded above).
        period.refresh_from_db()
        self.assertTrue(period.is_complete)
        # 2) Complete the order legitimately: operation progress → operation
        #    completed → full good-quantity receipt → close order.
        operation = self.order.operations.get()
        record_operation_progress(
            order=self.order, order_operation=operation,
            quantity=Decimal("900"), unit=self.pcs, notes="", actor=self.staff,
        )
        complete_operation(
            order=self.order, order_operation=operation, actor=self.staff
        )
        fg_location = StorageLocation.objects.create(
            warehouse=self.location.warehouse,
            code="FG",
            name_ar="موقع المنتج التام",
        )
        receive_finished_goods(
            order=self.order, quantity=Decimal("900"), unit=self.pcs,
            destination_location=fg_location,
            reference=f"YLD-FG-{uuid.uuid4().hex[:8]}", actor=self.staff,
        )
        complete_order(order=self.order, actor=self.staff)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, ProductionOrder.Status.COMPLETED)
        # 3) Finalize the costing period via its service (it recalculates and
        #    re-checks completeness; the SEPARATE waste policy is preserved).
        period = finalize_costing_period(period=period, actor=self.staff)
        self.assertEqual(period.status, CostingPeriod.Status.FINALIZED)
        self.assertTrue(period.is_complete)
        self.assertEqual(
            period.waste_policy, CostingPeriod.WastePolicy.SEPARATE_ABNORMAL
        )
        profit = calculate_monthly_profit(
            year=self.year, month=self.month, actor=self.staff
        )
        # Not a single scrap figure is created from the unexplained difference.
        self.assertEqual(profit.separated_scrap_loss, Decimal("0.00"))
        self.assertEqual(profit.unreconciled_scrap_cost, Decimal("0.00"))
        self.assertEqual(profit.absorbed_scrap_cost, Decimal("0.00"))
        self.assertEqual(profit.scrap_treatment, MonthlyProfitPeriod.ScrapTreatment.NONE)
        self.assertEqual(profit.monthly_result, Decimal("0.00"))
        # The persisted order entry remains the sole authority: still 0.00.
        # (Finalization recalculated, so re-fetch the authoritative row.)
        order_entry = ProductionOrderCostingEntry.objects.get(
            period=period, order=self.order
        )
        self.assertEqual(order_entry.waste_material_cost, Decimal("0.00"))
