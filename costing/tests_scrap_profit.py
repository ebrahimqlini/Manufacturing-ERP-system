"""Monthly profit integration of separated scrap cost (Prompt 11).

Proves the deduction discipline end to end:
- separated scrap is deducted exactly once, only after the costing period is
  finalized and the order is complete (the same authority gate COGS relies on);
- scrap under the absorbed policy stays inside unit cost → COGS and is never
  deducted again (no double count);
- everything else lands in the visible "unreconciled" bucket — never deducted,
  never fabricated;
- the report rows, owner API payload, and profit period page all expose the
  new figures without changing the finalized-period protections.
"""

import uuid
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from manufacturing.models import ProductionOrder
from reports.services import profit_report

from .models import (
    CostingPeriod,
    MonthlyProfitPeriod,
    ProductCostingEntry,
    ProductionOrderCostingEntry,
)
from .profit_services import calculate_monthly_profit, finalize_monthly_profit
from .tests_profit import ProfitFixtureMixin

WASTE = Decimal("5000.00")


class ScrapProfitFixtureMixin(ProfitFixtureMixin):
    """Profit fixture plus completed orders with separated-scrap entries."""

    def completed_order(self, *, status=ProductionOrder.Status.COMPLETED, quantity="1000"):
        self._scrap_order_seq = getattr(self, "_scrap_order_seq", 0) + 1
        return ProductionOrder.objects.create(
            order_number=f"SCRAP-MO-{self._scrap_order_seq}-{uuid.uuid4().hex[:6].upper()}",
            product=self.product,
            production_line=self.line,
            source_location=self.location,
            planned_quantity=Decimal(quantity),
            planned_quantity_base=Decimal(quantity),
            planned_unit=self.pcs,
            status=status,
            created_by=self.owner,
        )

    def add_order_entry(
        self,
        period,
        order,
        *,
        waste=WASTE,
        status="COMPLETE",
        good="1000",
        scrap="0",
    ):
        return ProductionOrderCostingEntry.objects.create(
            period=period,
            order=order,
            product=self.product,
            production_line=self.line,
            good_units_produced=Decimal(good),
            scrap_units_produced=Decimal(scrap),
            actual_material_cost=Decimal("70000.00"),
            material_cost_assigned_to_good=Decimal("70000.00"),
            waste_material_cost=Decimal(waste),
            calculation_status=status,
        )

    def separate_policy_period(self, year, month, *, finalize=True):
        """Cost period under SEPARATE_ABNORMAL with the standard 70 EGP unit cost.

        Expenses must exist before finalization: the engine rejects expense
        additions to a finalized costing month.
        """
        period = self.create_cost_period(year, month)
        period.waste_policy = CostingPeriod.WastePolicy.SEPARATE_ABNORMAL
        period.save()
        self.standard_month(year, month)
        if finalize:
            self.finalize_cost_period(period)
        return period

    def standard_month(self, year, month):
        """All operating expenses recorded: 20k labor + 10k elec + 5k maint + 10k other."""
        self.add_all_operating_expenses(
            year, month, Decimal("20000"), Decimal("10000"), Decimal("5000"), Decimal("10000")
        )


