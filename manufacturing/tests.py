from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product, ProductCategory, ProductUnitConversion, UnitOfMeasure
from costing.models import CostingPeriod
from inventory.models import StockLedgerEntry, StockMovement, StorageLocation, Warehouse
from inventory.services import balance_for, post_movement, reverse_movement

from .models import (
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
from .services import (
    cancel_order,
    complete_operation,
    complete_order,
    issue_material,
    receive_finished_goods,
    receive_replacement_finished_goods,
    order_summary,
    record_operation_progress,
    record_production_result,
    release_order,
    requirement_status,
    save_draft_order,
)


class ManufacturingFixtureMixin:
    def create_fixture(self):
        User = get_user_model()
        suffix = uuid.uuid4().hex[:8].upper()
        self.staff = User.objects.create_user(username=f"mfg-staff-{id(self)}", password="test-password", is_staff=True)
        self.regular = User.objects.create_user(username=f"mfg-reader-{id(self)}", password="test-password")
        self.pcs = UnitOfMeasure.objects.create(code=f"P{suffix}", name_ar="قطعة", dimension="count")
        self.kg = UnitOfMeasure.objects.create(code=f"K{suffix}", name_ar="كجم", dimension="mass")
        self.finished_category = ProductCategory.objects.get_or_create(code="finished-goods", defaults={"name_ar": "منتج تام"})[0]
        self.raw_category = ProductCategory.objects.get_or_create(code="raw-material", defaults={"name_ar": "مادة خام"})[0]
        self.product = Product.objects.create(sku=f"MFG-{suffix}", name_ar="منتج اختبار", category=self.finished_category, base_uom=self.pcs)
        self.material = Product.objects.create(sku=f"MAT-{suffix}", name_ar="مادة اختبار", category=self.raw_category, base_uom=self.kg)
        self.line = ProductionLine.objects.get_or_create(code="fiber-containers", defaults={"name_ar": "خط الألياف"})[0]
        self.press = ProductionOperation.objects.get_or_create(code="fiber-pressing-in-mold", defaults={"name_ar": "كبس في قالب"})[0]
        warehouse = Warehouse.objects.create(code=f"MFG-{suffix}", name_ar="مخزن الاختبار")
        self.location = StorageLocation.objects.create(warehouse=warehouse, code="RAW", name_ar="موقع المواد")
        self.output_location = StorageLocation.objects.create(warehouse=warehouse, code="FG", name_ar="موقع المنتج التام")
        self.bom = BillOfMaterials.objects.create(
            product=self.product, version=1, output_quantity=Decimal("100"), output_unit=self.pcs,
            created_by=self.staff,
        )
        self.component = BOMComponent.objects.create(
            bom=self.bom, material=self.material, quantity=Decimal("12"), unit=self.kg, sequence=10,
        )
        self.routing = Routing.objects.create(
            product=self.product, production_line=self.line, version=1, created_by=self.staff,
        )
        self.route_step = RoutingStep.objects.create(routing=self.routing, operation=self.press, sequence=1)

    def create_order(self, *, quantity="1000", bom="default", routing="default"):
        return save_draft_order(
            actor=self.staff, product=self.product, production_line=self.line,
            bom=self.bom if bom == "default" else bom, routing=self.routing if routing == "default" else routing,
            source_location=self.location, planned_quantity=Decimal(quantity), planned_unit=self.pcs, notes="",
        )

    def receive_material_stock(self, quantity="100"):
        return post_movement(
            reference=f"MFG-TEST-RECEIPT-{StockMovement.objects.count()}-{id(self)}",
            movement_type=StockMovement.Type.RECEIPT, product=self.material,
            quantity=Decimal(quantity), unit=self.kg, actor=self.staff, destination_location=self.location,
        )


class ManufacturingWorkflowTests(ManufacturingFixtureMixin, TestCase):
    def setUp(self):
        self.create_fixture()

    def test_order_release_snapshots_bom_and_only_configured_fiber_press_operation(self):
        order = self.create_order()
        self.assertEqual(order.status, ProductionOrder.Status.DRAFT)
        release_order(order=order, actor=self.staff)
        order.refresh_from_db()
        self.assertEqual(order.status, ProductionOrder.Status.RELEASED)
        self.assertEqual(order.requirements.count(), 1)
        self.assertEqual(order.requirements.get().planned_quantity_base, Decimal("120.000000000000"))
        self.assertEqual(order.operations.count(), 1)
        self.assertEqual(order.operations.get().operation_name, self.press.name_ar)
        self.assertFalse(order.operations.filter(operation_name__icontains="تجفيف").exists())

    def test_cup_and_fiber_orders_keep_product_routes_separate_and_share_inventory(self):
        cup_line = ProductionLine.objects.get_or_create(
            code="paper-cups", defaults={"name_ar": "خط الأكواب"},
        )[0]
        cup_operations = {
            code: ProductionOperation.objects.get_or_create(
                code=code, defaults={"name_ar": code},
            )[0]
            for code in (
                "cup-printing", "cup-cutting", "cup-forming",
                "cup-bottom-assembly", "cup-packaging",
            )
        }
        cup_product = Product.objects.create(
            sku=f"CUP-{uuid.uuid4().hex[:8].upper()}", name_ar="كوب اختبار",
            category=self.finished_category, base_uom=self.pcs,
        )
        cup_product_optional = Product.objects.create(
            sku=f"CUP-{uuid.uuid4().hex[:8].upper()}", name_ar="كوب اختبار بقاع مركب",
            category=self.finished_category, base_uom=self.pcs,
        )

        cup_boms = []
        for product in (cup_product, cup_product_optional):
            bom = BillOfMaterials.objects.create(
                product=product, version=1, output_quantity=Decimal("100"),
                output_unit=self.pcs, created_by=self.staff,
            )
            BOMComponent.objects.create(
                bom=bom, material=self.material, quantity=Decimal("12"), unit=self.kg, sequence=10,
            )
            cup_boms.append(bom)

        routings = []
        route_codes = [
            ("cup-printing", "cup-cutting", "cup-forming", "cup-packaging"),
            ("cup-printing", "cup-cutting", "cup-forming", "cup-bottom-assembly", "cup-packaging"),
        ]
        for product, codes in zip((cup_product, cup_product_optional), route_codes):
            routing = Routing.objects.create(
                product=product, production_line=cup_line, version=1, created_by=self.staff,
            )
            for sequence, code in enumerate(codes, start=1):
                RoutingStep.objects.create(
                    routing=routing, operation=cup_operations[code], sequence=sequence,
                )
            routings.append(routing)

        cup_orders = []
        for product, bom, routing in zip((cup_product, cup_product_optional), cup_boms, routings):
            cup_orders.append(save_draft_order(
                actor=self.staff, product=product, production_line=cup_line, bom=bom, routing=routing,
                source_location=self.location, planned_quantity=Decimal("100"), planned_unit=self.pcs, notes="",
            ))
        fiber_order = self.create_order(quantity="100")
        for order in [*cup_orders, fiber_order]:
            release_order(order=order, actor=self.staff)

        first_codes = list(cup_orders[0].operations.order_by("sequence").values_list("operation__code", flat=True))
        second_codes = list(cup_orders[1].operations.order_by("sequence").values_list("operation__code", flat=True))
        self.assertEqual(first_codes, list(route_codes[0]))
        self.assertEqual(second_codes, list(route_codes[1]))
        self.assertEqual(list(fiber_order.operations.values_list("operation__code", flat=True)), ["fiber-pressing-in-mold"])
        self.assertEqual(cup_orders[0].production_line.code, "paper-cups")
        self.assertEqual(fiber_order.production_line.code, "fiber-containers")

        self.receive_material_stock("36")
        cup_issue = issue_material(
            order=cup_orders[0], requirement=cup_orders[0].requirements.get(), quantity=Decimal("12"),
            unit=self.kg, reference="CUP-SHARED-ISSUE", actor=self.staff,
        )
        fiber_issue = issue_material(
            order=fiber_order, requirement=fiber_order.requirements.get(), quantity=Decimal("12"),
            unit=self.kg, reference="FIBER-SHARED-ISSUE", actor=self.staff,
        )
        self.assertNotEqual(cup_issue.movement_id, fiber_issue.movement_id)
        self.assertEqual(cup_issue.movement.source_reference_type, "manufacturing.material_issue")
        self.assertEqual(fiber_issue.movement.source_reference_type, "manufacturing.material_issue")
        self.assertEqual(balance_for(self.material, self.location), Decimal("12.000000000000"))

    def test_missing_bom_or_routing_is_reported_without_inventing_requirements(self):
        no_bom = self.create_order(bom=None)
        no_bom.bom = None
        no_bom.save(update_fields=["bom"])
        with self.assertRaisesMessage(ValidationError, "قائمة المواد غير مضبوطة"):
            release_order(order=no_bom, actor=self.staff)
        no_route = self.create_order()
        no_route.routing = None
        no_route.save(update_fields=["routing"])
        with self.assertRaisesMessage(ValidationError, "مسار الإنتاج غير مضبوط"):
            release_order(order=no_route, actor=self.staff)
        self.assertEqual(no_bom.requirements.count(), 0)

    def test_partial_issue_tracks_shortage_and_allows_later_remainder(self):
        order = self.create_order()
        release_order(order=order, actor=self.staff)
        requirement = order.requirements.get()
        self.receive_material_stock("100")
        self.assertEqual(requirement_status(requirement)["shortage"], Decimal("20.000000000000"))

        first = issue_material(
            order=order, requirement=requirement, quantity=Decimal("100"), unit=self.kg,
            reference="PARTIAL-100", actor=self.staff,
        )
        state = requirement_status(requirement)
        self.assertEqual(state["planned"], Decimal("120.000000000000"))
        self.assertEqual(state["issued"], Decimal("100.000000000000"))
        self.assertEqual(state["remaining"], Decimal("20.000000000000"))
        self.assertEqual(state["shortage"], Decimal("20.000000000000"))
        self.assertEqual(first.movement.movement_type, StockMovement.Type.ISSUE)
        self.assertEqual(first.movement.ledger_entries.count(), 1)

        self.receive_material_stock("20")
        second = issue_material(
            order=order, requirement=requirement, quantity=Decimal("20"), unit=self.kg,
            reference="PARTIAL-20", actor=self.staff,
        )
        self.assertEqual(second.order_id, order.pk)
        self.assertEqual(requirement_status(requirement)["remaining"], Decimal("0E-12"))
        self.assertEqual(balance_for(self.material, self.location), Decimal("0E-12"))
        self.assertEqual(MaterialIssue.objects.filter(order=order).count(), 2)

    def test_issue_cannot_exceed_requirement_or_stock_and_failure_is_atomic(self):
        order = self.create_order()
        release_order(order=order, actor=self.staff)
        requirement = order.requirements.get()
        self.receive_material_stock("100")
        before_movements = StockMovement.objects.count()
        with self.assertRaisesMessage(ValidationError, "الكمية تتجاوز المتبقي"):
            issue_material(order=order, requirement=requirement, quantity=Decimal("121"), unit=self.kg,
                           reference="OVER-REQUIREMENT", actor=self.staff)
        with self.assertRaisesMessage(ValidationError, "الرصيد المتاح"):
            issue_material(order=order, requirement=requirement, quantity=Decimal("101"), unit=self.kg,
                           reference="OVER-STOCK", actor=self.staff)
        self.assertEqual(StockMovement.objects.count(), before_movements)
        self.assertEqual(MaterialIssue.objects.count(), 0)
        self.assertEqual(balance_for(self.material, self.location), Decimal("100.000000000000"))

    def test_finalized_costing_month_blocks_issue_result_and_issue_reversal(self):
        order = self.create_order()
        release_order(order=order, actor=self.staff)
        requirement = order.requirements.get()
        self.receive_material_stock("100")
        issue = issue_material(
            order=order,
            requirement=requirement,
            quantity=Decimal("12"),
            unit=self.kg,
            reference="BEFORE-COST-CLOSE",
            actor=self.staff,
        )
        today = timezone.localdate()
        CostingPeriod.objects.create(
            year=today.year,
            month=today.month,
            status=CostingPeriod.Status.FINALIZED,
        )
        before_movements = StockMovement.objects.count()
        with self.assertRaisesMessage(ValidationError, "اعتماد فترة التكاليف"):
            issue_material(
                order=order,
                requirement=requirement,
                quantity=Decimal("1"),
                unit=self.kg,
                reference="AFTER-COST-CLOSE",
                actor=self.staff,
            )
        with self.assertRaisesMessage(ValidationError, "اعتماد فترة التكاليف"):
            record_production_result(
                order=order,
                good_quantity=Decimal("1"),
                scrap_quantity=Decimal("0"),
                unit=self.pcs,
                notes="",
                actor=self.staff,
            )
        with self.assertRaisesMessage(ValidationError, "اعتماد فترة التكاليف"):
            reverse_movement(
                movement=issue.movement,
                reference="REVERSE-AFTER-COST-CLOSE",
                actor=self.staff,
                reason="محاولة بعد الاعتماد",
            )
        self.assertEqual(StockMovement.objects.count(), before_movements)
        self.assertEqual(MaterialIssue.objects.filter(order=order).count(), 1)

    def test_uom_conversions_are_applied_to_order_and_bom(self):
        box = UnitOfMeasure.objects.create(code=f"B{uuid.uuid4().hex[:8].upper()}", name_ar="علبة", dimension="count")
        ProductUnitConversion.objects.create(product=self.product, unit=box, factor=Decimal("10"))
        order = self.create_order(quantity="20")
        order.planned_quantity = Decimal("20")
        order.planned_unit = box
        order.save(update_fields=["planned_quantity", "planned_unit"])
        order.full_clean()
        order.save(update_fields=["planned_quantity_base"])
        release_order(order=order, actor=self.staff)
        self.assertEqual(order.requirements.get().planned_quantity_base, Decimal("24.000000000000"))

    def test_result_scrap_partial_receipts_and_completion_allow_underproduction(self):
        order = self.create_order()
        release_order(order=order, actor=self.staff)
        step = order.operations.get()
        record_operation_progress(order=order, order_operation=step, quantity=Decimal("800"), unit=self.pcs, notes="", actor=self.staff)
        complete_operation(order=order, order_operation=step, actor=self.staff)
        result, _ = record_production_result(
            order=order, good_quantity=Decimal("800"), scrap_quantity=Decimal("10"),
            unit=self.pcs, notes="دفعة جزئية", actor=self.staff,
        )
        self.assertEqual(result.yield_percent, Decimal("800") * 100 / Decimal("810"))
        with self.assertRaisesMessage(ValidationError, "تتجاوز الإنتاج الجيد غير المستلم"):
            receive_finished_goods(order=order, quantity=Decimal("801"), unit=self.pcs,
                                   destination_location=self.output_location, reference="TOO-MUCH-FG", actor=self.staff)
        first = receive_finished_goods(order=order, quantity=Decimal("500"), unit=self.pcs,
                                       destination_location=self.output_location, reference="FG-500", actor=self.staff)
        second = receive_finished_goods(order=order, quantity=Decimal("300"), unit=self.pcs,
                                        destination_location=self.output_location, reference="FG-300", actor=self.staff)
        self.assertEqual(first.movement.movement_type, StockMovement.Type.RECEIPT)
        self.assertEqual(second.movement.ledger_entries.count(), 1)
        complete_order(order=order, actor=self.staff)
        order.refresh_from_db()
        self.assertEqual(order.status, ProductionOrder.Status.COMPLETED)
        self.assertEqual(balance_for(self.product, self.output_location), Decimal("800.000000000000"))

    def test_result_submission_is_idempotent_and_rejects_key_payload_mismatch(self):
        order = self.create_order()
        release_order(order=order, actor=self.staff)
        key = uuid.uuid4()
        first, created = record_production_result(order=order, good_quantity=Decimal("10"), scrap_quantity=Decimal("0"),
                                                 unit=self.pcs, notes="", actor=self.staff, submission_key=key)
        replay, replay_created = record_production_result(order=order, good_quantity=Decimal("10"), scrap_quantity=Decimal("0"),
                                                         unit=self.pcs, notes="", actor=self.staff, submission_key=key)
        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(first.pk, replay.pk)
        self.assertEqual(order.results.count(), 1)
        with self.assertRaisesMessage(ValidationError, "مفتاح الإرسال سبق استخدامه"):
            record_production_result(order=order, good_quantity=Decimal("11"), scrap_quantity=Decimal("0"),
                                     unit=self.pcs, notes="", actor=self.staff, submission_key=key)
        self.assertEqual(order.actual_good_base, Decimal("10.000000000000"))

    def test_progress_submission_is_idempotent_and_downstream_is_bounded_by_previous_step(self):
        second = ProductionOperation.objects.create(code=f"step-{uuid.uuid4().hex[:8]}", name_ar="العملية التالية")
        RoutingStep.objects.create(routing=self.routing, operation=second, sequence=2)
        order = self.create_order()
        release_order(order=order, actor=self.staff)
        first_step, second_step = order.operations.order_by("sequence")
        key = uuid.uuid4()
        one, created = record_operation_progress(order=order, order_operation=first_step, quantity=Decimal("20"),
                                                  unit=self.pcs, notes="batch", actor=self.staff, submission_key=key)
        replay, replay_created = record_operation_progress(order=order, order_operation=first_step, quantity=Decimal("20"),
                                                            unit=self.pcs, notes="batch", actor=self.staff, submission_key=key)
        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(one.pk, replay.pk)
        complete_operation(order=order, order_operation=first_step, actor=self.staff)
        with self.assertRaisesMessage(ValidationError, "يتجاوز كمية الأمر المخططة"):
            record_operation_progress(order=order, order_operation=second_step, quantity=Decimal("21"),
                                      unit=self.pcs, notes="", actor=self.staff)
        record_operation_progress(order=order, order_operation=second_step, quantity=Decimal("20"),
                                  unit=self.pcs, notes="", actor=self.staff)

    def test_required_step_cannot_be_skipped_but_optional_snapshot_can(self):
        order = self.create_order()
        release_order(order=order, actor=self.staff)
        required_step = order.operations.get()
        with self.assertRaisesMessage(ValidationError, "عملية مطلوبة"):
            complete_operation(order=order, order_operation=required_step, actor=self.staff, skip=True)

        optional_route = Routing.objects.create(product=self.product, production_line=self.line, version=2, created_by=self.staff)
        RoutingStep.objects.create(routing=optional_route, operation=self.press, sequence=1, is_optional=True)
        order2 = self.create_order(routing=optional_route)
        release_order(order=order2, actor=self.staff)
        snapshot = order2.operations.get()
        self.assertTrue(snapshot.is_optional)
        complete_operation(order=order2, order_operation=snapshot, actor=self.staff, skip=True)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, ProductionOrderOperation.Status.SKIPPED)

    def test_reversed_receipt_keeps_order_completed_and_allows_controlled_replacement(self):
        order = self.create_order()
        release_order(order=order, actor=self.staff)
        step = order.operations.get()
        record_operation_progress(order=order, order_operation=step, quantity=Decimal("10"), unit=self.pcs, notes="", actor=self.staff)
        complete_operation(order=order, order_operation=step, actor=self.staff)
        record_production_result(order=order, good_quantity=Decimal("10"), scrap_quantity=Decimal("0"),
                                 unit=self.pcs, notes="", actor=self.staff)
        original = receive_finished_goods(order=order, quantity=Decimal("10"), unit=self.pcs,
                                          destination_location=self.output_location, reference="REVERSE-ORIGINAL", actor=self.staff)
        complete_order(order=order, actor=self.staff)
        reverse_movement(movement=original.movement, reference="REVERSE-ORIGINAL-REV", actor=self.staff, reason="اختبار عكس")
        order.refresh_from_db()
        self.assertEqual(order.status, ProductionOrder.Status.COMPLETED)
        with self.assertRaisesMessage(ValidationError, "الاستلام التعويضي يتطلب مساره الصريح"):
            receive_finished_goods(order=order, quantity=Decimal("10"), unit=self.pcs,
                                   destination_location=self.output_location, reference="REVERSE-ORDINARY", actor=self.staff)
        replacement = receive_replacement_finished_goods(order=order, original_receipt=original, quantity=Decimal("10"),
                                                         unit=self.pcs, destination_location=self.output_location,
                                                         reference="REVERSE-REPLACEMENT", actor=self.staff)
        self.assertEqual(replacement.replacement_for_id, original.pk)
        self.assertEqual(order_summary(order)["received"], Decimal("10.000000000000"))
        self.assertEqual(ProductionOrder.objects.get(pk=order.pk).status, ProductionOrder.Status.COMPLETED)

    def test_completion_requires_all_good_output_received_but_not_full_plan(self):
        order = self.create_order()
        release_order(order=order, actor=self.staff)
        step = order.operations.get()
        record_operation_progress(order=order, order_operation=step, quantity=Decimal("25"), unit=self.pcs, notes="", actor=self.staff)
        complete_operation(order=order, order_operation=step, actor=self.staff)
        record_production_result(order=order, good_quantity=Decimal("25"), scrap_quantity=Decimal("0"), unit=self.pcs,
                                 notes="", actor=self.staff)
        with self.assertRaisesMessage(ValidationError, "استلم كامل الإنتاج الجيد"):
            complete_order(order=order, actor=self.staff)
        receive_finished_goods(order=order, quantity=Decimal("25"), unit=self.pcs,
                               destination_location=self.output_location, reference="FG-PARTIAL-PLAN", actor=self.staff)
        complete_order(order=order, actor=self.staff)
        order.refresh_from_db()
        self.assertEqual(order.status, ProductionOrder.Status.COMPLETED)
        self.assertEqual(order.actual_good_base, Decimal("25.000000000000"))

    def test_cancel_preserves_issued_material_history_and_terminal_orders_reject_postings(self):
        order = self.create_order()
        release_order(order=order, actor=self.staff)
        requirement = order.requirements.get()
        self.receive_material_stock("10")
        issue = issue_material(order=order, requirement=requirement, quantity=Decimal("5"), unit=self.kg,
                               reference="CANCEL-ISSUE", actor=self.staff)
        cancel_order(order=order, actor=self.staff)
        self.assertTrue(MaterialIssue.objects.filter(pk=issue.pk).exists())
        with self.assertRaisesMessage(ValidationError, "لا يمكن صرف مواد"):
            issue_material(order=order, requirement=requirement, quantity=Decimal("1"), unit=self.kg,
                           reference="CANCEL-ISSUE-2", actor=self.staff)

    def test_duplicate_posting_and_non_staff_are_rejected(self):
        order = self.create_order()
        release_order(order=order, actor=self.staff)
        requirement = order.requirements.get()
        self.receive_material_stock("20")
        issue_material(order=order, requirement=requirement, quantity=Decimal("5"), unit=self.kg,
                       reference="UNIQUE-ISSUE", actor=self.staff)
        before = StockMovement.objects.count()
        with self.assertRaisesMessage(ValidationError, "مرجع الصرف مستخدم"):
            issue_material(order=order, requirement=requirement, quantity=Decimal("5"), unit=self.kg,
                           reference="UNIQUE-ISSUE", actor=self.staff)
        with self.assertRaisesMessage(ValidationError, "مستخدمي النظام الداخليين"):
            issue_material(order=order, requirement=requirement, quantity=Decimal("1"), unit=self.kg,
                           reference="UNAUTHORIZED-ISSUE", actor=self.regular)
        self.assertEqual(StockMovement.objects.count(), before)

    def test_bom_and_route_revisions_used_by_released_order_are_immutable(self):
        order = self.create_order()
        release_order(order=order, actor=self.staff)
        self.bom.notes = "تغيير"
        with self.assertRaises(ValidationError):
            self.bom.full_clean()
        self.routing.notes = "تغيير"
        with self.assertRaises(ValidationError):
            self.routing.full_clean()

    def test_manufacturing_pages_are_authenticated_and_writes_are_staff_only(self):
        routes = (reverse("manufacturing:orders"), reverse("manufacturing:configuration"))
        for route in routes:
            self.assertEqual(self.client.get(route, secure=True).status_code, 302)
        self.client.force_login(self.regular)
        self.assertEqual(self.client.get(reverse("manufacturing:orders"), secure=True).status_code, 200)
        self.assertEqual(self.client.get(reverse("manufacturing:order_add"), secure=True).status_code, 403)
        self.client.force_login(self.staff)
        for route in (reverse("manufacturing:orders"), reverse("manufacturing:configuration"), reverse("manufacturing:order_add"),
                      reverse("manufacturing:line_add"), reverse("manufacturing:operation_add"), reverse("manufacturing:bom_add"),
                      reverse("manufacturing:routing_add")):
            self.assertEqual(self.client.get(route, secure=True).status_code, 200)

    def test_non_staff_post_is_denied_for_every_manufacturing_write_endpoint(self):
        order = self.create_order()
        routes = [
            reverse("manufacturing:order_add"), reverse("manufacturing:order_edit", args=[order.pk]),
            reverse("manufacturing:order_release", args=[order.pk]), reverse("manufacturing:order_complete", args=[order.pk]),
            reverse("manufacturing:order_cancel", args=[order.pk]), reverse("manufacturing:issue_add", args=[order.pk]),
            reverse("manufacturing:progress_add", args=[order.pk]), reverse("manufacturing:operation_complete", args=[order.pk, 1]),
            reverse("manufacturing:operation_skip", args=[order.pk, 1]), reverse("manufacturing:result_add", args=[order.pk]),
            reverse("manufacturing:receipt_add", args=[order.pk]), reverse("manufacturing:receipt_replace", args=[order.pk, uuid.uuid4()]),
            reverse("manufacturing:line_add"), reverse("manufacturing:line_edit", args=[self.line.pk]),
            reverse("manufacturing:operation_add"), reverse("manufacturing:operation_edit", args=[self.press.pk]),
            reverse("manufacturing:bom_add"), reverse("manufacturing:bom_edit", args=[self.bom.pk]),
            reverse("manufacturing:routing_add"), reverse("manufacturing:routing_edit", args=[self.routing.pk]),
        ]
        self.client.force_login(self.regular)
        for route in routes:
            with self.subTest(route=route):
                self.assertEqual(self.client.post(route, {}, secure=True).status_code, 403)


