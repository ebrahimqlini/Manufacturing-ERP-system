from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from unittest import mock
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from catalog.models import Product, ProductCategory, ProductUnitConversion, UnitOfMeasure
from costing.models import RawMaterialPrice
from costing.services import MATERIAL_VALUATION_METHOD, get_material_price
from inventory.models import StockMovement, StorageLocation, Warehouse
from inventory.services import balance_for, reverse_movement

from . import services as purchasing_services
from .forms import SupplierForm
from .models import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseReceipt,
    PurchaseReceiptLine,
    Supplier,
)
from .services import cancel_order, confirm_order, create_receipt, save_draft_order


class PurchasingFixtureMixin:
    def create_fixture(self):
        User = get_user_model()
        from django.contrib.auth.models import Permission
        suffix = uuid.uuid4().hex[:8].upper()
        self.staff = User.objects.create_user(
            username=f"pur-staff-{suffix}", password="test-password", is_staff=True
        )
        perms = Permission.objects.filter(
            content_type__app_label__in=[
                "manufacturing", "inventory", "costing", "purchasing", "sales"
            ]
        )
        self.staff.user_permissions.set(perms)
        self.regular = User.objects.create_user(
            username=f"pur-reader-{suffix}", password="test-password"
        )
        self.kg = UnitOfMeasure.objects.create(code=f"KG{suffix}", name_ar="كجم", dimension="mass")
        self.ton = UnitOfMeasure.objects.create(code=f"TN{suffix}", name_ar="طن", dimension="mass")
        self.pcs = UnitOfMeasure.objects.create(code=f"PC{suffix}", name_ar="قطعة", dimension="count")
        category = ProductCategory.objects.get_or_create(
            code="raw-material", defaults={"name_ar": "مواد خام"}
        )[0]
        self.material = Product.objects.create(
            sku=f"PAPER-{suffix}", name_ar="رول ورق خام", category=category, base_uom=self.kg
        )
        ProductUnitConversion.objects.create(product=self.material, unit=self.ton, factor=Decimal("1000"))
        self.material2 = Product.objects.create(
            sku=f"FOIL-{suffix}", name_ar="رول تغليف", category=category, base_uom=self.pcs
        )
        warehouse = Warehouse.objects.create(code=f"PUR-{suffix}", name_ar="مخزن المشتريات")
        self.location = StorageLocation.objects.create(warehouse=warehouse, code="RAW", name_ar="موقع المواد")
        self.supplier = Supplier.objects.create(
            code=f"SUP-{suffix}", name_ar="مورد الورق", created_by=self.staff
        )

    def line(self, material=None, quantity="2", unit=None, unit_price="5000"):
        return {
            "material": material or self.material,
            "quantity": Decimal(quantity),
            "unit": unit or self.ton,
            "unit_price": Decimal(unit_price),
            "notes": "",
        }

    def make_order(self, lines=None, confirm=False, supplier=None, **overrides):
        values = {
            "supplier": supplier or self.supplier,
            "order_date": date(2026, 10, 1),
            "expected_date": None,
            "supplier_reference": "",
            "notes": "",
        }
        values.update(overrides)
        order = save_draft_order(actor=self.staff, lines=lines or [self.line()], **values)
        if confirm:
            order = confirm_order(order=order, actor=self.staff)
        return order

    def receive(self, order, lines, reference="GRN-1"):
        return create_receipt(
            order=order,
            actor=self.staff,
            reference=reference,
            received_date=date(2026, 10, 5),
            destination_location=self.location,
            lines=lines,
        )


class SupplierTests(PurchasingFixtureMixin, TestCase):
    def setUp(self):
        self.create_fixture()

    def test_supplier_code_is_normalized_and_name_is_required(self):
        form = SupplierForm(data={"code": "  sup-9 ", "name_ar": "مورد جديد", "active": True})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["code"], "SUP-9")
        blank = SupplierForm(data={"code": "SUP-10", "name_ar": "  ", "active": True})
        self.assertFalse(blank.is_valid())
        self.assertIn("name_ar", blank.errors)

    def test_duplicate_supplier_code_is_rejected_by_the_form(self):
        form = SupplierForm(data={"code": self.supplier.code, "name_ar": "مكرر", "active": True})
        self.assertFalse(form.is_valid())
        self.assertIn("code", form.errors)

    def test_invalid_email_is_rejected(self):
        form = SupplierForm(data={"code": "SUP-11", "name_ar": "بريد", "email": "not-an-email", "active": True})
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_inactive_supplier_cannot_be_used_for_a_new_order(self):
        inactive = Supplier.objects.create(code="SUP-OFF", name_ar="مورد موقوف", active=False, created_by=self.staff)
        with self.assertRaises(ValidationError):
            save_draft_order(
                actor=self.staff,
                lines=[self.line()],
                supplier=inactive,
                order_date=date(2026, 10, 1),
            )


