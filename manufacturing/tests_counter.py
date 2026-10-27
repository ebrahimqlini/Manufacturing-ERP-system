"""Manual machine-counter reading workflow tests (Prompt 11).

Covers the full manual lifecycle — start, close, approve, audited correction —
and proves the two invariants the feature promises: recorded readings are
immutable outside the correction service, and a counter reading never posts
stock movements or changes the authoritative production record.
"""

from datetime import timedelta
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product, ProductCategory, ProductUnitConversion, UnitOfMeasure
from inventory.models import StockLedgerEntry, StockMovement, StorageLocation, Warehouse
from inventory.services import post_movement

from .counter_services import (
    approve_counter_reading,
    close_counter_interval,
    correct_counter_reading,
    start_counter_interval,
)
from .models import (
    BillOfMaterials,
    BOMComponent,
    MachineCounterReading,
    ProductionLine,
    ProductionOperation,
    ProductionOrder,
    Routing,
    RoutingStep,
)
from .services import issue_material, release_order, save_draft_order


class CounterFixtureMixin:
    def create_counter_fixture(self):
        User = get_user_model()
        from django.contrib.auth.models import Permission

        suffix = uuid.uuid4().hex[:8].upper()
        self.staff = User.objects.create_user(
            username=f"counter-staff-{suffix}", password="test-password", is_staff=True
        )
        perms = Permission.objects.filter(
            content_type__app_label__in=[
                "manufacturing", "inventory", "costing", "purchasing", "sales"
            ]
        )
        self.staff.user_permissions.set(perms)
        self.reader = User.objects.create_user(
            username=f"counter-reader-{suffix}", password="test-password"
        )
        self.pcs = UnitOfMeasure.objects.create(code=f"P{suffix}", name_ar="قطعة", dimension="count")
        self.kg = UnitOfMeasure.objects.create(code=f"K{suffix}", name_ar="كجم", dimension="mass")
        self.dozen = UnitOfMeasure.objects.create(code=f"D{suffix}", name_ar="دستة", dimension="count")
        self.finished_category = ProductCategory.objects.get_or_create(
            code="finished-goods", defaults={"name_ar": "منتج تام"}
        )[0]
        self.raw_category = ProductCategory.objects.get_or_create(
            code="raw-material", defaults={"name_ar": "مادة خام"}
        )[0]
        self.product = Product.objects.create(
            sku=f"CNT-{suffix}", name_ar="منتج العدّاد", category=self.finished_category, base_uom=self.pcs
        )
        # Dozen → base-unit conversion so unit conversion is exercised exactly.
        ProductUnitConversion.objects.create(
            product=self.product, unit=self.dozen, factor=Decimal("12"), active=True
        )
        self.material = Product.objects.create(
            sku=f"CMAT-{suffix}", name_ar="مادة العدّاد", category=self.raw_category, base_uom=self.kg
        )
        self.line = ProductionLine.objects.create(code=f"CLINE-{suffix}", name_ar="خط العدّاد")
        self.press = ProductionOperation.objects.create(code=f"CPR-{suffix}", name_ar="كبس")
        warehouse = Warehouse.objects.create(code=f"CW-{suffix}", name_ar="مخزن العدّاد")
        self.location = StorageLocation.objects.create(warehouse=warehouse, code="RAW", name_ar="موقع المواد")
        self.bom = BillOfMaterials.objects.create(
            product=self.product, version=1, output_quantity=Decimal("100"), output_unit=self.pcs,
            created_by=self.staff,
        )
        BOMComponent.objects.create(
            bom=self.bom, material=self.material, quantity=Decimal("12"), unit=self.kg, sequence=10
        )
        self.routing = Routing.objects.create(
            product=self.product, production_line=self.line, version=1, created_by=self.staff
        )
        RoutingStep.objects.create(routing=self.routing, operation=self.press, sequence=1)
        self._order_seq = 0

    def released_order(self, *, line=None, quantity="1000"):
        self._order_seq += 1
        order = save_draft_order(
            actor=self.staff,
            product=self.product,
            production_line=line or self.line,
            bom=self.bom,
            routing=self.routing,
            source_location=self.location,
            planned_quantity=Decimal(quantity),
            planned_unit=self.pcs,
            notes="",
        )
        release_order(order=order, actor=self.staff)
        order.refresh_from_db()
        return order


