from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from catalog.models import Product, ProductCategory, ProductUnitConversion, UnitOfMeasure
from .models import StockLedgerEntry, StockMovement, StorageLocation, Warehouse
from .services import balance_for, post_movement, reverse_movement, to_base_quantity


class InventoryLedgerTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user(username="staff", password="test-password", is_staff=True)
        self.regular = User.objects.create_user(username="reader", password="test-password")
        self.unit = UnitOfMeasure.objects.create(code="PCS", name_ar="قطعة", dimension="count")
        category, _ = ProductCategory.objects.get_or_create(code="raw-material", defaults={"name_ar": "مواد خام"})
        self.cup = Product.objects.create(sku="CUP-RAW", name_ar="ورق أكواب", category=category, base_uom=self.unit)
        self.tray = Product.objects.create(sku="TRAY-RAW", name_ar="مادة الحاويات", category=category, base_uom=self.unit)
        warehouse = Warehouse.objects.create(code="MAIN", name_ar="المخزن الرئيسي")
        self.a = StorageLocation.objects.create(warehouse=warehouse, code="A", name_ar="الموقع أ")
        self.b = StorageLocation.objects.create(warehouse=warehouse, code="B", name_ar="الموقع ب")

    def post(self, reference, kind, product=None, quantity="1", source=None, destination=None, reason=""):
        return post_movement(reference=reference, movement_type=kind, product=product or self.cup, quantity=Decimal(quantity), unit=self.unit,
                             actor=self.staff, source_location=source, destination_location=destination, reason=reason)

    def test_receipt_issue_transfer_and_reversal_use_common_ledger(self):
        self.post("R-CUP", StockMovement.Type.RECEIPT, quantity="10", destination=self.a)
        self.post("R-TRAY", StockMovement.Type.RECEIPT, product=self.tray, quantity="4", destination=self.a)
        self.post("I-CUP", StockMovement.Type.ISSUE, quantity="2", source=self.a)
        transfer = self.post("T-CUP", StockMovement.Type.TRANSFER, quantity="3", source=self.a, destination=self.b)
        self.assertEqual(balance_for(self.cup, self.a), Decimal("5.000000000000"))
        self.assertEqual(balance_for(self.cup, self.b), Decimal("3.000000000000"))
        self.assertEqual(balance_for(self.tray, self.a), Decimal("4.000000000000"))
        self.assertEqual(transfer.ledger_entries.count(), 2)
        reverse_movement(movement=transfer, reference="T-CUP-REV", actor=self.staff, reason="تصحيح التحويل")
        self.assertEqual(balance_for(self.cup, self.a), Decimal("8.000000000000"))
        self.assertEqual(balance_for(self.cup, self.b), Decimal("0"))
        self.assertEqual(StockLedgerEntry.objects.filter(product=self.cup).count(), 6)

    def test_invalid_transfer_is_atomic_and_does_not_create_a_movement(self):
        with self.assertRaises(ValidationError):
            self.post("BAD-TRANSFER", StockMovement.Type.TRANSFER, quantity="1", source=self.a, destination=self.a)
        self.assertFalse(StockMovement.objects.filter(reference="BAD-TRANSFER").exists())
        self.assertEqual(StockLedgerEntry.objects.count(), 0)

    def test_issue_above_available_stock_is_rejected_without_side_effects(self):
        self.post("RECEIPT-1", StockMovement.Type.RECEIPT, quantity="3", destination=self.a)
        with self.assertRaises(ValidationError):
            self.post("RECEIPT-1", StockMovement.Type.RECEIPT, quantity="3", destination=self.a)
        movement_count = StockMovement.objects.count()
        entry_count = StockLedgerEntry.objects.count()
        with self.assertRaisesMessage(ValidationError, "الرصيد المتاح"):
            self.post("ISSUE-4", StockMovement.Type.ISSUE, quantity="4", source=self.a)
        self.assertEqual(balance_for(self.cup, self.a), Decimal("3.000000000000"))
        self.assertEqual(StockMovement.objects.count(), movement_count)
        self.assertEqual(StockLedgerEntry.objects.count(), entry_count)

    def test_issue_equal_to_available_stock_is_allowed(self):
        self.post("RECEIPT-EXACT", StockMovement.Type.RECEIPT, quantity="3", destination=self.a)
        self.post("ISSUE-EXACT", StockMovement.Type.ISSUE, quantity="3", source=self.a)
        self.assertEqual(balance_for(self.cup, self.a), Decimal("0E-12"))

    def test_insufficient_transfer_is_atomic(self):
        self.post("TRANSFER-STOCK", StockMovement.Type.RECEIPT, quantity="2", destination=self.a)
        movement_count = StockMovement.objects.count()
        entry_count = StockLedgerEntry.objects.count()
        with self.assertRaisesMessage(ValidationError, "الرصيد المتاح"):
            self.post("TRANSFER-TOO-MUCH", StockMovement.Type.TRANSFER, quantity="3", source=self.a, destination=self.b)
        self.assertEqual(balance_for(self.cup, self.a), Decimal("2.000000000000"))
        self.assertEqual(balance_for(self.cup, self.b), Decimal("0"))
        self.assertEqual(StockMovement.objects.count(), movement_count)
        self.assertEqual(StockLedgerEntry.objects.count(), entry_count)
        self.assertFalse(StockMovement.objects.filter(reference="TRANSFER-TOO-MUCH").exists())

    def test_insufficient_decrease_adjustment_is_rejected(self):
        self.post("ADJUSTMENT-STOCK", StockMovement.Type.RECEIPT, quantity="2", destination=self.a)
        with self.assertRaisesMessage(ValidationError, "الرصيد المتاح"):
            self.post("ADJUSTMENT-OUT-TOO-MUCH", StockMovement.Type.ADJUSTMENT_OUT, quantity="3", source=self.a, reason="جرد")
        self.assertEqual(balance_for(self.cup, self.a), Decimal("2.000000000000"))
        self.assertFalse(StockMovement.objects.filter(reference="ADJUSTMENT-OUT-TOO-MUCH").exists())

    def test_receipt_reversal_cannot_make_balance_negative(self):
        receipt = self.post("REVERSAL-RECEIPT", StockMovement.Type.RECEIPT, quantity="5", destination=self.a)
        self.post("REVERSAL-ISSUE", StockMovement.Type.ISSUE, quantity="2", source=self.a)
        before_count = StockLedgerEntry.objects.count()
        with self.assertRaisesMessage(ValidationError, "الرصيد المتاح"):
            reverse_movement(movement=receipt, reference="REVERSAL-RECEIPT-REV", actor=self.staff, reason="تصحيح")
        self.assertEqual(balance_for(self.cup, self.a), Decimal("3.000000000000"))
        self.assertEqual(StockLedgerEntry.objects.count(), before_count)
        self.assertFalse(StockMovement.objects.filter(reference="REVERSAL-RECEIPT-REV").exists())

    def test_transfer_reversal_cannot_overdraw_destination(self):
        self.post("REVERSAL-TRANSFER-STOCK", StockMovement.Type.RECEIPT, quantity="5", destination=self.a)
        transfer = self.post("REVERSAL-TRANSFER", StockMovement.Type.TRANSFER, quantity="4", source=self.a, destination=self.b)
        self.post("REVERSAL-TRANSFER-ISSUE", StockMovement.Type.ISSUE, quantity="2", source=self.b)
        before_count = StockLedgerEntry.objects.count()
        with self.assertRaisesMessage(ValidationError, "الرصيد المتاح"):
            reverse_movement(movement=transfer, reference="REVERSAL-TRANSFER-REV", actor=self.staff, reason="تصحيح")
        self.assertEqual(balance_for(self.cup, self.a), Decimal("1.000000000000"))
        self.assertEqual(balance_for(self.cup, self.b), Decimal("2.000000000000"))
        self.assertEqual(StockLedgerEntry.objects.count(), before_count)
        self.assertFalse(StockMovement.objects.filter(reference="REVERSAL-TRANSFER-REV").exists())

    def test_reversal_movement_cannot_be_reversed(self):
        self.post("REVERSAL-ORIGINAL-STOCK", StockMovement.Type.RECEIPT, quantity="10", destination=self.a)
        issue = self.post("REVERSAL-ORIGINAL-ISSUE", StockMovement.Type.ISSUE, quantity="4", source=self.a)
        reversal = reverse_movement(
            movement=issue,
            reference="REVERSAL-ISSUE-REV",
            actor=self.staff,
            reason="تصحيح الصرف",
        )
        movement_count = StockMovement.objects.count()
        entry_count = StockLedgerEntry.objects.count()
        with self.assertRaisesMessage(ValidationError, "لا يمكن عكس حركة عكس"):
            reverse_movement(
                movement=reversal,
                reference="REVERSAL-OF-REVERSAL",
                actor=self.staff,
                reason="محاولة غير صحيحة",
            )
        self.assertEqual(balance_for(self.cup, self.a), Decimal("10.000000000000"))
        self.assertEqual(StockMovement.objects.count(), movement_count)
        self.assertEqual(StockLedgerEntry.objects.count(), entry_count)
        self.assertFalse(StockMovement.objects.filter(reference="REVERSAL-OF-REVERSAL").exists())

    def test_original_movement_cannot_be_reversed_twice(self):
        receipt = self.post("DOUBLE-REVERSAL-ORIGINAL", StockMovement.Type.RECEIPT, quantity="5", destination=self.a)
        reverse_movement(movement=receipt, reference="DOUBLE-REVERSAL-ONCE", actor=self.staff, reason="تصحيح")
        movement_count = StockMovement.objects.count()
        entry_count = StockLedgerEntry.objects.count()
        with self.assertRaisesMessage(ValidationError, "تم عكس هذه الحركة من قبل"):
            reverse_movement(
                movement=receipt,
                reference="DOUBLE-REVERSAL-TWICE",
                actor=self.staff,
                reason="محاولة مكررة",
            )
        self.assertEqual(balance_for(self.cup, self.a), Decimal("0E-12"))
        self.assertEqual(StockMovement.objects.count(), movement_count)
        self.assertEqual(StockLedgerEntry.objects.count(), entry_count)
        self.assertFalse(StockMovement.objects.filter(reference="DOUBLE-REVERSAL-TWICE").exists())

    def test_adjustments_require_reason_and_opening_is_controlled(self):
        with self.assertRaises(ValidationError):
            self.post("ADJ-1", StockMovement.Type.ADJUSTMENT_IN, quantity="2", destination=self.a)
        self.post("OPEN-1", StockMovement.Type.OPENING, quantity="2", destination=self.b)
        with self.assertRaises(ValidationError):
            self.post("OPEN-2", StockMovement.Type.OPENING, quantity="1", destination=self.b)

    def test_conversion_rejects_inexact_result_without_rounding(self):
        alternate = UnitOfMeasure.objects.create(code="HALF", name_ar="نصف", dimension="count")
        ProductUnitConversion.objects.create(product=self.cup, unit=alternate, factor=Decimal("0.000000000001"))
        with self.assertRaises(ValidationError):
            post_movement(reference="INEXACT", movement_type=StockMovement.Type.RECEIPT, product=self.cup, quantity=Decimal("0.5"),
                          unit=alternate, actor=self.staff, destination_location=self.a)

    def test_inactive_units_are_rejected_before_stock_posting(self):
        inactive = UnitOfMeasure.objects.create(
            code="INACTIVE", name_ar="وحدة موقوفة", dimension="count", active=False
        )
        ProductUnitConversion.objects.create(
            product=self.cup, unit=inactive, factor=Decimal("1")
        )
        with self.assertRaisesMessage(ValidationError, "وحدة قياس نشطة"):
            to_base_quantity(self.cup, Decimal("1"), inactive)
        with self.assertRaisesMessage(ValidationError, "وحدة قياس نشطة"):
            post_movement(
                reference="INACTIVE-UNIT",
                movement_type=StockMovement.Type.RECEIPT,
                product=self.cup,
                quantity=Decimal("1"),
                unit=inactive,
                actor=self.staff,
                destination_location=self.a,
            )
        self.assertFalse(StockMovement.objects.filter(reference="INACTIVE-UNIT").exists())

    def test_posted_movements_cannot_be_edited_or_deleted(self):
        movement = self.post("FIXED", StockMovement.Type.RECEIPT, destination=self.a)
        entry = movement.ledger_entries.get()
        movement.notes = "changed"
        with self.assertRaises(ValidationError):
            movement.save()
        with self.assertRaises(ValidationError):
            movement.delete()
        entry.quantity_base = Decimal("99")
        with self.assertRaises(ValidationError):
            entry.save()
        with self.assertRaises(ValidationError):
            entry.delete()
        with self.assertRaises(ValidationError):
            StockLedgerEntry.objects.filter(pk=entry.pk).update(quantity_base=Decimal("99"))
        with self.assertRaises(ValidationError):
            StockLedgerEntry.objects.filter(pk=entry.pk).delete()
        with self.assertRaises(ValidationError):
            StockLedgerEntry.objects.bulk_update([entry], ["quantity_base"])

    @skipUnlessDBFeature("supports_transactions")
    def test_postgresql_triggers_reject_direct_sql_edits_and_deletes(self):
        if connection.vendor != "postgresql":
            self.skipTest("Database triggers for direct SQL protection are installed on PostgreSQL only.")
        movement = self.post("SQL-IMMUTABLE", StockMovement.Type.RECEIPT, destination=self.a)
        entry = movement.ledger_entries.get()
        for sql, params in (
            ("UPDATE inventory_stockledgerentry SET quantity_base = 99 WHERE id = %s", [entry.pk]),
            ("DELETE FROM inventory_stockledgerentry WHERE id = %s", [entry.pk]),
            ("UPDATE inventory_stockmovement SET notes = 'changed' WHERE id = %s", [movement.pk]),
        ):
            with self.subTest(sql=sql), self.assertRaises(DatabaseError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)

    def test_generic_source_reference_is_available_for_future_apps(self):
        movement = post_movement(reference="PROD-REF-1", movement_type=StockMovement.Type.RECEIPT, product=self.cup,
                                 quantity=Decimal("2"), unit=self.unit, actor=self.staff, destination_location=self.a,
                                 source_reference_type="manufacturing.operation", source_reference_id="operation-42")
        self.assertEqual(movement.source_reference_type, "manufacturing.operation")
        self.assertEqual(movement.source_reference_id, "operation-42")
        with self.assertRaises(ValidationError):
            post_movement(reference="PROD-REF-2", movement_type=StockMovement.Type.RECEIPT, product=self.cup,
                          quantity=Decimal("2"), unit=self.unit, actor=self.staff, destination_location=self.a,
                          source_reference_type="manufacturing.operation", source_reference_id="operation-42")

    def test_product_sku_and_base_unit_are_stable_after_stock_activity(self):
        self.post("SKU-LOCK", StockMovement.Type.RECEIPT, destination=self.a)
        self.cup.sku = "NEW-SKU"
        with self.assertRaises(ValidationError):
            self.cup.full_clean()

    def test_inventory_views_require_login_and_staff_for_writes(self):
        response = self.client.get(reverse("inventory:balances"), secure=True)
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.regular)
        self.assertEqual(self.client.get(reverse("inventory:balances"), secure=True).status_code, 200)
        self.assertEqual(self.client.get(reverse("inventory:movement", args=["RECEIPT"]), secure=True).status_code, 403)
        self.client.force_login(self.staff)
        response = self.client.get(reverse("inventory:balances"), secure=True)
        self.assertContains(response, "<td>0</td>", html=True)

    def test_catalog_and_inventory_pages_render_for_authenticated_staff(self):
        self.client.force_login(self.staff)
        routes = (
            reverse("catalog:products"), reverse("catalog:configuration"),
            reverse("catalog:product_add"), reverse("catalog:config_add", args=["unit"]),
            reverse("inventory:dashboard"), reverse("inventory:warehouses"),
            reverse("inventory:warehouse_add"), reverse("inventory:location_add"),
            reverse("inventory:balances"), reverse("inventory:history"),
            reverse("inventory:movement", args=["RECEIPT"]),
        )
        for route in routes:
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route, secure=True).status_code, 200)


class InventoryConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user(username="concurrent", password="test-password", is_staff=True)
        unit = UnitOfMeasure.objects.create(code="PCS", name_ar="قطعة", dimension="count")
        category, _ = ProductCategory.objects.get_or_create(code="raw-material", defaults={"name_ar": "مواد خام"})
        self.product = Product.objects.create(sku="CONC-RAW", name_ar="مادة خام", category=category, base_uom=unit)
        warehouse = Warehouse.objects.create(code="MAIN", name_ar="المخزن الرئيسي")
        self.location = StorageLocation.objects.create(warehouse=warehouse, code="A", name_ar="الموقع أ")
        post_movement(reference="INITIAL", movement_type=StockMovement.Type.RECEIPT, product=self.product, quantity=Decimal("10"), unit=unit,
                      actor=self.staff, destination_location=self.location)
        self.unit = unit

    @skipUnlessDBFeature("has_select_for_update")
    def test_parallel_duplicate_reference_cannot_be_posted_twice(self):
        barrier = Barrier(2)

        def attempt(reference):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.staff.pk)
                barrier.wait(timeout=10)
                try:
                    post_movement(reference="DUPLICATE-RECEIPT", movement_type=StockMovement.Type.RECEIPT, product=self.product, quantity=Decimal("1"),
                                  unit=self.unit, actor=actor, destination_location=self.location)
                    return "posted"
                except ValidationError:
                    return "rejected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ("ATTEMPT-A", "ATTEMPT-B")))
        self.assertCountEqual(results, ["posted", "rejected"])
        self.assertEqual(balance_for(self.product, self.location), Decimal("11.000000000000"))

    @skipUnlessDBFeature("has_select_for_update")
    def test_parallel_issues_cannot_consume_more_than_available_stock(self):
        barrier = Barrier(2)

        def attempt(reference):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.staff.pk)
                barrier.wait(timeout=10)
                try:
                    post_movement(reference=reference, movement_type=StockMovement.Type.ISSUE, product=self.product,
                                  quantity=Decimal("7"), unit=self.unit, actor=actor, source_location=self.location)
                    return "posted"
                except ValidationError:
                    return "rejected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ("COMPETING-ISSUE-A", "COMPETING-ISSUE-B")))
        self.assertCountEqual(results, ["posted", "rejected"])
        self.assertEqual(balance_for(self.product, self.location), Decimal("3.000000000000"))
        self.assertEqual(StockMovement.objects.filter(movement_type=StockMovement.Type.ISSUE).count(), 1)