class PurchaseOrderWorkflowTests(PurchasingFixtureMixin, TestCase):
    def setUp(self):
        self.create_fixture()

    def test_draft_order_stores_lines_with_base_quantity_and_prices(self):
        order = self.make_order()
        self.assertTrue(order.order_number.startswith("PO-"))
        self.assertEqual(order.status, PurchaseOrder.Status.DRAFT)
        line = order.lines.get()
        self.assertEqual(line.quantity_base, Decimal("2000.000000000000"))
        self.assertEqual(line.price_per_base_unit, Decimal("5.000000"))
        self.assertEqual(line.line_total, Decimal("10000.00"))
        self.assertEqual(order.ordered_quantity_base, Decimal("2000.000000000000"))
        self.assertEqual(order.ordered_value, Decimal("10000.00"))
        self.assertEqual(order.receipt_state, PurchaseOrder.ReceiptState.NOT_RECEIVED)

    def test_draft_lines_can_be_replaced_but_confirmed_lines_cannot(self):
        order = self.make_order()
        order = save_draft_order(
            actor=self.staff,
            order=order,
            lines=[self.line(unit_price="6000")],
            supplier=self.supplier,
            order_date=date(2026, 10, 1),
            expected_date=None,
            supplier_reference="",
            notes="",
        )
        self.assertEqual(order.lines.count(), 1)
        self.assertEqual(order.lines.get().line_total, Decimal("12000.00"))

        confirm_order(order=order, actor=self.staff)
        with self.assertRaises(ValidationError):
            save_draft_order(
                actor=self.staff,
                order=order,
                lines=[self.line()],
                supplier=self.supplier,
                order_date=date(2026, 10, 1),
                expected_date=None,
                supplier_reference="",
                notes="",
            )
        line = order.lines.get()
        line.quantity = Decimal("3")
        with self.assertRaises(ValidationError):
            line.save()

    def test_confirm_requires_lines_and_sets_audit_fields(self):
        order = self.make_order()
        order.lines.all().delete()
        with self.assertRaises(ValidationError):
            confirm_order(order=order, actor=self.staff)

        order = self.make_order()
        order = confirm_order(order=order, actor=self.staff)
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)
        self.assertIsNotNone(order.confirmed_at)
        self.assertEqual(order.confirmed_by, self.staff)
        with self.assertRaises(ValidationError):
            confirm_order(order=order, actor=self.staff)

    def test_cancel_rules(self):
        draft = self.make_order()
        with self.assertRaises(ValidationError):
            cancel_order(order=draft, actor=self.staff, reason="  ")
        draft = cancel_order(order=draft, actor=self.staff, reason="طلب خاطئ")
        self.assertEqual(draft.status, PurchaseOrder.Status.CANCELLED)
        self.assertEqual(draft.cancel_reason, "طلب خاطئ")

        confirmed = self.make_order(confirm=True)
        confirmed = cancel_order(order=confirmed, actor=self.staff, reason="توقف التوريد")
        self.assertEqual(confirmed.status, PurchaseOrder.Status.CANCELLED)

        with_receipt = self.make_order(confirm=True)
        line = with_receipt.lines.get()
        self.receive(with_receipt, [{"order_line": line, "quantity": Decimal("10"), "unit": self.kg}])
        with self.assertRaises(ValidationError):
            cancel_order(order=with_receipt, actor=self.staff, reason="محاولة")

    def test_expected_date_cannot_precede_order_date(self):
        order = PurchaseOrder(
            order_number="PO-MANUAL-1",
            supplier=self.supplier,
            order_date=date(2026, 10, 10),
            expected_date=date(2026, 10, 1),
            created_by=self.staff,
        )
        with self.assertRaises(ValidationError):
            order.full_clean()

    def test_negative_line_values_are_rejected(self):
        order = self.make_order()
        line = order.lines.get()
        line.unit_price = Decimal("-1")
        with self.assertRaises(ValidationError):
            line.full_clean()
        line.unit_price = Decimal("1")
        line.quantity = Decimal("0")
        with self.assertRaises(ValidationError):
            line.full_clean()

    def test_receiving_requires_a_confirmed_order(self):
        order = self.make_order()
        line = order.lines.get()
        with self.assertRaises(ValidationError):
            self.receive(order, [{"order_line": line, "quantity": Decimal("10"), "unit": self.kg}])