class CounterStartCloseTests(CounterFixtureMixin, TestCase):
    def setUp(self):
        self.create_counter_fixture()
        self.order = self.released_order()

    def test_start_and_close_computes_output_in_order_units(self):
        reading = start_counter_interval(
            actor=self.staff,
            order=self.order,
            production_line=self.line,
            unit=self.pcs,
            start_reading=Decimal("1000"),
        )
        self.assertEqual(reading.status, MachineCounterReading.Status.PROVISIONAL)
        self.assertIsNone(reading.counter_output)
        closed = close_counter_interval(
            reading=reading,
            actor=self.staff,
            end_reading=Decimal("2500"),
            rejected_quantity=Decimal("50"),
        )
        self.assertEqual(closed.status, MachineCounterReading.Status.COMPLETE)
        self.assertEqual(closed.counter_output, Decimal("1500"))
        # Base-unit conversion: product base unit is pcs, so base == entered.
        self.assertEqual(closed.counter_output_base, Decimal("1500"))
        self.assertEqual(closed.rejected_quantity_base, Decimal("50"))
        self.assertEqual(closed.accepted_good_base, Decimal("1450"))
        self.assertIsNotNone(closed.closed_at)
        self.assertEqual(closed.closed_by, self.staff)

    def test_start_rejected_on_non_active_order(self):
        self.order.status = ProductionOrder.Status.COMPLETED
        self.order._allow_status_transition = True
        self.order.save()
        with self.assertRaises(ValidationError):
            start_counter_interval(
                actor=self.staff,
                order=self.order,
                production_line=self.line,
                unit=self.pcs,
                start_reading=Decimal("0"),
            )

    def test_start_rejected_when_line_is_not_the_orders_line(self):
        other_line = ProductionLine.objects.create(code="CLINE-OTHER", name_ar="خط آخر")
        with self.assertRaises(ValidationError):
            start_counter_interval(
                actor=self.staff,
                order=self.order,
                production_line=other_line,
                unit=self.pcs,
                start_reading=Decimal("0"),
            )

    def test_one_open_interval_per_line_and_order(self):
        start_counter_interval(
            actor=self.staff,
            order=self.order,
            production_line=self.line,
            unit=self.pcs,
            start_reading=Decimal("0"),
        )
        second_order = self.released_order()
        # Same line, different order: still rejected while the first is open.
        with self.assertRaises(ValidationError):
            start_counter_interval(
                actor=self.staff,
                order=second_order,
                production_line=self.line,
                unit=self.pcs,
                start_reading=Decimal("0"),
            )

    def test_overlapping_time_rejected_after_previous_closed(self):
        first = start_counter_interval(
            actor=self.staff,
            order=self.order,
            production_line=self.line,
            unit=self.pcs,
            start_reading=Decimal("0"),
            started_at=timezone.now() - timedelta(hours=4),
        )
        close_counter_interval(
            reading=first, actor=self.staff, end_reading=Decimal("100"),
            ended_at=timezone.now() - timedelta(hours=2),
        )
        second_order = self.released_order()
        # Starting before the previous interval ended is a real time overlap.
        with self.assertRaises(ValidationError):
            start_counter_interval(
                actor=self.staff,
                order=second_order,
                production_line=self.line,
                unit=self.pcs,
                start_reading=Decimal("100"),
                started_at=timezone.now() - timedelta(hours=3),
            )

    def test_backwards_counter_requires_documented_reset(self):
        first = start_counter_interval(
            actor=self.staff, order=self.order, production_line=self.line,
            unit=self.pcs, start_reading=Decimal("500"),
        )
        close_counter_interval(reading=first, actor=self.staff, end_reading=Decimal("900"))
        second_order = self.released_order()
        with self.assertRaises(ValidationError):
            start_counter_interval(
                actor=self.staff, order=second_order, production_line=self.line,
                unit=self.pcs, start_reading=Decimal("100"),
            )
        # With a continuation reference and reset reason it is accepted.
        continued = start_counter_interval(
            actor=self.staff, order=second_order, production_line=self.line,
            unit=self.pcs, start_reading=Decimal("100"),
            continuation_of=first, reset_reason="استبدال عجلة العدّاد",
        )
        self.assertEqual(continued.continuation_of, first)
        self.assertIn("استبدال", continued.reset_reason)

    def test_incompatible_unit_rejected(self):
        with self.assertRaises(ValidationError):
            start_counter_interval(
                actor=self.staff, order=self.order, production_line=self.line,
                unit=self.kg, start_reading=Decimal("0"),
            )

    def test_convertible_unit_accepted_and_converted_exactly(self):
        reading = start_counter_interval(
            actor=self.staff, order=self.order, production_line=self.line,
            unit=self.dozen, start_reading=Decimal("10"),
        )
        closed = close_counter_interval(
            reading=reading, actor=self.staff, end_reading=Decimal("20")
        )
        # 10 dozen = 120 pcs in base units.
        self.assertEqual(closed.counter_output_base, Decimal("120"))

    def test_zero_output_interval_converts_to_zero(self):
        reading = start_counter_interval(
            actor=self.staff, order=self.order, production_line=self.line,
            unit=self.pcs, start_reading=Decimal("400"),
        )
        closed = close_counter_interval(reading=reading, actor=self.staff, end_reading=Decimal("400"))
        self.assertEqual(closed.counter_output, Decimal("0"))
        self.assertEqual(closed.counter_output_base, Decimal("0"))

    def test_negative_start_rejected(self):
        with self.assertRaises(ValidationError):
            start_counter_interval(
                actor=self.staff, order=self.order, production_line=self.line,
                unit=self.pcs, start_reading=Decimal("-1"),
            )

    def test_material_issue_must_belong_to_the_order(self):
        requirement = self.order.requirements.get()
        self.receive_stock()
        issue = issue_material(
            order=self.order, requirement=requirement, quantity=Decimal("10"),
            unit=self.kg, reference=f"ISS-{uuid.uuid4().hex[:8]}", actor=self.staff,
        )
        other_order = self.released_order()
        with self.assertRaises(ValidationError):
            start_counter_interval(
                actor=self.staff, order=other_order, production_line=self.line,
                unit=self.pcs, start_reading=Decimal("0"), material_issue=issue,
            )

    def receive_stock(self):
        return post_movement(
            reference=f"CNT-RECEIPT-{uuid.uuid4().hex[:8]}",
            movement_type=StockMovement.Type.RECEIPT,
            product=self.material,
            quantity=Decimal("500"),
            unit=self.kg,
            actor=self.staff,
            destination_location=self.location,
        )

    def test_non_staff_and_permissionless_users_rejected(self):
        for user in (self.reader,):
            with self.assertRaises(ValidationError):
                start_counter_interval(
                    actor=user, order=self.order, production_line=self.line,
                    unit=self.pcs, start_reading=Decimal("0"),
                )

    def test_counter_flow_never_touches_stock_or_production_records(self):
        movements_before = StockMovement.objects.count()
        ledger_before = StockLedgerEntry.objects.count()
        reading = start_counter_interval(
            actor=self.staff, order=self.order, production_line=self.line,
            unit=self.pcs, start_reading=Decimal("0"),
        )
        close_counter_interval(reading=reading, actor=self.staff, end_reading=Decimal("100"))
        approve_counter_reading(reading=reading, actor=self.staff)
        self.assertEqual(StockMovement.objects.count(), movements_before)
        self.assertEqual(StockLedgerEntry.objects.count(), ledger_before)
        # The authoritative production record is untouched (no results created).
        self.assertEqual(self.order.results.count(), 0)
        self.assertEqual(self.order.actual_good_base, 0)