class ManufacturingConcurrencyTests(ManufacturingFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.create_fixture()
        self.order = self.create_order(quantity="100")
        release_order(order=self.order, actor=self.staff)
        self.requirement = self.order.requirements.get()
        self.receive_material_stock("10")

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_issues_cannot_exceed_order_requirement_or_available_stock(self):
        barrier = Barrier(2)

        def attempt(reference):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.staff.pk)
                order = ProductionOrder.objects.get(pk=self.order.pk)
                requirement = order.requirements.get()
                barrier.wait(timeout=10)
                try:
                    issue_material(order=order, requirement=requirement, quantity=Decimal("7"), unit=self.kg,
                                   reference=reference, actor=actor)
                    return "posted"
                except ValidationError:
                    return "rejected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ("RACE-ISSUE-A", "RACE-ISSUE-B")))
        self.assertCountEqual(results, ["posted", "rejected"])
        self.assertEqual(MaterialIssue.objects.filter(order=self.order).count(), 1)
        self.assertEqual(balance_for(self.material, self.location), Decimal("3.000000000000"))

    @skipUnlessDBFeature("has_select_for_update")
    def test_release_and_bom_component_edit_serialize_to_one_complete_snapshot(self):
        order = self.create_order(quantity="1000")
        barrier = Barrier(2)

        def release():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.staff.pk)
                candidate = ProductionOrder.objects.get(pk=order.pk)
                barrier.wait(timeout=10)
                try:
                    release_order(order=candidate, actor=actor)
                    return "released"
                except ValidationError:
                    return "rejected"
            finally:
                close_old_connections()

        def edit_component():
            close_old_connections()
            try:
                component = BOMComponent.objects.get(pk=self.component.pk)
                barrier.wait(timeout=10)
                try:
                    component.quantity = Decimal("13")
                    component.save()
                    return "edited"
                except ValidationError:
                    return "rejected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda fn: fn(), (release, edit_component)))
        order.refresh_from_db()
        self.assertEqual(order.status, ProductionOrder.Status.RELEASED)
        self.assertIn("released", results)
        expected = Decimal("130.000000000000") if "edited" in results else Decimal("120.000000000000")
        self.assertEqual(order.requirements.get().planned_quantity_base, expected)

    @skipUnlessDBFeature("has_select_for_update")
    def test_racing_duplicate_issue_reference_rolls_back_losing_inventory_post(self):
        other_order = self.create_order(quantity="100")
        release_order(order=other_order, actor=self.staff)
        self.receive_material_stock("10")
        barrier = Barrier(2)

        def attempt(order_id):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.staff.pk)
                order = ProductionOrder.objects.get(pk=order_id)
                requirement = order.requirements.get()
                barrier.wait(timeout=10)
                try:
                    issue_material(order=order, requirement=requirement, quantity=Decimal("1"), unit=self.kg,
                                   reference="RACE-SAME-REFERENCE", actor=actor)
                    return "posted"
                except ValidationError:
                    return "rejected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, (self.order.pk, other_order.pk)))
        self.assertCountEqual(results, ["posted", "rejected"])
        self.assertEqual(MaterialIssue.objects.filter(reference="RACE-SAME-REFERENCE").count(), 1)
        self.assertEqual(balance_for(self.material, self.location), Decimal("19.000000000000"))
