from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from threading import Barrier
from unittest import mock
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from catalog.models import Product, ProductCategory, ProductUnitConversion, UnitOfMeasure
from costing.models import RawMaterialPrice
from costing.services import MATERIAL_VALUATION_METHOD, get_material_price
from inventory.models import StockMovement, StorageLocation, Warehouse
from inventory.services import balance_for, post_movement, reverse_movement

from . import services as sales_services
from .forms import CustomerForm
from .models import Customer, SalesOrder, SalesOrderLine, Shipment, ShipmentLine
from .services import (
    cancel_order,
    complete_order,
    confirm_order,
    create_shipment,
    reopen_completed_order,
    save_draft_order,
)


def _rounded_money(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class SalesFixtureMixin:
    def create_fixture(self):
        User = get_user_model()
        from django.contrib.auth.models import Permission
        suffix = uuid.uuid4().hex[:8].upper()
        self.staff = User.objects.create_user(
            username=f"sal-staff-{suffix}", password="test-password", is_staff=True
        )
        perms = Permission.objects.filter(
            content_type__app_label__in=[
                "manufacturing", "inventory", "costing", "purchasing", "sales"
            ]
        )
        self.staff.user_permissions.set(perms)
        self.regular = User.objects.create_user(
            username=f"sal-reader-{suffix}", password="test-password"
        )
        self.pcs = UnitOfMeasure.objects.create(code=f"PC{suffix}", name_ar="قطعة", dimension="count")
        self.carton = UnitOfMeasure.objects.create(code=f"CTN{suffix}", name_ar="كرتونة", dimension="count")
        self.kg = UnitOfMeasure.objects.create(code=f"KG{suffix}", name_ar="كجم", dimension="mass")
        category = ProductCategory.objects.get_or_create(
            code="finished-goods", defaults={"name_ar": "منتجات تامة"}
        )[0]
        self.product = Product.objects.create(
            sku=f"CUP-{suffix}", name_ar="كوب ورقي", category=category, base_uom=self.pcs
        )
        ProductUnitConversion.objects.create(product=self.product, unit=self.carton, factor=Decimal("12"))
        self.product2 = Product.objects.create(
            sku=f"TRAY-{suffix}", name_ar="طبق ورقي", category=category, base_uom=self.pcs
        )
        warehouse = Warehouse.objects.create(code=f"SAL-{suffix}", name_ar="مخزن المبيعات")
        self.location = StorageLocation.objects.create(warehouse=warehouse, code="FG", name_ar="موقع المنتجات التامة")
        self.customer = Customer.objects.create(
            code=f"CUS-{suffix}", name_ar="عميل الجملة", created_by=self.staff
        )

    def line(self, product=None, quantity="100", unit=None, unit_price="2"):
        return {
            "product": product or self.product,
            "quantity": Decimal(quantity),
            "unit": unit or self.pcs,
            "unit_price": Decimal(unit_price),
            "notes": "",
        }

    def make_order(self, lines=None, confirm=False, customer=None, **overrides):
        values = {
            "customer": customer or self.customer,
            "order_date": date(2026, 10, 1),
            "requested_delivery_date": None,
            "customer_reference": "",
            "notes": "",
        }
        values.update(overrides)
        order = save_draft_order(actor=self.staff, lines=lines or [self.line()], **values)
        if confirm:
            order = confirm_order(order=order, actor=self.staff)
        return order

    def ship(self, order, lines, reference="SHP-1", location=None):
        return create_shipment(
            order=order,
            actor=self.staff,
            reference=reference,
            shipment_date=date(2026, 10, 5),
            source_location=location or self.location,
            lines=lines,
        )

    def add_stock(self, product, quantity, unit=None, reference=None):
        return post_movement(
            reference=reference or f"OPEN-{product.sku}-{uuid.uuid4().hex[:6].upper()}",
            movement_type=StockMovement.Type.OPENING,
            product=product,
            quantity=Decimal(quantity),
            unit=unit or self.pcs,
            actor=self.staff,
            destination_location=self.location,
        )


class CustomerTests(SalesFixtureMixin, TestCase):
    def setUp(self):
        self.create_fixture()

    def test_customer_code_is_normalized_and_name_is_required(self):
        form = CustomerForm(data={"code": "  cus-9 ", "name_ar": "عميل جديد", "active": True})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["code"], "CUS-9")
        blank = CustomerForm(data={"code": "CUS-10", "name_ar": "  ", "active": True})
        self.assertFalse(blank.is_valid())
        self.assertIn("name_ar", blank.errors)

    def test_duplicate_customer_code_is_rejected_by_the_form(self):
        form = CustomerForm(data={"code": self.customer.code, "name_ar": "مكرر", "active": True})
        self.assertFalse(form.is_valid())
        self.assertIn("code", form.errors)

    def test_invalid_email_is_rejected(self):
        form = CustomerForm(data={"code": "CUS-11", "name_ar": "بريد", "email": "not-an-email", "active": True})
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_inactive_customer_cannot_be_used_for_a_new_order(self):
        inactive = Customer.objects.create(code="CUS-OFF", name_ar="عميل موقوف", active=False, created_by=self.staff)
        with self.assertRaises(ValidationError):
            save_draft_order(
                actor=self.staff,
                lines=[self.line()],
                customer=inactive,
                order_date=date(2026, 10, 1),
            )