class SeparatedScrapDeductionTests(ScrapProfitFixtureMixin, TestCase):
    def setUp(self):
        self.setup_profit_fixture()

    def test_separated_scrap_deducted_exactly_once(self):
        period = self.separate_policy_period(2026, 6)
        order = self.completed_order()
        self.add_order_entry(period, order)
        self.ship(2026, 6, "1000", "150.00")
        profit = calculate_monthly_profit(year=2026, month=6, actor=self.owner)
        self.assertEqual(profit.revenue, Decimal("150000.00"))
        self.assertEqual(profit.cogs, Decimal("70000.00"))
        self.assertEqual(profit.gross_profit, Decimal("80000.00"))
        self.assertEqual(profit.operating_expenses_total, Decimal("45000.00"))
        # Formula: gross − operating − separated scrap, deducted once.
        self.assertEqual(profit.separated_scrap_loss, WASTE)
        self.assertEqual(profit.monthly_result, Decimal("30000.00"))
        self.assertEqual(profit.unreconciled_scrap_cost, Decimal("0.00"))
        self.assertEqual(profit.scrap_treatment, MonthlyProfitPeriod.ScrapTreatment.SEPARATED)
        self.assertTrue(any(str(WASTE) in note for note in profit.missing_data_notes.split("\n")))

    def test_draft_costing_period_scrap_is_unreconciled_not_deducted(self):
        # Deduction authority = finalized costing period (same gate as COGS).
        period = self.separate_policy_period(2026, 6, finalize=False)
        order = self.completed_order()
        self.add_order_entry(period, order)
        self.ship(2026, 6, "1000", "150.00")
        profit = calculate_monthly_profit(year=2026, month=6, actor=self.owner)
        self.assertEqual(profit.separated_scrap_loss, Decimal("0.00"))
        self.assertEqual(profit.unreconciled_scrap_cost, WASTE)
        self.assertEqual(profit.monthly_result, Decimal("35000.00"))

    def test_unfinished_order_scrap_is_unreconciled_not_deducted(self):
        period = self.separate_policy_period(2026, 6)
        order = self.completed_order(status=ProductionOrder.Status.RELEASED)
        self.add_order_entry(period, order)
        self.ship(2026, 6, "1000", "150.00")
        profit = calculate_monthly_profit(year=2026, month=6, actor=self.owner)
        self.assertEqual(profit.separated_scrap_loss, Decimal("0.00"))
        self.assertEqual(profit.unreconciled_scrap_cost, WASTE)
        self.assertEqual(profit.monthly_result, Decimal("35000.00"))
        self.assertEqual(profit.scrap_treatment, MonthlyProfitPeriod.ScrapTreatment.SEPARATED)

    def test_incomplete_entry_scrap_is_unreconciled_not_deducted(self):
        period = self.separate_policy_period(2026, 6)
        order = self.completed_order()
        self.add_order_entry(period, order, status="INCOMPLETE")
        self.ship(2026, 6, "1000", "150.00")
        profit = calculate_monthly_profit(year=2026, month=6, actor=self.owner)
        self.assertEqual(profit.separated_scrap_loss, Decimal("0.00"))
        self.assertEqual(profit.unreconciled_scrap_cost, WASTE)
        self.assertEqual(profit.monthly_result, Decimal("35000.00"))

    def test_recalculation_resets_stale_scrap_deduction(self):
        period = self.separate_policy_period(2026, 6)
        order = self.completed_order()
        entry = self.add_order_entry(period, order)
        self.ship(2026, 6, "1000", "150.00")
        calculate_monthly_profit(year=2026, month=6, actor=self.owner)
        # The separated waste turns out to have been corrected away upstream.
        ProductionOrderCostingEntry.objects.filter(pk=entry.pk).update(
            waste_material_cost=Decimal("0.00")
        )
        recalculated = calculate_monthly_profit(year=2026, month=6, actor=self.owner)
        self.assertEqual(recalculated.separated_scrap_loss, Decimal("0.00"))
        self.assertEqual(recalculated.monthly_result, Decimal("35000.00"))

    def test_no_costing_period_never_invents_scrap(self):
        self.standard_month(2026, 9)
        self.ship(2026, 9, "100", "150.00")
        profit = calculate_monthly_profit(year=2026, month=9, actor=self.owner)
        self.assertEqual(profit.scrap_treatment, MonthlyProfitPeriod.ScrapTreatment.NO_COSTING)
        self.assertEqual(profit.separated_scrap_loss, Decimal("0.00"))
        self.assertEqual(profit.absorbed_scrap_cost, Decimal("0.00"))
        self.assertEqual(profit.unreconciled_scrap_cost, Decimal("0.00"))
        self.assertEqual(profit.cogs, Decimal("0.00"))

    def test_unreconciled_scrap_blocks_finalization(self):
        period = self.separate_policy_period(2026, 6)
        order = self.completed_order(status=ProductionOrder.Status.IN_PROGRESS)
        self.add_order_entry(period, order)
        self.ship(2026, 6, "1000", "150.00")
        profit = calculate_monthly_profit(year=2026, month=6, actor=self.owner)
        self.assertFalse(profit.is_complete)
        with self.assertRaises(ValidationError):
            finalize_monthly_profit(period=profit, actor=self.owner)
        profit.refresh_from_db()
        self.assertEqual(profit.status, MonthlyProfitPeriod.Status.DRAFT)
        self.assertEqual(profit.separated_scrap_loss, Decimal("0.00"))
        self.assertEqual(profit.unreconciled_scrap_cost, WASTE)