class CounterCloseValidationTests(CounterFixtureMixin, TestCase):
    def setUp(self):
        self.create_counter_fixture()
        self.order = self.released_order()
        self.reading = start_counter_interval(
            actor=self.staff, order=self.order, production_line=self.line,
            unit=self.pcs, start_reading=Decimal("1000"),
        )

    def test_end_below_start_rejected(self):
        with self.assertRaises(ValidationError):
            close_counter_interval(reading=self.reading, actor=self.staff, end_reading=Decimal("999"))

    def test_rejected_exceeding_output_rejected(self):
        # Prompt mandate: rejected can never exceed counter output, so the
        # accepted counter-good amount can never go negative.
        with self.assertRaises(ValidationError):
            close_counter_interval(
                reading=self.reading, actor=self.staff,
                end_reading=Decimal("1100"), rejected_quantity=Decimal("200"),
            )

    def test_negative_rejected_rejected(self):
        with self.assertRaises(ValidationError):
            close_counter_interval(
                reading=self.reading, actor=self.staff,
                end_reading=Decimal("1100"), rejected_quantity=Decimal("-5"),
            )

    def test_close_twice_rejected(self):
        close_counter_interval(reading=self.reading, actor=self.staff, end_reading=Decimal("1200"))
        with self.assertRaises(ValidationError):
            close_counter_interval(reading=self.reading, actor=self.staff, end_reading=Decimal("1300"))