class SalesOrderWorkflowTests(SalesFixtureMixin, TestCase):
    def setUp(self):
        self.create_fixture()

    def test_draft_order_stores_lines_with_base_quantity_and_prices(self):
        order = self.make_order()
        self.assertTrue(order.order_number.startswith("SO-"))
        self.assertEqual(order.status, SalesOrder.Status.DRAFT)
        line = order.lines.get()
        self.assertEqual(line.quantity_base, Decimal("100.000000000000"))
        self.assertEqual(line.price_per_base_unit, Decimal("2.000000"))
        self.assertEqual(line.line_total, Decimal("200.00"))
        self.assertEqual(order.ordered_quantity_base, Decimal("100.000000000000"))
        self.assertEqual(order.ordered_value, Decimal("200.00"))
        self.assertEqual(order.shipment_state, SalesOrder.ShipmentState.NOT_SHIPPED)

    def test_draft_lines_can_be_replaced_but_confirmed_lines_cannot(self):
        order = self.make_order()
        order = save_draft_order(
            actor=self.staff,
            order=order,
            lines=[self.line(quantity="50", unit_price="3")],
            customer=self.customer,
            order_date=date(2026, 10, 1),
            requested_delivery_date=None,
            customer_reference="",
            notes="",
        )
        self.assertEqual(order.lines.count(), 1)
        self.assertEqual(order.lines.get().line_total, Decimal("150.00"))

        confirm_order(order=order, actor=self.staff)
        with self.assertRaises(ValidationError):
            save_draft_order(
                actor=self.staff,
                order=order,
                lines=[self.line()],
                customer=self.customer,
                order_date=date(2026, 10, 1),
                requested_delivery_date=None,
                customer_reference="",
                notes="",
            )
        line = order.lines.get()
        line.quantity = Decimal("3")
        with self.assertRaises(ValidationError):
            line.save()

    def test_confirmation_does_not_issue_or_reserve_stock(self):
        self.add_stock(self.product, "100")
        order = self.make_order()
        confirm_order(order=order, actor=self.staff)
        self.assertEqual(balance_for(self.product, self.location), Decimal("100.000000000000"))
        self.assertEqual(
            StockMovement.objects.filter(source_reference_type="sales.shipment").count(), 0
        )

    def test_confirm_requires_lines_and_sets_audit_fields(self):
        order = self.make_order()
        order.lines.all().delete()
        with self.assertRaises(ValidationError):
            confirm_order(order=order, actor=self.staff)

        order = self.make_order()
        order = confirm_order(order=order, actor=self.staff)
        self.assertEqual(order.status, SalesOrder.Status.CONFIRMED)
        self.assertIsNotNone(order.confirmed_at)
        self.assertEqual(order.confirmed_by, self.staff)
        with self.assertRaises(ValidationError):
            confirm_order(order=order, actor=self.staff)

    def test_cancel_rules(self):
        draft = self.make_order()
        with self.assertRaises(ValidationError):
            cancel_order(order=draft, actor=self.staff, reason="  ")
        draft = cancel_order(order=draft, actor=self.staff, reason="طلب خاطئ")
        self.assertEqual(draft.status, SalesOrder.Status.CANCELLED)
        self.assertEqual(draft.cancel_reason, "طلب خاطئ")

        confirmed = self.make_order(confirm=True)
        confirmed = cancel_order(order=confirmed, actor=self.staff, reason="توقف الطلب")
        self.assertEqual(confirmed.status, SalesOrder.Status.CANCELLED)
        self.assertIsNotNone(confirmed.cancelled_at)
        with self.assertRaises(ValidationError):
            confirm_order(order=confirmed, actor=self.staff)

    def test_requested_delivery_date_cannot_precede_order_date(self):
        with self.assertRaises(ValidationError):
            save_draft_order(
                actor=self.staff,
                lines=[self.line()],
                customer=self.customer,
                order_date=date(2026, 10, 10),
                requested_delivery_date=date(2026, 10, 1),
            )

    def test_negative_line_values_are_rejected(self):
        with self.assertRaises(ValidationError):
            save_draft_order(
                actor=self.staff,
                lines=[self.line(quantity="0")],
                customer=self.customer,
                order_date=date(2026, 10, 1),
            )
        with self.assertRaises(ValidationError):
            save_draft_order(
                actor=self.staff,
                lines=[self.line(unit_price="-1")],
                customer=self.customer,
                order_date=date(2026, 10, 1),
            )

    def test_complete_requires_full_shipment(self):
        order = self.make_order(confirm=True)
        with self.assertRaises(ValidationError):
            complete_order(order=order, actor=self.staff)

        line = order.lines.get()
        self.add_stock(self.product, "1000")
        self.ship(order, [{"order_line": line, "quantity": Decimal("40"), "unit": self.pcs}])
        with self.assertRaises(ValidationError):
            complete_order(order=order, actor=self.staff)

        self.ship(order, [{"order_line": line, "quantity": Decimal("60"), "unit": self.pcs}], reference="SHP-2")
        order = complete_order(order=order, actor=self.staff)
        self.assertEqual(order.status, SalesOrder.Status.COMPLETED)
        self.assertIsNotNone(order.completed_at)
        self.assertEqual(order.completed_by, self.staff)