class ReceiptWorkflowTests(PurchasingFixtureMixin, TestCase):
    def setUp(self):
        self.create_fixture()
        self.order = self.make_order(confirm=True)
        self.order_line = self.order.lines.get()

    def test_partial_then_full_receipt_updates_stock_and_state(self):
        receipt = self.receive(self.order, [{"order_line": self.order_line, "quantity": Decimal("500"), "unit": self.kg}])
        self.assertEqual(balance_for(self.material, self.location), Decimal("500.000000000000"))
        self.assertEqual(self.order.received_quantity_base, Decimal("500.000000000000"))
        self.assertEqual(self.order.outstanding_quantity_base, Decimal("1500.000000000000"))
        self.assertEqual(self.order.receipt_state, PurchaseOrder.ReceiptState.PARTIALLY_RECEIVED)

        receipt_line = receipt.lines.get()
        self.assertEqual(receipt_line.unit_price, Decimal("5000.0000"))
        self.assertEqual(receipt_line.price_per_base_unit, Decimal("5.000000"))
        self.assertEqual(receipt_line.line_total, Decimal("2500.00"))
        self.assertEqual(receipt_line.movement.movement_type, StockMovement.Type.RECEIPT)
        self.assertEqual(receipt_line.movement.destination_location, self.location)
        self.assertEqual(receipt_line.movement.external_reference, "GRN-1")
        self.assertEqual(receipt_line.movement.source_reference_type, "purchasing.purchase_receipt")

        self.receive(
            self.order,
            [{"order_line": self.order_line, "quantity": Decimal("1500"), "unit": self.kg}],
            reference="GRN-2",
        )
        self.assertEqual(balance_for(self.material, self.location), Decimal("2000.000000000000"))
        self.assertEqual(self.order.receipt_state, PurchaseOrder.ReceiptState.RECEIVED)
        self.assertEqual(self.order.outstanding_quantity_base, Decimal("0"))
        self.assertEqual(self.order.received_value, Decimal("10000.00"))

    def test_receipt_uses_unit_conversion_rules(self):
        self.receive(self.order, [{"order_line": self.order_line, "quantity": Decimal("1"), "unit": self.ton}])
        self.assertEqual(balance_for(self.material, self.location), Decimal("1000.000000000000"))
        self.assertEqual(self.order.receipt_state, PurchaseOrder.ReceiptState.PARTIALLY_RECEIVED)

    def test_invalid_unit_is_rejected_without_side_effects(self):
        with self.assertRaises(ValidationError):
            self.receive(self.order, [{"order_line": self.order_line, "quantity": Decimal("1"), "unit": self.pcs}])
        self.assertEqual(PurchaseReceipt.objects.count(), 0)
        self.assertEqual(balance_for(self.material, self.location), Decimal("0"))
        self.assertFalse(StockMovement.objects.filter(source_reference_type="purchasing.purchase_receipt").exists())

    def test_over_receiving_is_rejected_and_leaves_no_side_effects(self):
        self.receive(self.order, [{"order_line": self.order_line, "quantity": Decimal("500"), "unit": self.kg}])
        with self.assertRaises(ValidationError):
            self.receive(
                self.order,
                [{"order_line": self.order_line, "quantity": Decimal("1501"), "unit": self.kg}],
                reference="GRN-OVER",
            )
        self.assertEqual(PurchaseReceipt.objects.count(), 1)
        self.assertEqual(PurchaseReceiptLine.objects.count(), 1)
        self.assertEqual(balance_for(self.material, self.location), Decimal("500.000000000000"))
        self.assertFalse(PurchaseReceipt.objects.filter(reference="GRN-OVER").exists())

    def test_duplicate_receipt_reference_is_prevented(self):
        self.receive(self.order, [{"order_line": self.order_line, "quantity": Decimal("100"), "unit": self.kg}])
        with self.assertRaisesMessage(ValidationError, "مستخدم من قبل"):
            self.receive(self.order, [{"order_line": self.order_line, "quantity": Decimal("100"), "unit": self.kg}])
        self.assertEqual(PurchaseReceipt.objects.count(), 1)
        self.assertEqual(
            StockMovement.objects.filter(source_reference_type="purchasing.purchase_receipt").count(), 1
        )

    def test_duplicate_line_in_the_same_receipt_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.receive(
                self.order,
                [
                    {"order_line": self.order_line, "quantity": Decimal("10"), "unit": self.kg},
                    {"order_line": self.order_line, "quantity": Decimal("10"), "unit": self.kg},
                ],
            )
        self.assertEqual(PurchaseReceipt.objects.count(), 0)

    def test_receipt_of_fully_received_order_is_rejected(self):
        self.receive(self.order, [{"order_line": self.order_line, "quantity": Decimal("2000"), "unit": self.kg}])
        with self.assertRaises(ValidationError):
            self.receive(
                self.order,
                [{"order_line": self.order_line, "quantity": Decimal("1"), "unit": self.kg}],
                reference="GRN-EXTRA",
            )
        self.assertEqual(PurchaseReceipt.objects.count(), 1)

    def test_failed_second_line_rolls_back_the_first_movement(self):
        order = self.make_order(
            lines=[
                self.line(quantity="1000", unit=self.kg, unit_price="2"),
                self.line(material=self.material2, quantity="100", unit=self.pcs, unit_price="3"),
            ],
            confirm=True,
        )
        lines = list(order.lines.order_by("sequence"))
        real_post = purchasing_services.post_movement
        calls = {"count": 0}

        def flaky(**kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise ValidationError("فشل متعمد في الحركة الثانية")
            return real_post(**kwargs)

        with mock.patch("purchasing.services.post_movement", side_effect=flaky):
            with self.assertRaises(ValidationError):
                self.receive(
                    order,
                    [
                        {"order_line": lines[0], "quantity": Decimal("1000"), "unit": self.kg},
                        {"order_line": lines[1], "quantity": Decimal("100"), "unit": self.pcs},
                    ],
                )
        self.assertEqual(PurchaseReceipt.objects.count(), 0)
        self.assertEqual(PurchaseReceiptLine.objects.count(), 0)
        self.assertEqual(balance_for(self.material, self.location), Decimal("0"))
        self.assertEqual(balance_for(self.material2, self.location), Decimal("0"))
        self.assertEqual(
            StockMovement.objects.filter(source_reference_type="purchasing.purchase_receipt").count(), 0
        )

    def test_inventory_reversal_reduces_net_received_quantity(self):
        receipt = self.receive(self.order, [{"order_line": self.order_line, "quantity": Decimal("500"), "unit": self.kg}])
        receipt_line = receipt.lines.get()
        reverse_movement(movement=receipt_line.movement, reference="GRN-1-REV", actor=self.staff, reason="تصحيح")
        self.assertEqual(balance_for(self.material, self.location), Decimal("0"))
        self.assertEqual(self.order.received_quantity_base, Decimal("0"))
        self.assertEqual(self.order.receipt_state, PurchaseOrder.ReceiptState.NOT_RECEIVED)
        self.assertEqual(self.order.outstanding_quantity_base, Decimal("2000.000000000000"))

    def test_receipt_documents_and_lines_are_immutable(self):
        receipt = self.receive(self.order, [{"order_line": self.order_line, "quantity": Decimal("10"), "unit": self.kg}])
        receipt.notes = "تعديل"
        with self.assertRaises(ValidationError):
            receipt.save()
        receipt_line = receipt.lines.get()
        receipt_line.quantity = Decimal("20")
        with self.assertRaises(ValidationError):
            receipt_line.save()
        with self.assertRaises(ValidationError):
            receipt_line.delete()
        with self.assertRaises(ValidationError):
            receipt.delete()


class PurchasingPriceTraceabilityTests(PurchasingFixtureMixin, TestCase):
    def setUp(self):
        self.create_fixture()

    def test_purchase_price_is_recorded_but_does_not_change_costing_valuation(self):
        RawMaterialPrice.objects.create(
            material=self.material,
            price_per_unit=Decimal("3.0000"),
            unit=self.kg,
            effective_date=date(2026, 9, 1),
            created_by=self.staff,
        )
        self.assertEqual(get_material_price(self.material, date(2026, 10, 15)), Decimal("3.000000"))

        order = self.make_order(confirm=True)
        line = order.lines.get()
        receipt = self.receive(order, [{"order_line": line, "quantity": Decimal("500"), "unit": self.kg}])
        receipt_line = receipt.lines.get()

        # The purchase price is traceable on the document...
        self.assertEqual(receipt_line.unit_price, Decimal("5000.0000"))
        self.assertEqual(receipt_line.price_per_base_unit, Decimal("5.000000"))
        # ...but the owner-entered costing price and the valuation method are unchanged.
        self.assertEqual(get_material_price(self.material, date(2026, 10, 15)), Decimal("3.000000"))
        self.assertEqual(RawMaterialPrice.objects.count(), 1)
        self.assertEqual(MATERIAL_VALUATION_METHOD, "LAST_PURCHASE_COST")


class PurchasingPriceHistoryFilterTests(PurchasingFixtureMixin, TestCase):
    """The price-history page must filter by supplier via the receipt's order."""

    def setUp(self):
        self.create_fixture()
        other_supplier = Supplier.objects.create(
            code="SUP-OTHER", name_ar="مورد آخر", created_by=self.staff
        )
        self.order_a = self.make_order(confirm=True)
        self.line_a = self.order_a.lines.get()
        self.receipt_a = self.receive(
            self.order_a,
            [{"order_line": self.line_a, "quantity": Decimal("500"), "unit": self.kg}],
            reference="GRN-FILTER-A",
        )
        self.order_b = self.make_order(
            confirm=True,
            supplier=other_supplier,
            lines=[self.line(material=self.material2, quantity="100", unit=self.pcs, unit_price="3")],
        )
        self.line_b = self.order_b.lines.get()
        self.receipt_b = self.receive(
            self.order_b,
            [{"order_line": self.line_b, "quantity": Decimal("100"), "unit": self.pcs}],
            reference="GRN-FILTER-B",
        )

    def _result_lines(self, response):
        return list(response.context["page"].object_list)

    def test_unfiltered_page_lists_both_receipt_lines(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("purchasing:price_history"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {line.pk for line in self._result_lines(response)},
            {self.receipt_a.lines.get().pk, self.receipt_b.lines.get().pk},
        )

    def test_supplier_filter_returns_only_that_suppliers_receipt_lines(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("purchasing:price_history"), {"supplier": self.supplier.pk}
        )
        self.assertEqual(response.status_code, 200)
        lines = self._result_lines(response)
        self.assertEqual([line.pk for line in lines], [self.receipt_a.lines.get().pk])
        self.assertEqual({line.receipt.order.supplier_id for line in lines}, {self.supplier.pk})

    def test_material_filter_returns_only_that_materials_receipt_lines(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("purchasing:price_history"), {"material": self.material.pk}
        )
        self.assertEqual(response.status_code, 200)
        lines = self._result_lines(response)
        self.assertEqual([line.pk for line in lines], [self.receipt_a.lines.get().pk])
        self.assertEqual({line.order_line.material_id for line in lines}, {self.material.pk})

    def test_combined_supplier_and_material_filters_match_together(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("purchasing:price_history"),
            {"supplier": self.supplier.pk, "material": self.material.pk},
        )
        self.assertEqual(response.status_code, 200)
        lines = self._result_lines(response)
        self.assertEqual([line.pk for line in lines], [self.receipt_a.lines.get().pk])
        self.assertEqual({line.receipt.order.supplier_id for line in lines}, {self.supplier.pk})
        self.assertEqual({line.order_line.material_id for line in lines}, {self.material.pk})

    def test_nonmatching_supplier_and_material_returns_no_rows(self):
        self.client.force_login(self.staff)
        # Material 2 belongs to the other supplier's order, so this pair matches nothing.
        response = self.client.get(
            reverse("purchasing:price_history"),
            {"supplier": self.supplier.pk, "material": self.material2.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._result_lines(response), [])

    def test_regular_users_remain_forbidden_from_price_history(self):
        self.client.force_login(self.regular)
        response = self.client.get(
            reverse("purchasing:price_history"), {"supplier": self.supplier.pk}
        )
        self.assertEqual(response.status_code, 403)


class PurchasingViewAndPermissionTests(PurchasingFixtureMixin, TestCase):
    def setUp(self):
        self.create_fixture()

    def _routes(self):
        return [
            ("purchasing:order_list", {}),
            ("purchasing:order_add", {}),
            ("purchasing:supplier_list", {}),
            ("purchasing:supplier_add", {}),
            ("purchasing:price_history", {}),
        ]

    def test_anonymous_users_are_redirected_to_login(self):
        for name, kwargs in self._routes():
            response = self.client.get(reverse(name, kwargs=kwargs))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn("/login/", response.url)

    def test_regular_users_are_forbidden(self):
        self.client.force_login(self.regular)
        for name, kwargs in self._routes():
            response = self.client.get(reverse(name, kwargs=kwargs))
            self.assertEqual(response.status_code, 403, name)

    def test_staff_users_can_view_purchasing_pages(self):
        self.client.force_login(self.staff)
        for name, kwargs in self._routes():
            response = self.client.get(reverse(name, kwargs=kwargs))
            self.assertEqual(response.status_code, 200, name)

    def test_regular_users_cannot_post_state_changes(self):
        order = self.make_order()
        self.client.force_login(self.regular)
        response = self.client.post(reverse("purchasing:order_confirm", args=[order.pk]))
        self.assertEqual(response.status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.DRAFT)

    def test_staff_can_create_and_confirm_an_order_through_the_ui(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("purchasing:order_add"),
            {
                "supplier": self.supplier.pk,
                "order_date": "2026-10-01",
                "expected_date": "",
                "supplier_reference": "SUP-REF-1",
                "notes": "",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-material": self.material.pk,
                "lines-0-quantity": "2",
                "lines-0-unit": self.ton.pk,
                "lines-0-unit_price": "5000",
                "lines-0-notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        order = PurchaseOrder.objects.get()
        self.assertEqual(order.lines.count(), 1)
        self.assertEqual(order.lines.get().quantity_base, Decimal("2000.000000000000"))

    def test_staff_can_receive_through_the_ui(self):
        order = self.make_order(confirm=True)
        line = order.lines.get()
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("purchasing:receipt_add", args=[order.pk]),
            {
                "reference": "GRN-UI-1",
                "supplier_reference": "",
                "received_date": "2026-10-05",
                "destination_location": self.location.pk,
                "notes": "",
                f"quantity_{line.pk}": "500",
                f"unit_{line.pk}": self.kg.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(balance_for(self.material, self.location), Decimal("500.000000000000"))

    def test_navigation_link_is_hidden_from_regular_users(self):
        url = reverse("purchasing:order_list")
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("core:home")), url)
        self.client.force_login(self.regular)
        self.assertNotContains(self.client.get(reverse("core:home")), url)


class PurchasingConcurrencyTests(PurchasingFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.create_fixture()
        self.order = self.make_order(confirm=True)
        self.order_line = self.order.lines.get()

    @skipUnlessDBFeature("has_select_for_update")
    def test_parallel_duplicate_receipt_reference_posts_once(self):
        barrier = Barrier(2)

        def attempt():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.staff.pk)
                order = PurchaseOrder.objects.get(pk=self.order.pk)
                line = order.lines.get()
                barrier.wait(timeout=10)
                try:
                    create_receipt(
                        order=order,
                        actor=actor,
                        reference="RACE-DUP",
                        received_date=date(2026, 10, 5),
                        destination_location=self.location,
                        lines=[{"order_line": line, "quantity": Decimal("10"), "unit": self.kg}],
                    )
                    return "posted"
                except ValidationError:
                    return "rejected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(results, ["posted", "rejected"])
        self.assertEqual(PurchaseReceipt.objects.filter(reference="RACE-DUP").count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_parallel_receipts_cannot_over_receive(self):
        barrier = Barrier(2)

        def attempt(reference):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.staff.pk)
                order = PurchaseOrder.objects.get(pk=self.order.pk)
                line = order.lines.get()
                barrier.wait(timeout=10)
                try:
                    create_receipt(
                        order=order,
                        actor=actor,
                        reference=reference,
                        received_date=date(2026, 10, 5),
                        destination_location=self.location,
                        lines=[{"order_line": line, "quantity": Decimal("2000"), "unit": self.kg}],
                    )
                    return "posted"
                except ValidationError:
                    return "rejected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ("RACE-A", "RACE-B")))
        self.assertCountEqual(results, ["posted", "rejected"])
        self.assertEqual(PurchaseReceipt.objects.count(), 1)
        self.assertEqual(balance_for(self.material, self.location), Decimal("2000.000000000000"))