class AbsorbedScrapNoDoubleDeductionTests(ScrapProfitFixtureMixin, TestCase):
    def setUp(self):
        self.setup_profit_fixture()

    def _absorbed_month_with_scrap(self):
        """Absorbed policy: 77 EGP unit cost carries the scrap share of material."""
        period = self.create_cost_period(2026, 7)
        # Under ABSORBED the engine keeps the scrap material inside the good
        # unit cost: 77000 material over 1000 good units (100 recorded scrap).
        ProductCostingEntry.objects.filter(period=period, product=self.product).update(
            good_units_produced=Decimal("1000.0000"),
            scrap_units_produced=Decimal("100.0000"),
            actual_material_cost=Decimal("77000.00"),
            material_cost_assigned_to_good=Decimal("77000.00"),
            total_production_cost=Decimal("77000.00"),
            cost_per_good_unit=Decimal("77.0000"),
            material_cost_per_good_unit=Decimal("77.0000"),
        )
        order = self.completed_order()
        # Under the absorbed policy the engine persists zero separated waste.
        self.add_order_entry(period, order, waste="0.00", good="1000", scrap="100")
        # Expenses must exist before the costing period is finalized.
        self.standard_month(2026, 7)
        self.finalize_cost_period(period)
        self.ship(2026, 7, "1000", "150.00")
        return calculate_monthly_profit(year=2026, month=7, actor=self.owner)

    def test_absorbed_policy_deducts_nothing_and_shows_display_estimate(self):
        profit = self._absorbed_month_with_scrap()
        # COGS carries the scrap share through the 77 EGP unit cost (once).
        self.assertEqual(profit.cogs, Decimal("77000.00"))
        self.assertEqual(profit.gross_profit, Decimal("73000.00"))
        # No scrap line is deducted again: result = gross − operating exactly.
        self.assertEqual(profit.separated_scrap_loss, Decimal("0.00"))
        self.assertEqual(profit.monthly_result, Decimal("28000.00"))
        # Finished-output scrap alone is not measured raw-material waste.
        self.assertEqual(profit.scrap_treatment, MonthlyProfitPeriod.ScrapTreatment.NONE)
        self.assertEqual(profit.absorbed_scrap_cost, Decimal("0.00"))
        self.assertEqual(profit.unreconciled_scrap_cost, Decimal("0.00"))

    def test_absorbed_policy_never_deducts_even_persisted_waste(self):
        """Defense in depth: waste recorded under ABSORBED is still not deducted."""
        period = self.create_cost_period(2026, 7)
        order = self.completed_order()
        self.add_order_entry(period, order, waste=WASTE)
        self.standard_month(2026, 7)
        self.finalize_cost_period(period)
        self.ship(2026, 7, "1000", "150.00")
        profit = calculate_monthly_profit(year=2026, month=7, actor=self.owner)
        self.assertEqual(profit.separated_scrap_loss, Decimal("0.00"))
        self.assertEqual(profit.unreconciled_scrap_cost, Decimal("0.00"))
        self.assertEqual(profit.monthly_result, Decimal("35000.00"))


class ScrapProfitReportAndApiTests(ScrapProfitFixtureMixin, TestCase):
    def setUp(self):
        self.setup_profit_fixture()
        self.period = self.separate_policy_period(2026, 6)
        self.order = self.completed_order()
        self.add_order_entry(self.period, self.order)
        self.ship(2026, 6, "1000", "150.00")
        self.profit = calculate_monthly_profit(year=2026, month=6, actor=self.owner)

    def test_profit_report_rows_expose_scrap_figures_and_formula(self):
        start, end = date(2026, 6, 1), date(2026, 6, 30)
        report = profit_report(start, end)
        row = report["periods"][0]
        self.assertEqual(row["separated_scrap_loss"], "5000.00")
        self.assertEqual(row["absorbed_scrap_cost"], "0.00")
        self.assertEqual(row["unreconciled_scrap_cost"], "0.00")
        # Stored choice value (attribute SEPARATED maps to SEPARATED_DEDUCTED).
        self.assertEqual(row["scrap_treatment"], "SEPARATED_DEDUCTED")
        self.assertTrue(row["has_scrap_deduction"])
        self.assertEqual(report["range_totals"]["separated_scrap_loss"], "5000.00")
        # Row-level machine formula names the deduction field explicitly.
        self.assertIn("separated_scrap_loss", row["formula"])
        # Report-level Arabic formula names the deduction line in Arabic.
        self.assertIn("خسارة الهالك المفصلة", report["formula"]["monthly_result"])
        self.assertTrue(
            any("Scrap treatment" in note for note in report["basis_notes"])
        )

    def test_owner_api_profit_payload_includes_scrap(self):
        token, _ = Token.objects.get_or_create(user=self.owner)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = client.get(
            reverse("api:owner-profit"),
            {"start_date": "2026-06-01", "end_date": "2026-06-30"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()["profit_loss"]
        row = payload["periods"][0]
        self.assertEqual(row["separated_scrap_loss"], "5000.00")
        self.assertEqual(row["scrap_treatment"], "SEPARATED_DEDUCTED")
        self.assertTrue(row["has_scrap_deduction"])
        self.assertIn("separated_scrap_loss", row["formula"])
        self.assertIn("خسارة الهالك المفصلة", payload["formula"]["monthly_result"])

    def test_profit_period_page_shows_scrap_section(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("costing:profit_period_detail", args=[self.profit.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "معالجة الهالك في النتيجة")
        self.assertContains(response, self.profit.get_scrap_treatment_display())

    def test_reports_profit_page_shows_scrap_column(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("reports:profit"),
            {"start_date": "2026-06-01", "end_date": "2026-06-30"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "هالك مخصوم")
        self.assertContains(response, "خسارة هالك مفصلة مخصومة")