class CounterApproveCorrectionTests(CounterFixtureMixin, TestCase):
    def setUp(self):
        self.create_counter_fixture()
        self.order = self.released_order()
        self.reading = start_counter_interval(
            actor=self.staff, order=self.order, production_line=self.line,
            unit=self.pcs, start_reading=Decimal("0"),
        )
        close_counter_interval(
            reading=self.reading, actor=self.staff,
            end_reading=Decimal("100"), rejected_quantity=Decimal("10"),
        )

    def test_approve_requires_closed_reading(self):
        open_reading = start_counter_interval(
            actor=self.staff, order=self.order, production_line=self.line,
            unit=self.pcs, start_reading=Decimal("500"),
        )
        # NOTE: the previous interval is closed, so a new one may open.
        with self.assertRaises(ValidationError):
            approve_counter_reading(reading=open_reading, actor=self.staff)

    def test_approve_complete_reading(self):
        approved = approve_counter_reading(reading=self.reading, actor=self.staff)
        self.assertEqual(approved.status, MachineCounterReading.Status.APPROVED)
        self.assertEqual(approved.approved_by, self.staff)
        self.assertIsNotNone(approved.approved_at)

    def test_correction_requires_reason(self):
        with self.assertRaises(ValidationError):
            correct_counter_reading(
                reading=self.reading, actor=self.staff, reason="   ",
                end_reading=Decimal("110"),
            )

    def test_correction_archives_previous_values_and_recomputes(self):
        corrected = correct_counter_reading(
            reading=self.reading, actor=self.staff, reason="رقم النهاية كان خاطئًا",
            end_reading=Decimal("120"),
        )
        self.assertEqual(corrected.status, MachineCounterReading.Status.CORRECTED)
        self.assertEqual(corrected.end_reading, Decimal("120"))
        self.assertEqual(corrected.counter_output, Decimal("120"))
        self.assertEqual(len(corrected.corrections), 1)
        archived = corrected.corrections[0]
        # Snapshot values are strings of the stored (4-decimal) values.
        self.assertEqual(Decimal(archived["previous"]["end_reading"]), Decimal("100"))
        self.assertEqual(Decimal(archived["previous"]["counter_output"]), Decimal("100"))
        self.assertEqual(archived["reason"], "رقم النهاية كان خاطئًا")
        self.assertEqual(archived["status_before"], MachineCounterReading.Status.COMPLETE)

    def test_corrected_reading_can_be_reapproved(self):
        approve_counter_reading(reading=self.reading, actor=self.staff)
        corrected = correct_counter_reading(
            reading=self.reading, actor=self.staff, reason="تصحيح بعد الاعتماد",
            rejected_quantity=Decimal("5"),
        )
        self.assertEqual(corrected.status, MachineCounterReading.Status.CORRECTED)
        reapproved = approve_counter_reading(reading=corrected, actor=self.staff)
        self.assertEqual(reapproved.status, MachineCounterReading.Status.APPROVED)
        self.assertEqual(reapproved.accepted_good_base, Decimal("95"))

    def test_direct_edit_and_delete_of_recorded_reading_blocked(self):
        reading = MachineCounterReading.objects.get(pk=self.reading.pk)
        reading.notes = "محاولة تعديل مباشرة"
        with self.assertRaises(ValidationError):
            reading.save()
        with self.assertRaises(ValidationError):
            reading.delete()

    def test_correction_rejects_end_below_start(self):
        with self.assertRaises(ValidationError):
            correct_counter_reading(
                reading=self.reading, actor=self.staff, reason="قيمة خاطئة",
                end_reading=Decimal("-10"),
            )

    def test_correction_rejects_rejected_above_output(self):
        with self.assertRaises(ValidationError):
            correct_counter_reading(
                reading=self.reading, actor=self.staff, reason="مرفوض كبير",
                rejected_quantity=Decimal("500"),
            )