class ShipmentTests(SalesFixtureMixin, TestCase):
    def setUp(self):
        self.create_fixture()
        self.add_stock(self.product, "1000")
        self.order = self.make_order(confirm=True)
        self.order_line = self.order.lines.get()

    def test_shipping_requires_a_confirmed_order(self):
        draft = self.make_order()
        with self.assertRaises(ValidationError):
            self.ship(draft, [{"order_line": draft.lines.get(), "quantity": Decimal("10"), "unit": self.pcs}])
        self.assertEqual(Shipment.objects.count(), 0)

    def test_partial_then_full_shipment_updates_stock_and_state(self):
        self.ship(self.order, [{"order_line": self.order_line, "quantity": Decimal("40"), "unit": self.pcs}])
        self.assertEqual(balance_for(self.product, self.location), Decimal("960.000000000000"))
        self.assertEqual(self.order.shipped_quantity_base, Decimal("40.000000000000"))
        self.assertEqual(self.order.shipment_state, SalesOrder.ShipmentState.PARTIALLY_SHIPPED)
        self.assertEqual(self.order.outstanding_quantity_base, Decimal("60.000000000000"))

        self.ship(self.order, [{"order_line": self.order_line, "quantity": Decimal("60"), "unit": self.pcs}], reference="SHP-2")
        self.assertEqual(balance_for(self.product, self.location), Decimal("900.000000000000"))
        self.assertEqual(self.order.shipment_state, SalesOrder.ShipmentState.SHIPPED)
        self.assertEqual(self.order.outstanding_quantity_base, Decimal("0"))

    def test_shipment_uses_unit_conversion_rules(self):
        order = self.make_order(lines=[self.line(quantity="2", unit=self.carton, unit_price="24")], confirm=True)
        line = order.lines.get()
        self.assertEqual(line.quantity_base, Decimal("24.000000000000"))
        shipment = self.ship(order, [{"order_line": line, "quantity": Decimal("1"), "unit": self.carton}])
        shipment_line = shipment.lines.get()
        self.assertEqual(shipment_line.quantity_base, Decimal("12.000000000000"))
        self.assertEqual(balance_for(self.product, self.location), Decimal("988.000000000000"))

    def test_invalid_unit_is_rejected_without_side_effects(self):
        order = self.make_order(lines=[self.line(quantity="1", unit=self.carton)], confirm=True)
        line = order.lines.get()
        with self.assertRaises(ValidationError):
            self.ship(order, [{"order_line": line, "quantity": Decimal("1"), "unit": self.kg}])
        self.assertEqual(Shipment.objects.count(), 0)
        self.assertEqual(balance_for(self.product, self.location), Decimal("1000.000000000000"))

    def test_insufficient_stock_is_rejected_without_side_effects(self):
        warehouse = Warehouse.objects.create(code="EMPTY", name_ar="مخزن فارغ")
        empty_location = StorageLocation.objects.create(warehouse=warehouse, code="FG", name_ar="موقع فارغ")
        with self.assertRaises(ValidationError):
            self.ship(
                self.order,
                [{"order_line": self.order_line, "quantity": Decimal("100"), "unit": self.pcs}],
                location=empty_location,
            )
        self.assertEqual(Shipment.objects.count(), 0)
        self.assertEqual(ShipmentLine.objects.count(), 0)
        self.assertEqual(
            StockMovement.objects.filter(source_reference_type="sales.shipment").count(), 0
        )

    def test_over_shipping_is_rejected_and_leaves_no_side_effects(self):
        with self.assertRaises(ValidationError):
            self.ship(self.order, [{"order_line": self.order_line, "quantity": Decimal("101"), "unit": self.pcs}])
        self.assertEqual(Shipment.objects.count(), 0)
        self.assertEqual(balance_for(self.product, self.location), Decimal("1000.000000000000"))

    def test_duplicate_shipment_reference_is_prevented(self):
        self.ship(self.order, [{"order_line": self.order_line, "quantity": Decimal("10"), "unit": self.pcs}])
        with self.assertRaises(ValidationError):
            self.ship(
                self.order,
                [{"order_line": self.order_line, "quantity": Decimal("10"), "unit": self.pcs}],
                reference="SHP-1",
            )
        self.assertEqual(Shipment.objects.count(), 1)

    def test_duplicate_line_in_the_same_shipment_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.ship(
                self.order,
                [
                    {"order_line": self.order_line, "quantity": Decimal("10"), "unit": self.pcs},
                    {"order_line": self.order_line, "quantity": Decimal("10"), "unit": self.pcs},
                ],
            )
        self.assertEqual(Shipment.objects.count(), 0)

    def test_shipment_of_fully_shipped_order_is_rejected(self):
        self.ship(self.order, [{"order_line": self.order_line, "quantity": Decimal("100"), "unit": self.pcs}])
        with self.assertRaises(ValidationError):
            self.ship(
                self.order,
                [{"order_line": self.order_line, "quantity": Decimal("1"), "unit": self.pcs}],
                reference="SHP-EXTRA",
            )
        self.assertEqual(Shipment.objects.count(), 1)

    def test_cancel_is_blocked_after_a_shipment(self):
        self.ship(self.order, [{"order_line": self.order_line, "quantity": Decimal("10"), "unit": self.pcs}])
        with self.assertRaises(ValidationError):
            cancel_order(order=self.order, actor=self.staff, reason="إلغاء متأخر")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, SalesOrder.Status.CONFIRMED)

    def test_failed_second_line_rolls_back_the_first_movement(self):
        order = self.make_order(
            lines=[
                self.line(quantity="10", unit_price="2"),
                self.line(product=self.product2, quantity="5", unit_price="3"),
            ],
            confirm=True,
        )
        self.add_stock(self.product2, "100")
        lines = list(order.lines.order_by("sequence"))
        real_post = sales_services.post_movement
        calls = {"count": 0}

        def flaky(**kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise ValidationError("فشل متعمد في الحركة الثانية")
            return real_post(**kwargs)

        with mock.patch("sales.services.post_movement", side_effect=flaky):
            with self.assertRaises(ValidationError):
                self.ship(
                    order,
                    [
                        {"order_line": lines[0], "quantity": Decimal("10"), "unit": self.pcs},
                        {"order_line": lines[1], "quantity": Decimal("5"), "unit": self.pcs},
                    ],
                )
        self.assertEqual(Shipment.objects.count(), 0)
        self.assertEqual(ShipmentLine.objects.count(), 0)
        self.assertEqual(balance_for(self.product, self.location), Decimal("1000.000000000000"))
        self.assertEqual(
            StockMovement.objects.filter(source_reference_type="sales.shipment").count(), 0
        )

    def test_inventory_reversal_reduces_net_shipped_quantity(self):
        shipment = self.ship(self.order, [{"order_line": self.order_line, "quantity": Decimal("40"), "unit": self.pcs}])
        shipment_line = shipment.lines.get()
        reverse_movement(movement=shipment_line.movement, reference="SHP-1-REV", actor=self.staff, reason="تصحيح")
        self.assertEqual(balance_for(self.product, self.location), Decimal("1000.000000000000"))
        self.assertEqual(self.order.shipped_quantity_base, Decimal("0"))
        self.assertEqual(self.order.shipment_state, SalesOrder.ShipmentState.NOT_SHIPPED)
        self.assertEqual(self.order.outstanding_quantity_base, Decimal("100.000000000000"))

    def test_shipment_documents_and_lines_are_immutable(self):
        shipment = self.ship(self.order, [{"order_line": self.order_line, "quantity": Decimal("10"), "unit": self.pcs}])
        shipment.notes = "تعديل"
        with self.assertRaises(ValidationError):
            shipment.save()
        shipment_line = shipment.lines.get()
        shipment_line.quantity = Decimal("20")
        with self.assertRaises(ValidationError):
            shipment_line.save()
        with self.assertRaises(ValidationError):
            shipment_line.delete()
        with self.assertRaises(ValidationError):
            shipment.delete()

    def test_shipment_posts_issue_movements_with_sales_source_reference(self):
        shipment = self.ship(self.order, [{"order_line": self.order_line, "quantity": Decimal("10"), "unit": self.pcs}])
        movement = shipment.lines.get().movement
        self.assertEqual(movement.movement_type, StockMovement.Type.ISSUE)
        self.assertEqual(movement.source_location, self.location)
        self.assertIsNone(movement.destination_location)
        self.assertEqual(movement.source_reference_type, "sales.shipment")
        self.assertEqual(movement.source_reference_id, f"{shipment.id.hex}:{self.order_line.pk}")


class SalesPricingBoundaryTests(SalesFixtureMixin, TestCase):
    def setUp(self):
        self.create_fixture()

    def test_sales_price_is_recorded_but_does_not_change_costing_valuation(self):
        RawMaterialPrice.objects.create(
            material=self.product,
            price_per_unit=Decimal("3.0000"),
            unit=self.pcs,
            effective_date=date(2026, 9, 1),
            created_by=self.staff,
        )
        self.assertEqual(get_material_price(self.product, date(2026, 10, 15)), Decimal("3.000000"))

        self.add_stock(self.product, "500")
        order = self.make_order(confirm=True)
        line = order.lines.get()
        shipment = self.ship(order, [{"order_line": line, "quantity": Decimal("100"), "unit": self.pcs}])
        shipment_line = shipment.lines.get()

        self.assertEqual(shipment_line.unit_price, Decimal("2.0000"))
        self.assertEqual(shipment_line.price_per_base_unit, Decimal("2.000000"))
        self.assertEqual(shipment_line.line_total, Decimal("200.00"))
        self.assertEqual(get_material_price(self.product, date(2026, 10, 15)), Decimal("3.000000"))
        self.assertEqual(RawMaterialPrice.objects.count(), 1)
        self.assertEqual(MATERIAL_VALUATION_METHOD, "LAST_PURCHASE_COST")


class ShipmentValuationTests(SalesFixtureMixin, TestCase):
    def setUp(self):
        self.create_fixture()
        self.add_stock(self.product, "1000")

    def test_carton_order_shipped_in_pieces_uses_base_price_for_value(self):
        order = self.make_order(
            lines=[self.line(quantity="2", unit=self.carton, unit_price="24")],
            confirm=True,
        )
        line = order.lines.get()
        shipment = self.ship(
            order,
            [{"order_line": line, "quantity": Decimal("12"), "unit": self.pcs}],
        )
        shipment_line = shipment.lines.get()
        self.assertEqual(shipment_line.quantity_base, Decimal("12.000000000000"))
        self.assertEqual(shipment_line.quantity_base, shipment_line.movement.base_quantity)
        self.assertEqual(shipment_line.unit_price, Decimal("24.0000"))
        self.assertEqual(shipment_line.price_per_base_unit, Decimal("2.000000"))
        self.assertEqual(
            shipment_line.line_total,
            _rounded_money(shipment_line.movement.base_quantity * line.price_per_base_unit),
        )
        self.assertEqual(shipment_line.line_total, Decimal("24.00"))
        self.assertEqual(shipment.total_value, Decimal("24.00"))
        self.assertEqual(shipment.net_value, Decimal("24.00"))
        self.assertEqual(order.shipped_value, Decimal("24.00"))
        self.assertEqual(RawMaterialPrice.objects.count(), 0)
        self.assertEqual(MATERIAL_VALUATION_METHOD, "LAST_PURCHASE_COST")

    def test_piece_order_shipped_in_cartons_uses_base_price_for_value(self):
        order = self.make_order(
            lines=[self.line(quantity="24", unit=self.pcs, unit_price="2")],
            confirm=True,
        )
        line = order.lines.get()
        shipment = self.ship(
            order,
            [{"order_line": line, "quantity": Decimal("1"), "unit": self.carton}],
        )
        shipment_line = shipment.lines.get()
        self.assertEqual(shipment_line.quantity_base, Decimal("12.000000000000"))
        self.assertEqual(shipment_line.quantity_base, shipment_line.movement.base_quantity)
        self.assertEqual(shipment_line.unit_price, Decimal("2.0000"))
        self.assertEqual(shipment_line.price_per_base_unit, Decimal("2.000000"))
        self.assertEqual(
            shipment_line.line_total,
            _rounded_money(shipment_line.movement.base_quantity * line.price_per_base_unit),
        )
        self.assertEqual(shipment_line.line_total, Decimal("24.00"))
        self.assertEqual(shipment.total_value, Decimal("24.00"))
        self.assertEqual(order.shipped_value, Decimal("24.00"))

    def test_shipment_detail_labels_the_order_line_unit_price(self):
        order = self.make_order(
            lines=[self.line(quantity="2", unit=self.carton, unit_price="24")],
            confirm=True,
        )
        line = order.lines.get()
        shipment = self.ship(
            order,
            [{"order_line": line, "quantity": Decimal("12"), "unit": self.pcs}],
        )
        self.client.force_login(self.staff)
        response = self.client.get(reverse("sales:shipment_detail", args=[shipment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "سعر وحدة أمر البيع")
        self.assertContains(response, "ج.م /")
        self.assertContains(response, line.unit.code)


class CompletedOrderReversalTests(SalesFixtureMixin, TestCase):
    def setUp(self):
        self.create_fixture()
        self.add_stock(self.product, "1000")
        self.order = self.make_order(confirm=True)
        self.line = self.order.lines.get()

    def _complete_with_shipments(self, quantities):
        shipments = []
        for index, quantity in enumerate(quantities, start=1):
            shipments.append(
                self.ship(
                    self.order,
                    [{"order_line": self.line, "quantity": Decimal(quantity), "unit": self.pcs}],
                    reference=f"REOPEN-SHP-{index}",
                )
            )
        self.order = complete_order(order=self.order, actor=self.staff)
        return shipments

    def _completed_and_reversed(self):
        shipment = self._complete_with_shipments(("100",))[0]
        reverse_movement(
            movement=shipment.lines.get().movement,
            reference="REOPEN-SHP-1-REV",
            actor=self.staff,
            reason="تصحيح شحن",
        )
        self.order.refresh_from_db()
        return shipment

    def test_completed_order_reopens_after_full_reversal_and_can_be_reshiped(self):
        shipment = self._completed_and_reversed()
        self.assertEqual(self.order.status, SalesOrder.Status.COMPLETED)
        self.assertEqual(self.order.shipment_state, SalesOrder.ShipmentState.NOT_SHIPPED)
        self.assertEqual(self.order.outstanding_quantity_base, Decimal("100.000000000000"))
        self.assertTrue(self.order.can_reopen)
        self.assertEqual(shipment.total_value, Decimal("200.00"))
        self.assertEqual(shipment.net_value, Decimal("0.00"))
        self.assertTrue(shipment.is_fully_reversed)
        self.assertTrue(shipment.has_reversed_lines)

        reopened = reopen_completed_order(
            order=self.order,
            actor=self.staff,
            reason="إعادة شحن بعد عكس الشحنة",
        )
        self.assertEqual(reopened.status, SalesOrder.Status.CONFIRMED)
        self.assertIsNotNone(reopened.reopened_at)
        self.assertEqual(reopened.reopened_by, self.staff)
        self.assertEqual(reopened.reopen_reason, "إعادة شحن بعد عكس الشحنة")
        self.assertEqual(reopened.shipment_state, SalesOrder.ShipmentState.NOT_SHIPPED)
        self.assertEqual(reopened.outstanding_quantity_base, Decimal("100.000000000000"))

        self.ship(
            reopened,
            [{"order_line": self.line, "quantity": Decimal("100"), "unit": self.pcs}],
            reference="REOPEN-SHP-2",
        )
        reopened.refresh_from_db()
        self.assertEqual(reopened.shipped_quantity_base, Decimal("100.000000000000"))
        self.assertEqual(reopened.shipment_state, SalesOrder.ShipmentState.SHIPPED)
        reopened = complete_order(order=reopened, actor=self.staff)
        self.assertEqual(reopened.status, SalesOrder.Status.COMPLETED)
        self.assertEqual(reopened.shipped_value, Decimal("200.00"))

    def test_completed_order_with_partial_reversal_reopens_and_reships_only_outstanding(self):
        shipments = self._complete_with_shipments(("40", "60"))
        reverse_movement(
            movement=shipments[1].lines.get().movement,
            reference="REOPEN-PARTIAL-REV",
            actor=self.staff,
            reason="تصحيح الجزء الثاني",
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, SalesOrder.Status.COMPLETED)
        self.assertEqual(self.order.shipment_state, SalesOrder.ShipmentState.PARTIALLY_SHIPPED)
        self.assertEqual(self.order.outstanding_quantity_base, Decimal("60.000000000000"))

        reopened = reopen_completed_order(order=self.order, actor=self.staff, reason="إعادة شحن المتبقي")
        self.assertEqual(reopened.status, SalesOrder.Status.CONFIRMED)
        self.assertEqual(reopened.shipped_quantity_base, Decimal("40.000000000000"))
        self.assertEqual(reopened.outstanding_quantity_base, Decimal("60.000000000000"))

        self.ship(
            reopened,
            [{"order_line": self.line, "quantity": Decimal("60"), "unit": self.pcs}],
            reference="REOPEN-PARTIAL-SHP-3",
        )
        reopened = complete_order(order=reopened, actor=self.staff)
        self.assertEqual(reopened.status, SalesOrder.Status.COMPLETED)
        self.assertEqual(reopened.shipped_quantity_base, Decimal("100.000000000000"))

    def test_reopen_rejects_an_order_without_outstanding_quantity(self):
        self._complete_with_shipments(("100",))
        with self.assertRaisesMessage(ValidationError, "لا توجد كمية معلقة"):
            reopen_completed_order(order=self.order, actor=self.staff, reason="لا شيء معلق")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, SalesOrder.Status.COMPLETED)

    def test_reopen_rejects_non_completed_orders_and_requires_a_reason(self):
        with self.assertRaisesMessage(ValidationError, "يمكن إعادة فتح أمر بيع مكتمل فقط"):
            reopen_completed_order(order=self.order, actor=self.staff, reason="غير مكتمل")
        self.assertEqual(self.order.status, SalesOrder.Status.CONFIRMED)

        self._completed_and_reversed()
        with self.assertRaisesMessage(ValidationError, "سبب إعادة فتح أمر البيع مطلوب"):
            reopen_completed_order(order=self.order, actor=self.staff, reason="  ")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, SalesOrder.Status.COMPLETED)

    def test_reopen_rejects_an_overlong_reason_without_changing_audit_fields(self):
        self._completed_and_reversed()
        original = SalesOrder.objects.get(pk=self.order.pk)
        with self.assertRaisesMessage(
            ValidationError, "سبب إعادة فتح أمر البيع يجب ألا يتجاوز 240 حرفًا"
        ):
            reopen_completed_order(order=self.order, actor=self.staff, reason="س" * 241)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, original.status)
        self.assertEqual(self.order.completed_at, original.completed_at)
        self.assertEqual(self.order.completed_by_id, original.completed_by_id)
        self.assertEqual(self.order.reopened_at, original.reopened_at)
        self.assertEqual(self.order.reopened_by_id, original.reopened_by_id)
        self.assertEqual(self.order.reopen_reason, original.reopen_reason)

    def test_reopen_is_staff_only(self):
        self._completed_and_reversed()
        with self.assertRaises(ValidationError):
            reopen_completed_order(order=self.order, actor=self.regular, reason="محاولة غير مصرح بها")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, SalesOrder.Status.COMPLETED)

    def test_reopen_view_is_post_only_staff_only_and_csrf_protected(self):
        self._completed_and_reversed()
        url = reverse("sales:order_reopen", args=[self.order.pk])

        self.client.force_login(self.staff)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)

        self.client.force_login(self.regular)
        response = self.client.post(url, {"reason": "محاولة غير مصرح بها"})
        self.assertEqual(response.status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, SalesOrder.Status.COMPLETED)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        response = csrf_client.post(url, {"reason": "بدون رمز"})
        self.assertEqual(response.status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, SalesOrder.Status.COMPLETED)

        self.client.force_login(self.staff)
        response = self.client.post(url, {"reason": "إعادة فتح من الواجهة"})
        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, SalesOrder.Status.CONFIRMED)
        self.assertEqual(self.order.reopen_reason, "إعادة فتح من الواجهة")

    def test_order_detail_marks_reversed_shipment_and_separates_document_value(self):
        shipment = self._completed_and_reversed()
        self.client.force_login(self.staff)
        response = self.client.get(reverse("sales:order_detail", args=[self.order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "معكوس بالكامل")
        self.assertContains(response, "قيمة المستند")
        self.assertContains(response, "صافي غير المعكوس:")
        self.assertEqual(shipment.total_value, Decimal("200.00"))
        self.assertEqual(shipment.net_value, Decimal("0.00"))


class SalesViewAndPermissionTests(SalesFixtureMixin, TestCase):
    def setUp(self):
        self.create_fixture()

    def _routes(self):
        return [
            ("sales:order_list", {}),
            ("sales:order_add", {}),
            ("sales:customer_list", {}),
            ("sales:customer_add", {}),
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

    def test_staff_users_can_view_sales_pages(self):
        self.client.force_login(self.staff)
        for name, kwargs in self._routes():
            response = self.client.get(reverse(name, kwargs=kwargs))
            self.assertEqual(response.status_code, 200, name)

    def test_regular_users_cannot_post_state_changes(self):
        order = self.make_order()
        self.client.force_login(self.regular)
        response = self.client.post(reverse("sales:order_confirm", args=[order.pk]))
        self.assertEqual(response.status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.DRAFT)

    def test_state_change_requires_csrf_token(self):
        order = self.make_order()
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        response = csrf_client.post(reverse("sales:order_confirm", args=[order.pk]))
        self.assertEqual(response.status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.DRAFT)

    def test_staff_can_create_and_confirm_an_order_through_the_ui(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("sales:order_add"),
            {
                "customer": self.customer.pk,
                "order_date": "2026-10-01",
                "requested_delivery_date": "",
                "customer_reference": "CUS-REF-1",
                "notes": "",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-product": self.product.pk,
                "lines-0-quantity": "100",
                "lines-0-unit": self.pcs.pk,
                "lines-0-unit_price": "2",
                "lines-0-notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        order = SalesOrder.objects.get()
        self.assertEqual(order.lines.count(), 1)
        self.assertEqual(order.lines.get().quantity_base, Decimal("100.000000000000"))

    def test_shipment_form_renders_dynamic_quantity_and_unit_fields(self):
        self.add_stock(self.product, "500")
        order = self.make_order(confirm=True)
        line = order.lines.get()
        self.client.force_login(self.staff)
        response = self.client.get(reverse("sales:shipment_add", args=[order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'name="quantity_{line.pk}"')
        self.assertContains(response, f'name="unit_{line.pk}"')

    def test_staff_can_ship_through_the_ui(self):
        self.add_stock(self.product, "500")
        order = self.make_order(confirm=True)
        line = order.lines.get()
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("sales:shipment_add", args=[order.pk]),
            {
                "reference": "SHP-UI-1",
                "customer_reference": "",
                "shipment_date": "2026-10-05",
                "source_location": self.location.pk,
                "notes": "",
                f"quantity_{line.pk}": "40",
                f"unit_{line.pk}": self.pcs.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(balance_for(self.product, self.location), Decimal("460.000000000000"))

    def test_staff_can_render_order_customer_and_shipment_detail_pages(self):
        self.add_stock(self.product, "500")
        order = self.make_order(confirm=True)
        line = order.lines.get()
        shipment = self.ship(
            order, [{"order_line": line, "quantity": Decimal("10"), "unit": self.pcs}]
        )
        self.client.force_login(self.staff)
        for url in (
            reverse("sales:order_detail", args=[order.pk]),
            reverse("sales:customer_detail", args=[self.customer.pk]),
            reverse("sales:shipment_detail", args=[shipment.pk]),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)

        draft = self.make_order()
        response = self.client.get(reverse("sales:order_edit", args=[draft.pk]))
        self.assertEqual(response.status_code, 200)

    def test_customer_submitted_pending_pricing_is_visible_and_confirm_is_hidden(self):
        order = save_draft_order(
            actor=self.staff,
            customer=self.customer,
            order_date=date(2026, 10, 1),
            customer_submitted=True,
            lines=[self.line(unit_price="0")],
        )
        self.assertEqual(order.pricing_status, "PENDING_REVIEW")
        self.client.force_login(self.staff)

        listing = self.client.get(reverse("sales:order_list"))
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, "طلب عميل")
        self.assertContains(listing, "بانتظار التسعير")

        detail = self.client.get(reverse("sales:order_detail", args=[order.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "طلب عميل")
        self.assertContains(detail, "بانتظار التسعير")
        self.assertNotContains(
            detail,
            f'action="{reverse("sales:order_confirm", args=[order.pk])}"',
        )

        # Staff can set valid prices in the existing edit flow; the guard then
        # permits confirmation and the visible pending state disappears.
        priced = save_draft_order(
            actor=self.staff,
            order=order,
            lines=[self.line(unit_price="2")],
        )
        self.assertEqual(priced.pricing_status, "RECORDED")
        confirmed = confirm_order(order=priced, actor=self.staff)
        self.assertEqual(confirmed.status, SalesOrder.Status.CONFIRMED)

    def test_admin_shows_customer_submission_and_pricing_state(self):
        order = save_draft_order(
            actor=self.staff,
            customer=self.customer,
            order_date=date(2026, 10, 1),
            customer_submitted=True,
            lines=[self.line(unit_price="0")],
        )
        admin_user = get_user_model().objects.create_superuser(
            username=f"sales-admin-{uuid.uuid4().hex[:8]}",
            email="admin@example.invalid",
            password="test-password",
        )
        self.client.force_login(admin_user)

        listing = self.client.get(reverse("admin:sales_salesorder_changelist"))
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, order.order_number)
        self.assertContains(listing, "PENDING_REVIEW")

        detail = self.client.get(
            reverse("admin:sales_salesorder_change", args=[order.pk])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "حالة التسعير")
        self.assertContains(detail, "PENDING_REVIEW")

    def test_navigation_link_is_hidden_from_regular_users(self):
        url = reverse("sales:order_list")
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("core:home")), url)
        self.client.force_login(self.regular)
        self.assertNotContains(self.client.get(reverse("core:home")), url)


class SalesConcurrencyTests(SalesFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.create_fixture()
        self.add_stock(self.product, "1000")
        self.order = self.make_order(confirm=True)
        self.order_line = self.order.lines.get()

    @skipUnlessDBFeature("has_select_for_update")
    def test_parallel_duplicate_shipment_reference_posts_once(self):
        barrier = Barrier(2)

        def attempt():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.staff.pk)
                order = SalesOrder.objects.get(pk=self.order.pk)
                line = order.lines.get()
                barrier.wait(timeout=10)
                try:
                    create_shipment(
                        order=order,
                        actor=actor,
                        reference="RACE-DUP",
                        shipment_date=date(2026, 10, 5),
                        source_location=self.location,
                        lines=[{"order_line": line, "quantity": Decimal("10"), "unit": self.pcs}],
                    )
                    return "posted"
                except ValidationError:
                    return "rejected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(results, ["posted", "rejected"])
        self.assertEqual(Shipment.objects.filter(reference="RACE-DUP").count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_parallel_shipments_cannot_oversell_one_order(self):
        barrier = Barrier(2)

        def attempt(reference):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.staff.pk)
                order = SalesOrder.objects.get(pk=self.order.pk)
                line = order.lines.get()
                barrier.wait(timeout=10)
                try:
                    create_shipment(
                        order=order,
                        actor=actor,
                        reference=reference,
                        shipment_date=date(2026, 10, 5),
                        source_location=self.location,
                        lines=[{"order_line": line, "quantity": Decimal("60"), "unit": self.pcs}],
                    )
                    return "posted"
                except ValidationError:
                    return "rejected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ("RACE-A", "RACE-B")))
        self.assertCountEqual(results, ["posted", "rejected"])
        self.assertEqual(Shipment.objects.count(), 1)
        self.assertEqual(self.order.shipped_quantity_base, Decimal("60.000000000000"))
        self.assertEqual(balance_for(self.product, self.location), Decimal("940.000000000000"))

    @skipUnlessDBFeature("has_select_for_update")
    def test_parallel_shipments_on_different_orders_cannot_oversell_shared_stock(self):
        warehouse = Warehouse.objects.create(code="SHARED", name_ar="مخزن مشترك")
        shared = StorageLocation.objects.create(warehouse=warehouse, code="FG", name_ar="موقع مشترك")
        post_movement(
            reference="OPEN-SHARED",
            movement_type=StockMovement.Type.OPENING,
            product=self.product2,
            quantity=Decimal("10"),
            unit=self.pcs,
            actor=self.staff,
            destination_location=shared,
        )
        order_a = self.make_order(lines=[self.line(product=self.product2, quantity="10")], confirm=True)
        order_b = self.make_order(lines=[self.line(product=self.product2, quantity="10")], confirm=True)
        line_a = order_a.lines.get()
        line_b = order_b.lines.get()
        barrier = Barrier(2)

        def attempt(order_pk, line_pk, reference):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.staff.pk)
                order = SalesOrder.objects.get(pk=order_pk)
                line = SalesOrderLine.objects.get(pk=line_pk)
                barrier.wait(timeout=10)
                try:
                    create_shipment(
                        order=order,
                        actor=actor,
                        reference=reference,
                        shipment_date=date(2026, 10, 5),
                        source_location=shared,
                        lines=[{"order_line": line, "quantity": Decimal("10"), "unit": self.pcs}],
                    )
                    return "posted"
                except ValidationError:
                    return "rejected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda args: attempt(*args),
                ((order_a.pk, line_a.pk, "RACE-S1"), (order_b.pk, line_b.pk, "RACE-S2")),
            ))
        self.assertCountEqual(results, ["posted", "rejected"])
        self.assertEqual(balance_for(self.product2, shared), Decimal("0"))
