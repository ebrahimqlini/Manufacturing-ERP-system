"""Real PostgreSQL concurrency tests for the monthly-closing locking protocol.

These use ``TransactionTestCase`` with worker threads that each open their own
database connection, so two transactions genuinely overlap inside PostgreSQL.
They verify the month-scoped advisory-lock protocol documented in
``costing.models``: an operational write and the closing/finalization of the
same month serialize, concurrent first calculations of a brand-new month create
exactly one period row, and a write can never commit into a month that another
transaction finalized first.

Every database-dependent assertion targets the verified ``test_paper_factory``
database; the production ``paper_factory`` database is never touched.
"""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest import mock
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from catalog.models import Product, ProductCategory, UnitOfMeasure
from costing.models import (
    CostingPeriod,
    MonthlyExpense,
    MonthlyProfitPeriod,
    RawMaterialPrice,
)
from costing.profit_services import (
    calculate_monthly_profit,
    finalize_monthly_profit,
)
from costing.services import calculate_monthly_costing, finalize_costing_period
from inventory.models import StockMovement, StorageLocation, Warehouse
from inventory.services import balance_for, post_movement
from manufacturing.models import (
    BOMComponent,
    BillOfMaterials,
    MaterialIssue,
    ProductionLine,
    ProductionOperation,
    ProductionOrder,
    Routing,
    RoutingStep,
)
from manufacturing.services import (
    issue_material,
    record_production_result,
    release_order,
    save_draft_order,
)
from sales.models import Customer, SalesOrder
from sales.services import (
    confirm_order as confirm_sales_order,
    create_shipment,
    save_draft_order as save_sales_draft_order,
)


class _RaceHarness:
    """Run callables concurrently on separate connections and capture outcomes."""

    def run_race(self, *callables):
        barrier = Barrier(len(callables))
        outcomes = [None] * len(callables)

        def runner(index, fn):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                fn()
                outcomes[index] = "ok"
            except ValidationError as exc:
                outcomes[index] = ("rejected", " ".join(exc.messages))
            except Exception as exc:  # pragma: no cover - surfaced by assertions
                outcomes[index] = ("error", repr(exc))
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(callables)) as pool:
            futures = [pool.submit(runner, i, fn) for i, fn in enumerate(callables)]
            for future in futures:
                future.result(timeout=120)
        return outcomes