class CounterViewTests(CounterFixtureMixin, TestCase):
    def setUp(self):
        self.create_counter_fixture()
        self.order = self.released_order()
        self.client.force_login(self.staff)

    def test_full_web_flow_start_close_approve(self):
        start_url = reverse("manufacturing:counter_start", args=[self.order.pk])
        response = self.client.get(start_url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            start_url,
            {"unit": self.pcs.pk, "start_reading": "500", "roll_reference": "ROLL-1"},
        )
        self.assertEqual(response.status_code, 302)
        reading = MachineCounterReading.objects.get(order=self.order)
        self.assertEqual(reading.start_reading, Decimal("500"))
        self.assertEqual(reading.roll_reference, "ROLL-1")

        close_url = reverse("manufacturing:counter_close", args=[reading.pk])
        response = self.client.post(
            close_url, {"end_reading": "800", "rejected_quantity": "20"}
        )
        self.assertEqual(response.status_code, 302)
        reading.refresh_from_db()
        self.assertEqual(reading.status, MachineCounterReading.Status.COMPLETE)

        approve_url = reverse("manufacturing:counter_approve", args=[reading.pk])
        response = self.client.post(approve_url)
        self.assertEqual(response.status_code, 302)
        reading.refresh_from_db()
        self.assertEqual(reading.status, MachineCounterReading.Status.APPROVED)

    def test_correction_view_records_audit(self):
        close_counter_interval(reading=self.reading_for_view(), actor=self.staff, end_reading=Decimal("900"))
        reading = MachineCounterReading.objects.get(order=self.order)
        correct_url = reverse("manufacturing:counter_correct", args=[reading.pk])
        response = self.client.post(correct_url, {"reason": "رقم خاطئ", "end_reading": "950"})
        self.assertEqual(response.status_code, 302)
        reading.refresh_from_db()
        self.assertEqual(reading.status, MachineCounterReading.Status.CORRECTED)
        self.assertEqual(len(reading.corrections), 1)

    def reading_for_view(self):
        return start_counter_interval(
            actor=self.staff, order=self.order, production_line=self.line,
            unit=self.pcs, start_reading=Decimal("100"),
        )

    def test_order_detail_lists_counter_readings(self):
        self.reading_for_view()
        response = self.client.get(
            reverse("manufacturing:order_detail", args=[self.order.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "قراءات عدّاد الآلة")

    def test_counter_views_require_staff(self):
        self.client.force_login(self.reader)
        start_url = reverse("manufacturing:counter_start", args=[self.order.pk])
        response = self.client.get(start_url)
        self.assertEqual(response.status_code, 403)


class CounterPermissionTests(CounterFixtureMixin, TestCase):
    def setUp(self):
        self.create_counter_fixture()

    def test_counter_meta_permissions_exist(self):
        from django.contrib.auth.models import Permission

        codenames = set(
            Permission.objects.filter(
                content_type__app_label="manufacturing",
                codename__in=[
                    "record_counter_reading",
                    "approve_counter_reading",
                    "correct_counter_reading",
                ],
            ).values_list("codename", flat=True)
        )
        self.assertEqual(
            codenames,
            {"record_counter_reading", "approve_counter_reading", "correct_counter_reading"},
        )

    def test_unauthenticated_actor_rejected(self):
        order = self.released_order()
        with self.assertRaises(ValidationError):
            start_counter_interval(
                actor=None, order=order, production_line=self.line,
                unit=self.pcs, start_reading=Decimal("0"),
            )
