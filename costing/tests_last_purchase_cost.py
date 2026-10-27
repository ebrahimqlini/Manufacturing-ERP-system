"""Prompt 12 — Last Purchase Cost valuation and mandatory receipt pricing.

Covers the owner-approved valuation basis (latest posted purchase cost) and
the mandatory receipt pricing rules:

- a confirmed receipt posts an immutable base-unit cost and moves the
  product's last purchase cost (with an append-only history entry);
- the latest receipt price becomes the active last purchase cost;
- a receipt without a positive purchase price is rejected before any stock
  movement or cost state is written;
- reversing a receipt restores the previous last purchase cost (or clears it
  when no earlier valid receipt exists);
- material costing and waste valuation use the last purchase cost captured at
  issue time, never an owner-entered price that was superseded by a purchase;
- waste is valued at the applicable last purchase cost as of the production
  event, and a later purchase never rewrites an earlier waste valuation;
- the as-of lookup resolves the applicable purchase cost from history.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from unittest import mock

from costing.models import CostingPeriod, MonthlyExpense, RawMaterialPrice
from costing.services import (
    MATERIAL_VALUATION_METHOD,
    calculate_monthly_costing,
    finalize_costing_period,
    get_last_purchase_cost_as_of,
)
from inventory.models import (
    LastPurchaseCost,
    LastPurchaseCostHistory,
    StockMovement,
    StorageLocation,
)
from inventory.services import post_movement, reverse_movement
from manufacturing.models import MaterialIssue
from manufacturing.services import (
    issue_material,
    record_production_result,
    release_order,
    save_draft_order as save_production_order,
)
from purchasing.models import PurchaseReceipt, Supplier
from purchasing.services import confirm_order, create_receipt, save_draft_order
from purchasing.tests import PurchasingFixtureMixin

from .tests import CostingProductionMixin


class LastPurchaseCostReceiptTests(PurchasingFixtureMixin, TestCase):
    """Mandatory receipt pricing and last purchase cost bookkeeping."""

    def setUp(self):
        self.create_fixture()

    def test_confirmed_receipt_updates_last_purchase_cost_and_appends_history(self):
        order = self.make_order(confirm=True)
        line = order.lines.get()
        receipt = self.receive(order, [{"order_line": line, "quantity": Decimal("500"), "unit": self.kg}])
        receipt_line = receipt.lines.get()

        lpc = LastPurchaseCost.objects.get(product=self.material)
        self.assertEqual(lpc.price_per_base_unit, Decimal("5.000000"))
        self.assertEqual(lpc.source_movement, receipt_line.movement)

        history = LastPurchaseCostHistory.objects.get(product=self.material)
        self.assertEqual(history.source_movement, receipt_line.movement)
        self.assertEqual(history.price_per_base_unit, Decimal("5.000000"))
        self.assertEqual(history.effective_at, receipt_line.movement.posted_at)

    def test_latest_receipt_price_becomes_the_last_purchase_cost(self):
        first = self.make_order(confirm=True, lines=[self.line(quantity="1", unit=self.ton, unit_price="4000")])
        second = self.make_order(confirm=True, lines=[self.line(quantity="1", unit=self.ton, unit_price="6000")])
        self.receive(
            first,
            [{"order_line": first.lines.get(), "quantity": Decimal("500"), "unit": self.kg}],
            reference=f"GRN-LPC-{uuid.uuid4().hex[:8]}",
        )
        self.receive(
            second,
            [{"order_line": second.lines.get(), "quantity": Decimal("500"), "unit": self.kg}],
            reference=f"GRN-LPC-{uuid.uuid4().hex[:8]}",
        )

        self.assertEqual(
            LastPurchaseCost.objects.get(product=self.material).price_per_base_unit,
            Decimal("6.000000"),
        )
        self.assertEqual(LastPurchaseCostHistory.objects.filter(product=self.material).count(), 2)

    def test_receipt_without_price_is_rejected_without_side_effects(self):
        order = self.make_order(confirm=True, lines=[self.line(unit_price="0")])
        line = order.lines.get()
        with self.assertRaises(ValidationError):
            self.receive(order, [{"order_line": line, "quantity": Decimal("100"), "unit": self.kg}])

        self.assertEqual(PurchaseReceipt.objects.count(), 0)
        self.assertFalse(StockMovement.objects.filter(source_reference_type="purchasing.purchase_receipt").exists())
        self.assertFalse(LastPurchaseCost.objects.filter(product=self.material).exists())

    def test_reversal_of_receipt_restores_previous_last_purchase_cost(self):
        first = self.make_order(confirm=True, lines=[self.line(quantity="1", unit=self.ton, unit_price="4000")])
        second = self.make_order(confirm=True, lines=[self.line(quantity="1", unit=self.ton, unit_price="6000")])
        self.receive(
            first,
            [{"order_line": first.lines.get(), "quantity": Decimal("500"), "unit": self.kg}],
            reference=f"GRN-LPC-{uuid.uuid4().hex[:8]}",
        )
        receipt_two = self.receive(
            second,
            [{"order_line": second.lines.get(), "quantity": Decimal("500"), "unit": self.kg}],
            reference=f"GRN-LPC-{uuid.uuid4().hex[:8]}",
        )
        self.assertEqual(LastPurchaseCost.objects.get(product=self.material).price_per_base_unit, Decimal("6.000000"))

        reverse_movement(
            movement=receipt_two.lines.get().movement,
            reference="REV-P12-LAST-1",
            actor=self.staff,
            reason="استرجاع سعر استلام سابق",
        )
        self.assertEqual(LastPurchaseCost.objects.get(product=self.material).price_per_base_unit, Decimal("4.000000"))

    def test_reversal_of_receipt_without_previous_receipt_clears_last_purchase_cost(self):
        order = self.make_order(confirm=True)
        receipt = self.receive(order, [{"order_line": order.lines.get(), "quantity": Decimal("500"), "unit": self.kg}])
        self.assertTrue(LastPurchaseCost.objects.filter(product=self.material).exists())

        reverse_movement(
            movement=receipt.lines.get().movement,
            reference="REV-P12-LAST-2",
            actor=self.staff,
            reason="استرجاع سعر سابق",
        )
        self.assertFalse(LastPurchaseCost.objects.filter(product=self.material).exists())

    def _post_purchase_receipt(
        self, *, price, occurred_at=None, quantity=Decimal("100"), material=None, unit=None
    ):
        material = material or self.material
        unit = unit or material.base_uom
        order = self.make_order(
            confirm=True,
            lines=[self.line(material=material, quantity=str(quantity), unit=unit, unit_price=str(Decimal(str(price)).quantize(Decimal("0.0001"))) )],
        )
        posted_at = occurred_at or timezone.now()
        with mock.patch("django.utils.timezone.now", return_value=posted_at):
            receipt = self.receive(
                order,
                [{"order_line": order.lines.get(), "quantity": quantity, "unit": unit}],
                reference=f"GRN-LPC-{uuid.uuid4().hex[:8]}",
            )
        return receipt.lines.get().movement

    def test_base_unit_price_uses_actual_received_base_quantity(self):
        order = self.make_order(
            confirm=True,
            lines=[self.line(quantity="1", unit=self.ton, unit_price="4000")],
        )
        receipt = self.receive(
            order,
            [{"order_line": order.lines.get(), "quantity": Decimal("500"), "unit": self.kg}],
            reference=f"GRN-LPC-{uuid.uuid4().hex[:8]}",
        )
        receipt_line = receipt.lines.get()
        # 4,000 per ton = 4.000000 per kg, regardless of receiving 500 of the
        # ordered 1,000 kg; the line total is actual base quantity × unit cost.
        self.assertEqual(receipt_line.price_per_base_unit, Decimal("4.000000"))
        self.assertEqual(receipt_line.quantity_base, Decimal("500.000000000000"))
        self.assertEqual(receipt_line.line_total, Decimal("2000.00"))
        self.assertEqual(
            LastPurchaseCost.objects.get(product=self.material).price_per_base_unit,
            Decimal("4.000000"),
        )

    def test_mandatory_positive_receipt_quantity(self):
        order = self.make_order(confirm=True)
        with self.assertRaises(ValidationError):
            self.receive(
                order,
                [{"order_line": order.lines.get(), "quantity": Decimal("0"), "unit": self.kg}],
                reference=f"GRN-LPC-{uuid.uuid4().hex[:8]}",
            )
        self.assertFalse(PurchaseReceipt.objects.exists())
        self.assertFalse(LastPurchaseCost.objects.filter(product=self.material).exists())

    def test_draft_purchase_order_cannot_receive_or_set_lpc(self):
        order = self.make_order(confirm=False)
        with self.assertRaises(ValidationError):
            self.receive(
                order,
                [{"order_line": order.lines.get(), "quantity": Decimal("100"), "unit": self.kg}],
                reference=f"GRN-LPC-{uuid.uuid4().hex[:8]}",
            )
        self.assertFalse(LastPurchaseCost.objects.filter(product=self.material).exists())
        self.assertFalse(LastPurchaseCostHistory.objects.filter(product=self.material).exists())

    def test_non_purchasing_receipt_ignores_supplied_cost(self):
        post_movement(
            reference=f"LPC-NONPUR-{uuid.uuid4().hex[:8]}",
            movement_type=StockMovement.Type.RECEIPT,
            product=self.material,
            quantity=Decimal("10"),
            unit=self.kg,
            actor=self.staff,
            destination_location=self.location,
            source_reference_type="manufacturing.initial_stock",
            source_reference_id=f"NONPUR:{uuid.uuid4().hex}",
            reason="افتتاحي غير شرائي",
            unit_cost_per_base_unit=Decimal("999.000000"),
        )
        self.assertFalse(LastPurchaseCost.objects.filter(product=self.material).exists())
        self.assertFalse(LastPurchaseCostHistory.objects.filter(product=self.material).exists())

    def test_forged_purchasing_source_label_does_not_update_lpc(self):
        forged = post_movement(
            reference=f"LPC-FORGED-{uuid.uuid4().hex[:8]}",
            movement_type=StockMovement.Type.RECEIPT,
            product=self.material,
            quantity=Decimal("10"),
            unit=self.kg,
            actor=self.staff,
            destination_location=self.location,
            source_reference_type="purchasing.purchase_receipt",
            source_reference_id=f"{uuid.uuid4().hex}:999999",
            external_reference="FORGED-GRN",
            reason="وسم مصدر مزور للاختبار",
            unit_cost_per_base_unit=Decimal("999.000000"),
        )
        self.assertEqual(forged.movement_type, StockMovement.Type.RECEIPT)
        self.assertFalse(LastPurchaseCost.objects.filter(product=self.material).exists())
        self.assertFalse(LastPurchaseCostHistory.objects.filter(product=self.material).exists())
        reverse_movement(
            movement=forged,
            reference=f"REV-FORGED-{uuid.uuid4().hex[:8]}",
            actor=self.staff,
            reason="عكس استلام غير مرتبط بمستند مشتريات",
        )
        self.assertFalse(LastPurchaseCost.objects.filter(product=self.material).exists())
        self.assertFalse(LastPurchaseCostHistory.objects.filter(product=self.material).exists())

    def test_backdated_receipt_date_does_not_make_lpc_available_early(self):
        order = self.make_order(confirm=True)
        receipt = create_receipt(
            order=order,
            actor=self.staff,
            reference=f"GRN-BACKDATED-{uuid.uuid4().hex[:8]}",
            received_date=timezone.localdate() - timedelta(days=30),
            destination_location=self.location,
            lines=[{"order_line": order.lines.get(), "quantity": Decimal("500"), "unit": self.kg}],
        )
        movement = receipt.lines.get().movement
        self.assertLess(receipt.received_date, timezone.localdate(movement.posted_at))
        before_posting = movement.posted_at - timedelta(seconds=1)
        self.assertIsNone(get_last_purchase_cost_as_of(self.material, before_posting))
        self.assertEqual(
            get_last_purchase_cost_as_of(self.material, movement.posted_at),
            Decimal("5.000000"),
        )

    def test_transfer_ignores_supplied_cost(self):
        post_movement(
            reference=f"LPC-STOCK-{uuid.uuid4().hex[:8]}",
            movement_type=StockMovement.Type.RECEIPT,
            product=self.material,
            quantity=Decimal("10"),
            unit=self.kg,
            actor=self.staff,
            destination_location=self.location,
            source_reference_type="manufacturing.initial_stock",
            source_reference_id=f"STOCK:{uuid.uuid4().hex}",
            reason="رصيد اختبار التحويل",
        )
        other_location = StorageLocation.objects.create(
            warehouse=self.location.warehouse,
            code=f"TRN-{uuid.uuid4().hex[:6]}",
            name_ar="موقع تحويل",
        )
        post_movement(
            reference=f"LPC-TRN-{uuid.uuid4().hex[:8]}",
            movement_type=StockMovement.Type.TRANSFER,
            product=self.material,
            quantity=Decimal("5"),
            unit=self.kg,
            actor=self.staff,
            source_location=self.location,
            destination_location=other_location,
            reason="تحويل مخزني",
            unit_cost_per_base_unit=Decimal("999.000000"),
        )
        self.assertFalse(LastPurchaseCost.objects.filter(product=self.material).exists())
        self.assertFalse(LastPurchaseCostHistory.objects.filter(product=self.material).exists())

    def test_rolled_back_purchase_receipt_leaves_no_lpc_or_history(self):
        order = self.make_order(confirm=True)
        reference = f"GRN-LPC-{uuid.uuid4().hex[:8]}"
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.receive(
                    order,
                    [{"order_line": order.lines.get(), "quantity": Decimal("100"), "unit": self.kg}],
                    reference=reference,
                )
                raise RuntimeError("forced rollback")
        self.assertFalse(PurchaseReceipt.objects.filter(reference=reference).exists())
        self.assertFalse(LastPurchaseCost.objects.filter(product=self.material).exists())
        self.assertFalse(LastPurchaseCostHistory.objects.filter(product=self.material).exists())

    def test_reversal_restores_latest_still_valid_receipt(self):
        now = timezone.now()
        first = self._post_purchase_receipt(
            price=Decimal("40.000000"), occurred_at=now - timedelta(days=3)
        )
        second = self._post_purchase_receipt(
            price=Decimal("50.000000"), occurred_at=now - timedelta(days=2)
        )
        third = self._post_purchase_receipt(
            price=Decimal("60.000000"), occurred_at=now - timedelta(days=1)
        )
        self.assertEqual(
            LastPurchaseCost.objects.get(product=self.material).price_per_base_unit,
            Decimal("60.000000"),
        )
        reverse_movement(
            movement=third,
            reference=f"REV-LPC-{uuid.uuid4().hex[:8]}",
            actor=self.staff,
            reason="عكس الأحدث",
        )
        self.assertEqual(
            LastPurchaseCost.objects.get(product=self.material).price_per_base_unit,
            Decimal("50.000000"),
        )
        reverse_movement(
            movement=second,
            reference=f"REV-LPC-{uuid.uuid4().hex[:8]}",
            actor=self.staff,
            reason="عكس الثاني",
        )
        self.assertEqual(
            LastPurchaseCost.objects.get(product=self.material).price_per_base_unit,
            Decimal("40.000000"),
        )
        self.assertNotEqual(first.pk, second.pk)

    def test_reversing_older_receipt_keeps_later_valid_price_active(self):
        now = timezone.now()
        self._post_purchase_receipt(
            price=Decimal("40.000000"), occurred_at=now - timedelta(days=3)
        )
        middle = self._post_purchase_receipt(
            price=Decimal("50.000000"), occurred_at=now - timedelta(days=2)
        )
        self._post_purchase_receipt(
            price=Decimal("60.000000"), occurred_at=now - timedelta(days=1)
        )
        reverse_movement(
            movement=middle,
            reference=f"REV-LPC-{uuid.uuid4().hex[:8]}",
            actor=self.staff,
            reason="عكس استلام أقدم مع وجود أحدث",
        )
        self.assertEqual(
            LastPurchaseCost.objects.get(product=self.material).price_per_base_unit,
            Decimal("60.000000"),
        )
        self.assertEqual(
            get_last_purchase_cost_as_of(self.material, timezone.now() + timedelta(seconds=1)),
            Decimal("60.000000"),
        )

    def test_reversal_without_previous_records_null_history_not_zero(self):
        now = timezone.now()
        receipt = self._post_purchase_receipt(
            price=Decimal("60.000000"), occurred_at=now - timedelta(hours=1)
        )
        reverse_movement(
            movement=receipt,
            reference=f"REV-LPC-{uuid.uuid4().hex[:8]}",
            actor=self.staff,
            reason="عكس بلا سابق",
        )
        self.assertFalse(LastPurchaseCost.objects.filter(product=self.material).exists())
        latest = LastPurchaseCostHistory.objects.filter(product=self.material).order_by(
            "-effective_at", "-recorded_at"
        ).first()
        self.assertIsNotNone(latest)
        self.assertIsNone(latest.price_per_base_unit)
        self.assertIsNone(
            get_last_purchase_cost_as_of(self.material, timezone.now() + timedelta(seconds=1))
        )

    def test_reversal_effective_at_is_reversal_time_not_original_receipt_time(self):
        now = timezone.now()
        receipt = self._post_purchase_receipt(
            price=Decimal("60.000000"), occurred_at=now - timedelta(hours=1)
        )
        reverse_movement(
            movement=receipt,
            reference=f"REV-LPC-{uuid.uuid4().hex[:8]}",
            actor=self.staff,
            reason="عكس تاريخي",
        )
        # Before the reversal, the receipt was a valid historical price.
        self.assertEqual(
            get_last_purchase_cost_as_of(self.material, now - timedelta(minutes=30)),
            Decimal("60.000000"),
        )
        # After the reversal, the applicable price is unavailable (not zero).
        self.assertIsNone(
            get_last_purchase_cost_as_of(self.material, timezone.now() + timedelta(seconds=1))
        )

    def test_reversal_does_not_consider_non_purchasing_receipts(self):
        now = timezone.now()
        post_movement(
            reference=f"LPC-NONPUR-{uuid.uuid4().hex[:8]}",
            movement_type=StockMovement.Type.RECEIPT,
            product=self.material,
            quantity=Decimal("10"),
            unit=self.kg,
            actor=self.staff,
            destination_location=self.location,
            source_reference_type="manufacturing.initial_stock",
            source_reference_id=f"NONPUR:{uuid.uuid4().hex}",
            reason="افتتاحي غير شرائي",
            unit_cost_per_base_unit=Decimal("999.000000"),
            occurred_at=now - timedelta(hours=2),
        )
        purchase = self._post_purchase_receipt(
            price=Decimal("60.000000"), occurred_at=now - timedelta(hours=1)
        )
        reverse_movement(
            movement=purchase,
            reference=f"REV-LPC-{uuid.uuid4().hex[:8]}",
            actor=self.staff,
            reason="عكس الشراء فقط",
        )
        self.assertFalse(LastPurchaseCost.objects.filter(product=self.material).exists())
        self.assertIsNone(
            get_last_purchase_cost_as_of(self.material, timezone.now() + timedelta(seconds=1))
        )

    def test_each_receipt_and_reversal_appends_exactly_one_history_row(self):
        first = self._post_purchase_receipt(price=Decimal("40.000000"))
        second = self._post_purchase_receipt(price=Decimal("50.000000"))
        self.assertEqual(LastPurchaseCostHistory.objects.filter(product=self.material).count(), 2)
        reverse_movement(
            movement=second,
            reference=f"REV-LPC-{uuid.uuid4().hex[:8]}",
            actor=self.staff,
            reason="عكس الثاني",
        )
        self.assertEqual(LastPurchaseCostHistory.objects.filter(product=self.material).count(), 3)
        reverse_movement(
            movement=first,
            reference=f"REV-LPC-{uuid.uuid4().hex[:8]}",
            actor=self.staff,
            reason="عكس الأول",
        )
        self.assertEqual(LastPurchaseCostHistory.objects.filter(product=self.material).count(), 4)


class LastPurchaseCostValuationTests(CostingProductionMixin, TestCase):
    """Last purchase cost drives costing and waste valuation; no owner override."""

    def _post_priced_receipt(
        self, material, quantity_base, price_per_base_unit, *, occurred_at=None, unit=None
    ):
        unit = unit or material.base_uom
        suffix = uuid.uuid4().hex[:8].upper()
        supplier = Supplier.objects.create(
            code=f"LPC-{suffix}", name_ar="مورد اختبار", created_by=self.staff_user
        )
        order = save_draft_order(
            actor=self.staff_user,
            supplier=supplier,
            order_date=date.today(),
            lines=[{
                "material": material,
                "quantity": quantity_base,
                "unit": unit,
                "unit_price": Decimal(str(price_per_base_unit)).quantize(Decimal("0.0001")),
            }],
        )
        confirm_order(order=order, actor=self.staff_user)
        posted_at = occurred_at or timezone.now()
        with mock.patch("django.utils.timezone.now", return_value=posted_at):
            receipt = create_receipt(
                order=order,
                actor=self.staff_user,
                reference=f"GRN-P12-{suffix}",
                received_date=date.today(),
                destination_location=self.raw_location,
                lines=[{
                    "order_line": order.lines.get(),
                    "quantity": quantity_base,
                    "unit": unit,
                }],
            )
        return receipt.lines.get().movement

    def test_costing_material_cost_uses_last_purchase_cost_not_owner_price(self):
        self._post_priced_receipt(self.raw_paper, Decimal("2000"), Decimal("60.000000"))
        self._create_production_run(
            product=self.product_a,
            line=self.line_1,
            planned_qty=Decimal("100"),
            actual_paper_kg=Decimal("200"),
            actual_foil_rolls=Decimal("1"),
            good_qty=Decimal("100"),
            scrap_qty=Decimal("0"),
        )
        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        entry = period.product_entries.get(product=self.product_a)
        # Paper is now valued at the posted purchase cost (60) instead of the
        # superseded owner-entered price (50); the foil fallback owner price (200)
        # still applies because no purchase receipt posted for it.
        self.assertEqual(entry.actual_material_cost, Decimal("12200.00"))

    def test_waste_is_valued_at_last_purchase_cost(self):
        self._post_priced_receipt(self.raw_paper, Decimal("2000"), Decimal("60.000000"))
        self._create_production_run(
            product=self.product_b,
            line=self.line_2,
            planned_qty=Decimal("150"),
            actual_paper_kg=Decimal("500"),
            actual_foil_rolls=Decimal("0"),
            good_qty=Decimal("100"),
            scrap_qty=Decimal("50"),
            measured_waste_base=Decimal("166.666666666667"),
        )
        period = calculate_monthly_costing(
            year=self.year,
            month=self.month,
            waste_policy=CostingPeriod.WastePolicy.SEPARATE_ABNORMAL,
            actor=self.staff_user,
        )
        entry = period.product_entries.get(product=self.product_b)
        self.assertEqual(entry.actual_material_cost, Decimal("30000.00"))
        self.assertEqual(entry.waste_material_cost, Decimal("10000.00"))
        self.assertEqual(entry.material_cost_assigned_to_good, Decimal("20000.00"))

    def test_absorbed_policy_loads_full_material_cost_into_good_units(self):
        self._post_priced_receipt(self.raw_paper, Decimal("2000"), Decimal("60.000000"))
        self._create_production_run(
            product=self.product_b,
            line=self.line_2,
            planned_qty=Decimal("150"),
            actual_paper_kg=Decimal("500"),
            actual_foil_rolls=Decimal("0"),
            good_qty=Decimal("100"),
            scrap_qty=Decimal("50"),
            measured_waste_base=Decimal("166.666666666667"),
        )
        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        entry = period.product_entries.get(product=self.product_b)
        self.assertEqual(entry.actual_material_cost, Decimal("30000.00"))
        # Measured event cost remains traceable while it stays absorbed in
        # good-unit cost under the selected policy.
        self.assertEqual(entry.waste_material_cost, Decimal("10000.00"))
        self.assertEqual(entry.material_cost_assigned_to_good, Decimal("30000.00"))

    def test_later_purchase_does_not_rewrite_earlier_waste_valuation(self):
        self._post_priced_receipt(self.raw_paper, Decimal("2000"), Decimal("60.000000"))
        self._create_production_run(
            product=self.product_b,
            line=self.line_2,
            planned_qty=Decimal("150"),
            actual_paper_kg=Decimal("500"),
            actual_foil_rolls=Decimal("0"),
            good_qty=Decimal("100"),
            scrap_qty=Decimal("50"),
            measured_waste_base=Decimal("166.666666666667"),
        )
        period = calculate_monthly_costing(
            year=self.year,
            month=self.month,
            waste_policy=CostingPeriod.WastePolicy.SEPARATE_ABNORMAL,
            actor=self.staff_user,
        )
        first_waste = period.product_entries.get(product=self.product_b).waste_material_cost
        self.assertEqual(first_waste, Decimal("10000.00"))

        # A later purchase moves the last purchase cost forward; the waste of
        # the earlier production event must keep the cost captured at issue time.
        self._post_priced_receipt(self.raw_paper, Decimal("1000"), Decimal("70.000000"))
        period = calculate_monthly_costing(
            year=self.year,
            month=self.month,
            waste_policy=CostingPeriod.WastePolicy.SEPARATE_ABNORMAL,
            actor=self.staff_user,
        )
        entry = period.product_entries.get(product=self.product_b)
        self.assertEqual(entry.waste_material_cost, first_waste)
        self.assertEqual(entry.material_cost_assigned_to_good + entry.waste_material_cost, entry.actual_material_cost)

    def test_cross_month_issue_and_scrap_event_stay_unvalued_without_material_link(self):
        now = timezone.now()
        previous_month_day = date(now.year, now.month, 1) - timedelta(days=1)
        issue_at = timezone.make_aware(
            datetime.combine(previous_month_day, datetime.min.time())
        )
        self._post_priced_receipt(
            self.raw_paper,
            Decimal("1000"),
            Decimal("60.000000"),
            occurred_at=issue_at - timedelta(hours=1),
        )
        order = save_production_order(
            actor=self.staff_user,
            product=self.product_a,
            planned_quantity=Decimal("100"),
            planned_unit=self.pcs,
            production_line=self.line_1,
            routing=self.routing_a,
            bom=self.bom_a,
            source_location=self.raw_location,
        )
        release_order(order=order, actor=self.staff_user)
        requirement = order.requirements.get(material=self.raw_paper)
        with mock.patch("django.utils.timezone.now", return_value=issue_at):
            issue_material(
                order=order,
                requirement=requirement,
                quantity=Decimal("10"),
                unit=self.kg,
                reference=f"CROSS-MONTH-ISSUE-{uuid.uuid4().hex[:8]}",
                actor=self.staff_user,
            )
        issue = MaterialIssue.objects.get(order=order, requirement=requirement)
        self.assertEqual(issue.unit_cost_snapshot, Decimal("60.000000"))

        result, _ = record_production_result(
            order=order,
            good_quantity=Decimal("80"),
            scrap_quantity=Decimal("20"),
            unit=self.pcs,
            notes="",
            actor=self.staff_user,
            occurred_at=now,
        )
        self.assertGreater(result.posted_at, issue.movement.posted_at)

        prior_period = calculate_monthly_costing(
            year=previous_month_day.year,
            month=previous_month_day.month,
            actor=self.staff_user,
        )
        result_period = calculate_monthly_costing(
            year=now.year, month=now.month, actor=self.staff_user,
        )
        self.assertFalse(prior_period.is_complete)
        self.assertFalse(result_period.is_complete)
        self.assertIn("بدون صرف مواد", result_period.missing_data_notes)
        result_entry = result_period.order_entries.get(order=order)
        self.assertEqual(result_entry.waste_material_cost, Decimal("0.00"))
        self.assertEqual(result_entry.calculation_status, "INCOMPLETE")

    def test_as_of_last_purchase_cost_resolves_from_history(self):
        now = timezone.now()
        first_at = now - timedelta(days=10)
        second_at = now - timedelta(days=2)
        self._post_priced_receipt(
            self.raw_paper, Decimal("1000"), Decimal("60.000000"), occurred_at=first_at
        )
        self._post_priced_receipt(
            self.raw_paper, Decimal("1000"), Decimal("70.000000"), occurred_at=second_at
        )

        self.assertEqual(get_last_purchase_cost_as_of(self.raw_paper, now), Decimal("70.000000"))
        self.assertEqual(
            get_last_purchase_cost_as_of(self.raw_paper, now - timedelta(days=5)),
            Decimal("60.000000"),
        )
        self.assertIsNone(
            get_last_purchase_cost_as_of(self.raw_paper, now - timedelta(days=20))
        )

    def test_missing_price_at_issue_time_blocks_finalization(self):
        RawMaterialPrice.objects.filter(material=self.raw_foil).delete()
        order = self._create_production_run(
            product=self.product_a,
            line=self.line_1,
            planned_qty=Decimal("50"),
            actual_paper_kg=Decimal("100"),
            actual_foil_rolls=Decimal("1"),
            good_qty=Decimal("50"),
            scrap_qty=Decimal("0"),
        )
        foil_issue = MaterialIssue.objects.get(
            order=order, requirement__material=self.raw_foil
        )
        self.assertIsNone(foil_issue.unit_cost_snapshot)
        self.assertEqual(foil_issue.cost_source, MaterialIssue.CostSource.UNAVAILABLE)
        period = calculate_monthly_costing(year=self.year, month=self.month, actor=self.staff_user)
        self.assertFalse(period.is_complete)
        self.assertIn(self.raw_foil.sku, period.missing_data_notes)
        entry = period.product_entries.get(product=self.product_a)
        self.assertEqual(entry.calculation_status, "INCOMPLETE")
        with self.assertRaises(ValidationError):
            finalize_costing_period(period=period, actor=self.staff_user)

    def test_independent_prices_for_different_materials(self):
        self._post_priced_receipt(self.raw_paper, Decimal("2000"), Decimal("60.000000"))
        self._post_priced_receipt(
            self.raw_foil, Decimal("10"), Decimal("300.000000"), unit=self.roll
        )
        order = self._create_production_run(
            product=self.product_a,
            line=self.line_1,
            planned_qty=Decimal("100"),
            actual_paper_kg=Decimal("200"),
            actual_foil_rolls=Decimal("1"),
            good_qty=Decimal("100"),
            scrap_qty=Decimal("0"),
        )
        paper_issue = MaterialIssue.objects.get(
            order=order, requirement__material=self.raw_paper
        )
        foil_issue = MaterialIssue.objects.get(
            order=order, requirement__material=self.raw_foil
        )
        self.assertEqual(paper_issue.unit_cost_snapshot, Decimal("60.000000"))
        self.assertEqual(foil_issue.unit_cost_snapshot, Decimal("300.000000"))
        self.assertEqual(
            paper_issue.cost_source, MaterialIssue.CostSource.LAST_PURCHASE_COST
        )
        self.assertEqual(
            foil_issue.cost_source, MaterialIssue.CostSource.LAST_PURCHASE_COST
        )

    def test_future_receipt_does_not_value_current_issue(self):
        now = timezone.now()
        self._post_priced_receipt(
            self.raw_paper, Decimal("1000"), Decimal("60.000000"),
            occurred_at=now - timedelta(hours=1),
        )
        self._post_priced_receipt(
            self.raw_paper, Decimal("1000"), Decimal("99.000000"),
            occurred_at=now + timedelta(days=1),
        )
        order = self._create_production_run(
            product=self.product_a,
            line=self.line_1,
            planned_qty=Decimal("50"),
            actual_paper_kg=Decimal("100"),
            actual_foil_rolls=Decimal("0"),
            good_qty=Decimal("50"),
            scrap_qty=Decimal("0"),
        )
        paper_issue = MaterialIssue.objects.get(
            order=order, requirement__material=self.raw_paper
        )
        self.assertEqual(paper_issue.unit_cost_snapshot, Decimal("60.000000"))
        self.assertEqual(
            paper_issue.cost_source, MaterialIssue.CostSource.LAST_PURCHASE_COST
        )

    def test_issue_snapshot_is_immutable_after_a_later_purchase(self):
        self._post_priced_receipt(self.raw_paper, Decimal("2000"), Decimal("60.000000"))
        order = self._create_production_run(
            product=self.product_a,
            line=self.line_1,
            planned_qty=Decimal("100"),
            actual_paper_kg=Decimal("200"),
            actual_foil_rolls=Decimal("1"),
            good_qty=Decimal("100"),
            scrap_qty=Decimal("0"),
        )
        paper_issue = MaterialIssue.objects.get(
            order=order, requirement__material=self.raw_paper
        )
        self.assertEqual(paper_issue.unit_cost_snapshot, Decimal("60.000000"))
        self._post_priced_receipt(self.raw_paper, Decimal("1000"), Decimal("70.000000"))
        paper_issue.refresh_from_db()
        self.assertEqual(paper_issue.unit_cost_snapshot, Decimal("60.000000"))
        period = calculate_monthly_costing(
            year=self.year, month=self.month, actor=self.staff_user
        )
        entry = period.product_entries.get(product=self.product_a)
        # 200 kg × 60 (issue snapshot) + 1 roll × 200 (fallback), not 70.
        self.assertEqual(entry.actual_material_cost, Decimal("12200.00"))

    def test_issue_snapshot_labels_lpc_and_fallback_sources(self):
        self._post_priced_receipt(self.raw_paper, Decimal("2000"), Decimal("60.000000"))
        order = self._create_production_run(
            product=self.product_a,
            line=self.line_1,
            planned_qty=Decimal("100"),
            actual_paper_kg=Decimal("200"),
            actual_foil_rolls=Decimal("1"),
            good_qty=Decimal("100"),
            scrap_qty=Decimal("0"),
        )
        paper_issue = MaterialIssue.objects.get(
            order=order, requirement__material=self.raw_paper
        )
        foil_issue = MaterialIssue.objects.get(
            order=order, requirement__material=self.raw_foil
        )
        self.assertEqual(
            paper_issue.cost_source, MaterialIssue.CostSource.LAST_PURCHASE_COST
        )
        self.assertEqual(
            foil_issue.cost_source, MaterialIssue.CostSource.STANDARD_PRICE_FALLBACK
        )
        self.assertEqual(foil_issue.unit_cost_snapshot, Decimal("200.000000"))

    def test_finalized_period_blocks_recalculation(self):
        self._post_priced_receipt(self.raw_paper, Decimal("2000"), Decimal("60.000000"))
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
            year=self.year,
            month=self.month,
            expense_type=MonthlyExpense.ExpenseType.LABOR,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("0.00"),
            description="لا توجد عمالة مباشرة مسجلة",
            created_by=self.staff_user,
        )
        MonthlyExpense.objects.create(
            year=self.year,
            month=self.month,
            expense_type=MonthlyExpense.ExpenseType.ELECTRICITY,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("100.00"),
            description="electricity",
            created_by=self.staff_user,
        )
        MonthlyExpense.objects.create(
            year=self.year,
            month=self.month,
            expense_type=MonthlyExpense.ExpenseType.WATER,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("50.00"),
            description="water",
            created_by=self.staff_user,
        )
        period = calculate_monthly_costing(
            year=self.year, month=self.month, actor=self.staff_user
        )
        period = finalize_costing_period(period=period, actor=self.staff_user)
        with self.assertRaises(ValidationError):
            calculate_monthly_costing(
                year=self.year, month=self.month, actor=self.staff_user
            )


class LastPurchaseCostMethodTests(CostingProductionMixin, TestCase):
    """The costing module now declares the last purchase cost as its method."""

    def test_valuation_method_is_last_purchase_cost(self):
        self.assertEqual(MATERIAL_VALUATION_METHOD, "LAST_PURCHASE_COST")
        period = CostingPeriod.objects.create(year=self.year, month=self.month)
        self.assertEqual(period.material_valuation_method, "LAST_PURCHASE_COST")