class CostingClosingConcurrencyTests(TransactionTestCase, _RaceHarness):
    """Costing-period finalization racing operational material issues."""

    reset_sequences = True

    def setUp(self):
        User = get_user_model()
        suffix = uuid.uuid4().hex[:8].upper()
        self.staff = User.objects.create_user(
            username=f"conc-cost-{suffix}", password="pw", is_staff=True
        )
        for codename, app in (
            ("post_movement", "inventory"),
            ("release_order", "manufacturing"),
            ("issue_material", "manufacturing"),
            ("record_production_result", "manufacturing"),
            ("record_production_waste", "manufacturing"),
            ("calculate_monthly_costing", "costing"),
            ("finalize_costing_period", "costing"),
            ("calculate_monthly_profit", "costing"),
        ):
            self.staff.user_permissions.add(
                Permission.objects.get(codename=codename, content_type__app_label=app)
            )

        self.pcs = UnitOfMeasure.objects.create(code=f"PC{suffix}", name_ar="قطعة", dimension="count")
        self.kg = UnitOfMeasure.objects.create(code=f"KG{suffix}", name_ar="كجم", dimension="mass")
        self.finished_cat = ProductCategory.objects.get_or_create(
            code="finished-goods", defaults={"name_ar": "منتج تام"}
        )[0]
        self.raw_cat = ProductCategory.objects.get_or_create(
            code="raw-material", defaults={"name_ar": "مادة خام"}
        )[0]
        self.product = Product.objects.create(
            sku=f"FG-{suffix}", name_ar="منتج", category=self.finished_cat, base_uom=self.pcs
        )
        self.material = Product.objects.create(
            sku=f"RM-{suffix}", name_ar="مادة", category=self.raw_cat, base_uom=self.kg
        )
        wh = Warehouse.objects.create(code=f"WH-{suffix}", name_ar="مخزن")
        self.raw_location = StorageLocation.objects.create(
            warehouse=wh, code="RAW", name_ar="موقع الخام"
        )
        post_movement(
            reference=f"OPEN-{suffix}", movement_type=StockMovement.Type.RECEIPT,
            product=self.material, quantity=Decimal("10000"), unit=self.kg,
            actor=self.staff, destination_location=self.raw_location,
        )
        RawMaterialPrice.objects.create(
            material=self.material, price_per_unit=Decimal("50.00"), unit=self.kg,
            effective_date=timezone.now().date(), created_by=self.staff,
        )

        self.line = ProductionLine.objects.get_or_create(
            code=f"LINE-{suffix}", defaults={"name_ar": "خط"}
        )[0]
        self.press = ProductionOperation.objects.get_or_create(
            code=f"OP-{suffix}", defaults={"name_ar": "كبس"}
        )[0]
        self.bom = BillOfMaterials.objects.create(
            product=self.product, version=1, output_quantity=Decimal("100"),
            output_unit=self.pcs, created_by=self.staff,
        )
        self.component = BOMComponent.objects.create(
            bom=self.bom, material=self.material, quantity=Decimal("12"),
            unit=self.kg, sequence=10,
        )
        self.routing = Routing.objects.create(
            product=self.product, production_line=self.line, version=1, created_by=self.staff,
        )
        self.route_step = RoutingStep.objects.create(
            routing=self.routing, operation=self.press, sequence=1
        )

        self.order = save_draft_order(
            actor=self.staff, product=self.product, production_line=self.line,
            bom=self.bom, routing=self.routing, source_location=self.raw_location,
            planned_quantity=Decimal("1000"), planned_unit=self.pcs, notes="",
        )
        release_order(order=self.order, actor=self.staff)
        self.requirement = self.order.requirements.get()
        # Seed one committed issue and one production result so the month has
        # both consumption and output (is_complete requires good_units > 0 under
        # the default UNITS_PRODUCED allocation policy before it can be frozen).
        issue_material(
            order=self.order, requirement=self.requirement, quantity=Decimal("10"),
            unit=self.kg, reference=f"SEED-{suffix}", actor=self.staff,
        )
        result, _ = record_production_result(
            order=self.order, actor=self.staff, good_quantity=Decimal("50"),
            scrap_quantity=Decimal("0"), unit=self.pcs, notes="",
            occurred_at=timezone.now(),
        )
        from manufacturing.services import confirm_no_material_waste
        confirm_no_material_waste(result=result, actor=self.staff)

        today = timezone.localdate()
        self.year, self.month = today.year, today.month
        for expense_type in (
            MonthlyExpense.ExpenseType.ELECTRICITY,
            MonthlyExpense.ExpenseType.WATER,
            MonthlyExpense.ExpenseType.LABOR,
        ):
            MonthlyExpense.objects.create(
                year=self.year, month=self.month, expense_type=expense_type,
                scope=MonthlyExpense.AllocationScope.FACTORY, amount=Decimal("0.00"),
                description=f"{expense_type} {self.year}/{self.month}",
                created_by=self.staff, updated_by=self.staff,
            )

    def _fresh_actor(self):
        return get_user_model().objects.get(pk=self.staff.pk)

    @skipUnlessDBFeature("has_select_for_update")
    def test_material_issue_racing_finalization_never_posted_into_finalized_month(self):
        period = calculate_monthly_costing(
            year=self.year, month=self.month, actor=self._fresh_actor()
        )
        self.assertTrue(period.is_complete, period.missing_data_notes)
        race_reference = f"RACE-ISSUE-{uuid.uuid4().hex[:8]}"

        def do_issue():
            issue_material(
                order=ProductionOrder.objects.get(pk=self.order.pk),
                requirement=self.requirement, quantity=Decimal("5"), unit=self.kg,
                reference=race_reference, actor=self._fresh_actor(),
            )

        def do_finalize():
            finalize_costing_period(
                period=CostingPeriod.objects.get(pk=period.pk), actor=self._fresh_actor()
            )

        outcomes = self.run_race(do_finalize, do_issue)
        self.assertNotIn(
            "error", [o[0] for o in outcomes if isinstance(o, tuple)],
            f"a thread crashed instead of succeeding or rejecting: {outcomes}",
        )

        period.refresh_from_db()
        raced = MaterialIssue.objects.filter(reference=race_reference).first()

        # Core invariant: a material issue may never exist inside a finalized
        # month unless it was committed before that month was frozen (i.e. the
        # finalization's under-lock recalculation saw it). If finalization won
        # the race, the issue must have been rejected outright.
        if raced is not None and period.status == CostingPeriod.Status.FINALIZED:
            self.assertLessEqual(
                raced.created_at, period.finalized_at,
                "a material issue committed after the month was finalized",
            )
        # If finalization succeeded it must be the terminal state of the month.
        if any(o == "ok" for o in outcomes):
            self.assertIsNotNone(period.finalized_at)

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_first_calculations_create_exactly_one_period(self):
        target_year, target_month = self.year, self.month + 1
        if target_month > 12:
            target_year, target_month = self.year + 1, 1
        # Ensure no period exists yet for the target month.
        self.assertFalse(
            CostingPeriod.objects.filter(year=target_year, month=target_month).exists()
        )

        def calc_a():
            calculate_monthly_costing(
                year=target_year, month=target_month, actor=self._fresh_actor()
            )

        def calc_b():
            calculate_monthly_costing(
                year=target_year, month=target_month, actor=self._fresh_actor()
            )

        outcomes = self.run_race(calc_a, calc_b)
        self.assertNotIn(
            "error", [o[0] for o in outcomes if isinstance(o, tuple)],
            f"concurrent first calculation crashed: {outcomes}",
        )
        self.assertEqual(
            CostingPeriod.objects.filter(year=target_year, month=target_month).count(),
            1,
            "concurrent first calculations must not create duplicate periods",
        )

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_first_profit_calculations_create_exactly_one_period(self):
        target_year, target_month = self.year, self.month + 2
        if target_month > 12:
            target_year, target_month = self.year + 1, target_month - 12
        self.assertFalse(
            MonthlyProfitPeriod.objects.filter(year=target_year, month=target_month).exists()
        )

        def calc_a():
            calculate_monthly_profit(year=target_year, month=target_month, actor=self._fresh_actor())

        def calc_b():
            calculate_monthly_profit(year=target_year, month=target_month, actor=self._fresh_actor())

        outcomes = self.run_race(calc_a, calc_b)
        self.assertNotIn(
            "error", [o[0] for o in outcomes if isinstance(o, tuple)],
            f"concurrent first profit calculation crashed: {outcomes}",
        )
        self.assertEqual(
            MonthlyProfitPeriod.objects.filter(year=target_year, month=target_month).count(),
            1,
            "concurrent first profit calculations must not create duplicate periods",
        )

    @skipUnlessDBFeature("has_select_for_update")
    def test_write_into_finalized_month_is_rejected_without_partial_post(self):
        period = calculate_monthly_costing(
            year=self.year, month=self.month, actor=self._fresh_actor()
        )
        finalize_costing_period(period=period, actor=self._fresh_actor())
        period.refresh_from_db()
        self.assertEqual(period.status, CostingPeriod.Status.FINALIZED)

        movements = StockMovement.objects.count()
        issues = MaterialIssue.objects.count()
        with self.assertRaisesMessage(ValidationError, "بعد اعتماد فترة التكاليف"):
            issue_material(
                order=ProductionOrder.objects.get(pk=self.order.pk),
                requirement=self.requirement, quantity=Decimal("1"), unit=self.kg,
                reference=f"POST-CLOSE-{uuid.uuid4().hex[:8]}", actor=self._fresh_actor(),
            )
        self.assertEqual(StockMovement.objects.count(), movements)
        self.assertEqual(MaterialIssue.objects.count(), issues)

    @skipUnlessDBFeature("has_select_for_update")
    def test_legitimate_write_into_other_open_month_succeeds(self):
        period = calculate_monthly_costing(
            year=self.year, month=self.month, actor=self._fresh_actor()
        )
        finalize_costing_period(period=period, actor=self._fresh_actor())

        # The current month is now frozen, but a production result dated into a
        # later (still-open) month must be accepted. record_production_result is
        # the one operational write that carries an explicit event date.
        self.staff.user_permissions.add(
            Permission.objects.get(
                codename="record_production_result", content_type__app_label="manufacturing"
            )
        )
        from datetime import timedelta
        open_date = timezone.now() + timedelta(days=45)
        # Simulate that the system clock has advanced into the open month.
        # A user-entered occurred_at must not choose the costing month.
        with mock.patch("django.utils.timezone.now", return_value=open_date):
            result, created = record_production_result(
                order=ProductionOrder.objects.get(pk=self.order.pk), actor=self._fresh_actor(),
                good_quantity=Decimal("5"), scrap_quantity=Decimal("0"), unit=self.pcs,
                notes="", occurred_at=open_date,
            )
        self.assertTrue(created)
        self.assertEqual(
            timezone.localdate(result.posted_at).month, open_date.date().month
        )

    @skipUnlessDBFeature("has_select_for_update")
    def test_failed_write_after_lock_leaves_no_partial_inventory_effect(self):
        calculate_monthly_costing(year=self.year, month=self.month, actor=self._fresh_actor())
        # Planned base for the requirement is finite; requesting far more than
        # the remaining quantity must fail after the advisory lock is acquired,
        # leaving no movement, ledger entry, or issue behind.
        movements = StockMovement.objects.count()
        balance = balance_for(self.material, self.raw_location)
        with self.assertRaises(ValidationError):
            issue_material(
                order=ProductionOrder.objects.get(pk=self.order.pk),
                requirement=self.requirement, quantity=Decimal("999999"), unit=self.kg,
                reference=f"OVERISSUE-{uuid.uuid4().hex[:8]}", actor=self._fresh_actor(),
            )
        self.assertEqual(StockMovement.objects.count(), movements)
        self.assertEqual(balance_for(self.material, self.raw_location), balance)

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_reversal_and_profit_calculation_complete_without_deadlock(self):
        """A costing->profit lock order (reversal) against a profit-only writer.

        The reversal acquires the costing month lock then the profit month lock;
        the profit calculation acquires only the profit month lock. The two
        must not deadlock, exercising the documented lock-order discipline.
        """
        from inventory.services import reverse_movement

        movement = StockMovement.objects.filter(
            source_reference_type="", movement_type=StockMovement.Type.RECEIPT
        ).first()
        self.assertIsNotNone(movement)
        target_year, target_month = self.year, self.month + 3
        if target_month > 12:
            target_year, target_month = self.year + 1, target_month - 12

        def do_reverse():
            reverse_movement(
                movement=StockMovement.objects.get(pk=movement.pk),
                reference=f"REV-{uuid.uuid4().hex[:8]}", actor=self._fresh_actor(),
                reason="اختبار عدم وجود راكل",
            )

        def do_profit():
            calculate_monthly_profit(
                year=target_year, month=target_month, actor=self._fresh_actor()
            )

        outcomes = self.run_race(do_reverse, do_profit)
        self.assertNotIn(
            "error", [o[0] for o in outcomes if isinstance(o, tuple)],
            f"reversal vs profit calculation deadlocked or crashed: {outcomes}",
        )


class ProfitClosingConcurrencyTests(TransactionTestCase, _RaceHarness):
    """Monthly-profit finalization racing a sales shipment."""

    reset_sequences = True

    def setUp(self):
        User = get_user_model()
        suffix = uuid.uuid4().hex[:8].upper()
        self.staff = User.objects.create_user(
            username=f"conc-profit-{suffix}", password="pw", is_staff=True
        )
        for codename, app in (
            ("post_movement", "inventory"),
            ("create_shipment", "sales"),
            ("confirm_sales_order", "sales"),
            ("calculate_monthly_profit", "costing"),
            ("finalize_monthly_profit", "costing"),
        ):
            self.staff.user_permissions.add(
                Permission.objects.get(codename=codename, content_type__app_label=app)
            )

        self.pcs = UnitOfMeasure.objects.create(code=f"PC{suffix}", name_ar="قطعة", dimension="count")
        self.finished_cat = ProductCategory.objects.get_or_create(
            code="finished-goods", defaults={"name_ar": "منتج تام"}
        )[0]
        self.product = Product.objects.create(
            sku=f"FGP-{suffix}", name_ar="منتج أرباح", category=self.finished_cat,
            base_uom=self.pcs, available_to_customers=True,
        )
        self.location = StorageLocation.objects.create(
            warehouse=Warehouse.objects.create(code=f"WHP-{suffix}", name_ar="مخزن"),
            code="FG", name_ar="موقع التام",
        )
        post_movement(
            reference=f"OPENP-{suffix}", movement_type=StockMovement.Type.RECEIPT,
            product=self.product, quantity=Decimal("5000"), unit=self.pcs,
            actor=self.staff, destination_location=self.location,
        )
        self.customer = Customer.objects.create(
            code=f"CUSTP-{suffix}", name_ar="عميل", created_by=self.staff
        )

        self.year, self.month = 2025, 12
        # A costing source and full operating expenses make the month
        # finalizable, mirroring the documented profit workflow. Expenses are
        # recorded while the costing period is still a draft (MonthlyExpense
        # correctly refuses to post into a finalized costing month), and the
        # costing period is finalized afterwards so profit has an authoritative
        # COGS source.
        from datetime import date
        from calendar import monthrange
        start = date(self.year, self.month, 1)
        end = date(self.year, self.month, monthrange(self.year, self.month)[1])
        from costing.models import ProductCostingEntry
        cost = CostingPeriod.objects.create(
            year=self.year, month=self.month, start_date=start, end_date=end,
            status=CostingPeriod.Status.DRAFT, is_complete=True,
            labor_recorded=True, electricity_recorded=True, water_recorded=True,
            calculated_at=timezone.now(), calculated_by=self.staff,
        )
        ProductCostingEntry.objects.create(
            period=cost, product=self.product, good_units_produced=Decimal("1000"),
            actual_material_cost=Decimal("70000.00"),
            material_cost_assigned_to_good=Decimal("70000.00"),
            total_production_cost=Decimal("70000.00"),
            cost_per_good_unit=Decimal("70.0000"),
            material_cost_per_good_unit=Decimal("70.0000"),
            calculation_status="COMPLETE",
        )
        for expense_type, amount in (
            (MonthlyExpense.ExpenseType.LABOR, Decimal("30000")),
            (MonthlyExpense.ExpenseType.ELECTRICITY, Decimal("20000")),
            (MonthlyExpense.ExpenseType.MAINTENANCE, Decimal("10000")),
            (MonthlyExpense.ExpenseType.OTHER_INDUSTRIAL, Decimal("10000")),
        ):
            MonthlyExpense.objects.create(
                year=self.year, month=self.month, expense_type=expense_type,
                scope=MonthlyExpense.AllocationScope.FACTORY, amount=amount,
                description=f"{expense_type} {self.year}/{self.month}",
                created_by=self.staff, updated_by=self.staff,
            )
        # Freeze the costing period now that expenses are recorded (queryset
        # update bypasses the service, exactly as a completed finalization
        # would leave the row).
        CostingPeriod.objects.filter(pk=cost.pk).update(
            status=CostingPeriod.Status.FINALIZED,
            finalized_at=timezone.now(), finalized_by=self.staff,
        )
        self._ship("SEED", Decimal("1000"), Decimal("120.00"))
        self._counter = 0

    def _fresh_actor(self):
        return get_user_model().objects.get(pk=self.staff.pk)

    def _ship(self, tag, quantity, price):
        from datetime import date
        order = save_sales_draft_order(
            actor=self.staff, customer=self.customer,
            order_date=date(self.year, self.month, 15),
            lines=[{
                "product": self.product, "quantity": quantity, "unit": self.pcs,
                "unit_price": price, "notes": "",
            }],
        )
        confirm_sales_order(order=order, actor=self.staff)
        order_line = order.lines.get()
        return create_shipment(
            order=order, actor=self.staff, reference=f"SHIP-{tag}-{uuid.uuid4().hex[:8]}",
            shipment_date=date(self.year, self.month, 15), source_location=self.location,
            lines=[{"order_line": order_line, "quantity": quantity, "unit": self.pcs}],
        )

    @skipUnlessDBFeature("has_select_for_update")
    def test_shipment_racing_profit_finalization_never_posted_into_finalized_month(self):
        period = calculate_monthly_profit(
            year=self.year, month=self.month, actor=self._fresh_actor()
        )
        self.assertTrue(period.is_complete, period.missing_data_notes)

        def do_shipment():
            self._ship("RACE", Decimal("10"), Decimal("120.00"))

        def do_finalize():
            finalize_monthly_profit(
                period=MonthlyProfitPeriod.objects.get(pk=period.pk), actor=self._fresh_actor()
            )

        outcomes = self.run_race(do_finalize, do_shipment)
        self.assertNotIn(
            "error", [o[0] for o in outcomes if isinstance(o, tuple)],
            f"a thread crashed instead of succeeding or rejecting: {outcomes}",
        )

        period.refresh_from_db()
        # Core invariant: if the month is finalized, any shipment that exists
        # must have been committed before finalization froze the month (so the
        # under-lock recalculation saw it). A shipment posted after the freeze
        # would have had to pass ensure_profit_period_open, which the advisory
        # lock prevents.
        if period.status == MonthlyProfitPeriod.Status.FINALIZED:
            latest_shipment_movement = StockMovement.objects.filter(
                source_reference_type="sales.shipment"
            ).order_by("-posted_at").first()
            if latest_shipment_movement is not None:
                self.assertLessEqual(
                    latest_shipment_movement.posted_at, period.finalized_at,
                    "a shipment committed after the month was finalized",
                )

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_first_profit_calculations_create_exactly_one_period(self):
        from datetime import date
        target = date(2026, 3, 1)
        self.assertFalse(
            MonthlyProfitPeriod.objects.filter(year=target.year, month=target.month).exists()
        )

        def calc_a():
            calculate_monthly_profit(year=target.year, month=target.month, actor=self._fresh_actor())

        def calc_b():
            calculate_monthly_profit(year=target.year, month=target.month, actor=self._fresh_actor())

        outcomes = self.run_race(calc_a, calc_b)
        self.assertNotIn(
            "error", [o[0] for o in outcomes if isinstance(o, tuple)],
            f"concurrent first profit calculation crashed: {outcomes}",
        )
        self.assertEqual(
            MonthlyProfitPeriod.objects.filter(year=target.year, month=target.month).count(), 1
        )

    @skipUnlessDBFeature("has_select_for_update")
    def test_shipment_into_finalized_profit_month_is_rejected_without_partial_post(self):
        period = calculate_monthly_profit(
            year=self.year, month=self.month, actor=self._fresh_actor()
        )
        finalize_monthly_profit(period=period, actor=self._fresh_actor())
        period.refresh_from_db()
        self.assertEqual(period.status, MonthlyProfitPeriod.Status.FINALIZED)

        from sales.models import Shipment
        shipments_before = Shipment.objects.count()
        movements_before = StockMovement.objects.count()
        with self.assertRaisesMessage(ValidationError, "بعد اعتماد نتيجة الشهر"):
            self._ship("LATE", Decimal("5"), Decimal("120.00"))
        self.assertEqual(Shipment.objects.count(), shipments_before)
        self.assertEqual(StockMovement.objects.count(), movements_before)
