import os
import secrets
import subprocess
import sys

from django.test import TestCase
from django.urls import reverse


class HomePageTests(TestCase):
    def test_home_page_uses_arabic_rtl_shell(self):
        response = self.client.get(reverse("core:home"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'lang="ar" dir="rtl"')
        self.assertContains(response, "bootstrap.rtl.min.css")


class SettingsSafetyTests(TestCase):
    def run_settings_import(self, code="import config.settings", unset=(), **environment):
        child_environment = os.environ.copy()
        for name in unset:
            child_environment.pop(name, None)
        child_environment.update(environment)
        child_environment["DJANGO_SETTINGS_MODULE"] = "config.settings"
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env=child_environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_postgres_engine_requires_explicit_connection_settings(self):
        result = self.run_settings_import(
            unset=("DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT"),
            DJANGO_DEBUG="true",
            DB_ENGINE="postgres",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set all required PostgreSQL settings", result.stderr)

    def test_unknown_database_engine_does_not_fall_back_to_sqlite(self):
        result = self.run_settings_import(
            DJANGO_DEBUG="true",
            DB_ENGINE="postgress",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DB_ENGINE must be either", result.stderr)

    def test_postgres_rejects_invalid_port(self):
        result = self.run_settings_import(
            DJANGO_DEBUG="true",
            DB_ENGINE="postgres",
            DB_NAME="test_database",
            DB_USER="test_role",
            DB_PASSWORD=secrets.token_urlsafe(32),
            DB_HOST="127.0.0.1",
            DB_PORT="not-a-port",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DB_PORT must be an integer", result.stderr)

    def test_non_debug_requires_secret_key(self):
        result = self.run_settings_import(
            unset=("DJANGO_SECRET_KEY",),
            DJANGO_DEBUG="false",
            DJANGO_ALLOWED_HOSTS="localhost",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set DJANGO_SECRET_KEY", result.stderr)

    def test_non_debug_requires_allowed_hosts(self):
        result = self.run_settings_import(
            unset=("DJANGO_ALLOWED_HOSTS",),
            DJANGO_DEBUG="false",
            DJANGO_SECRET_KEY=secrets.token_urlsafe(50),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set DJANGO_ALLOWED_HOSTS", result.stderr)

    def test_non_debug_rejects_sqlite(self):
        result = self.run_settings_import(
            DJANGO_DEBUG="false",
            DJANGO_SECRET_KEY=secrets.token_urlsafe(50),
            DJANGO_ALLOWED_HOSTS="localhost",
            DB_ENGINE="sqlite",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SQLite is for local development and tests", result.stderr)

    def test_non_debug_enables_https_redirect_and_secure_cookies(self):
        result = self.run_settings_import(
            code=(
                "import config.settings as s; "
                "assert s.SECURE_SSL_REDIRECT and s.SESSION_COOKIE_SECURE and s.CSRF_COOKIE_SECURE"
            ),
            DJANGO_DEBUG="false",
            DJANGO_SECRET_KEY=secrets.token_urlsafe(50),
            DJANGO_ALLOWED_HOSTS="localhost",
            DB_ENGINE="postgres",
            DB_NAME="test_database",
            DB_USER="test_role",
            DB_PASSWORD=secrets.token_urlsafe(32),
            DB_HOST="127.0.0.1",
            DB_PORT="5432",
        )

        self.assertEqual(result.returncode, 0, result.stderr)


import uuid
from datetime import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product, ProductCategory, UnitOfMeasure
from costing.models import CostingPeriod, MonthlyExpense, MonthlyProfitPeriod
from costing.profit_services import (
    finalize_monthly_profit,
    reopen_monthly_profit,
    reverse_monthly_expense,
)
from costing.services import finalize_costing_period, reopen_costing_period
from inventory.models import StockLedgerEntry, StockMovement, StorageLocation, Warehouse
from inventory.services import post_movement, reverse_movement
from manufacturing.models import (
    BOMComponent,
    BillOfMaterials,
    MaterialIssue,
    ProductionLine,
    ProductionOperation,
    ProductionOrder,
    ProductionResult,
    Routing,
    RoutingStep,
)
from manufacturing.services import (
    issue_material,
    receive_finished_goods,
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


class AuthorizationBoundaryTests(TestCase):
    """End-to-end authorization for every sensitive write entry point.

    Each high-risk operation is exercised against four caller types: an
    anonymous user, an authenticated non-staff user, a staff user missing the
    specific permission, and a staff user holding it. For every denied caller
    the test asserts both that the call raises and that no database row was
    created, updated, or deleted. The non-staff caller is deliberately granted
    the permission so the test proves ``is_staff`` and a Django permission are
    not interchangeable in either direction.
    """

    def _perm(self, codename, app):
        return Permission.objects.get(codename=codename, content_type__app_label=app)

    def setUp(self):
        User = get_user_model()
        suffix = uuid.uuid4().hex[:8].upper()

        # Four caller types. The anonymous and non-staff users are never given
        # write permission except where a test explicitly isolates one check.
        self.anonymous = None  # passed as AnonymousUser-like sentinel below
        self.nonstaff = User.objects.create_user(
            username=f"authz-nonstaff-{suffix}", password="pw"
        )
        self.staff_no_perm = User.objects.create_user(
            username=f"authz-staffnp-{suffix}", password="pw", is_staff=True
        )
        self.authorized = User.objects.create_user(
            username=f"authz-authorized-{suffix}", password="pw", is_staff=True
        )

        from django.contrib.auth.models import AnonymousUser
        self.anonymous = AnonymousUser()

        # The authorized positive-path user doubles as the fixture builder, so
        # it holds the permissions needed to construct state (receiving stock,
        # releasing orders, confirming sales). The denial callers (anonymous,
        # non-staff, staff-without-permission) hold none of these.
        for codename, app in (
            ("post_movement", "inventory"),
            ("release_order", "manufacturing"),
            ("confirm_sales_order", "sales"),
        ):
            self._grant(self.authorized, codename, app)

        # --- catalog ---
        self.pcs = UnitOfMeasure.objects.create(
            code=f"PC{suffix}", name_ar="قطعة", dimension="count"
        )
        self.kg = UnitOfMeasure.objects.create(
            code=f"KG{suffix}", name_ar="كجم", dimension="mass"
        )
        self.finished_cat = ProductCategory.objects.get_or_create(
            code="finished-goods", defaults={"name_ar": "منتج تام"}
        )[0]
        self.raw_cat = ProductCategory.objects.get_or_create(
            code="raw-material", defaults={"name_ar": "مادة خام"}
        )[0]
        self.product = Product.objects.create(
            sku=f"FG-{suffix}", name_ar="منتج", category=self.finished_cat, base_uom=self.pcs,
            available_to_customers=True,
        )
        self.material = Product.objects.create(
            sku=f"RM-{suffix}", name_ar="مادة", category=self.raw_cat, base_uom=self.kg
        )

        # --- inventory ---
        wh = Warehouse.objects.create(code=f"WH-{suffix}", name_ar="مخزن")
        self.raw_location = StorageLocation.objects.create(
            warehouse=wh, code="RAW", name_ar="موقع الخام"
        )
        self.fg_location = StorageLocation.objects.create(
            warehouse=wh, code="FG", name_ar="موقع التام"
        )
        self.actor = self.authorized  # fixture builder uses the privileged user
        self._receive_material("100")
        self._receive_finished("50")

        # --- manufacturing master data & a released order ---
        self.line = ProductionLine.objects.get_or_create(
            code=f"LINE-{suffix}", defaults={"name_ar": "خط"}
        )[0]
        self.press = ProductionOperation.objects.get_or_create(
            code=f"OP-{suffix}", defaults={"name_ar": "كبس"}
        )[0]
        self.bom = BillOfMaterials.objects.create(
            product=self.product, version=1, output_quantity=Decimal("100"),
            output_unit=self.pcs, created_by=self.authorized,
        )
        self.component = BOMComponent.objects.create(
            bom=self.bom, material=self.material, quantity=Decimal("12"),
            unit=self.kg, sequence=10,
        )
        self.routing = Routing.objects.create(
            product=self.product, production_line=self.line, version=1,
            created_by=self.authorized,
        )
        self.route_step = RoutingStep.objects.create(
            routing=self.routing, operation=self.press, sequence=1
        )
        self.order = save_draft_order(
            actor=self.authorized, product=self.product, production_line=self.line,
            bom=self.bom, routing=self.routing, source_location=self.raw_location,
            planned_quantity=Decimal("100"), planned_unit=self.pcs, notes="",
        )
        release_order(order=self.order, actor=self.authorized)
        self.requirement = self.order.requirements.get()

        # --- sales order (confirmed) for shipment ---
        self.customer = Customer.objects.create(
            code=f"CUST-{suffix}", name_ar="عميل", created_by=self.authorized
        )
        self.sales_order = save_sales_draft_order(
            actor=self.authorized, customer=self.customer,
            order_date=timezone.localdate(),
            lines=[{
                "product": self.product, "quantity": Decimal("5"),
                "unit": self.pcs, "unit_price": Decimal("10"), "notes": "",
            }],
        )
        confirm_sales_order(order=self.sales_order, actor=self.authorized)

        # --- costing & profit periods + an active expense (current month) ---
        today = timezone.localdate()
        self.year, self.month = today.year, today.month
        self.costing_period = CostingPeriod.objects.create(
            year=self.year, month=self.month, status=CostingPeriod.Status.DRAFT
        )
        self.profit_period = MonthlyProfitPeriod.objects.create(
            year=self.year, month=self.month, status=MonthlyProfitPeriod.Status.DRAFT
        )
        self.expense = MonthlyExpense.objects.create(
            year=self.year, month=self.month,
            expense_type=MonthlyExpense.ExpenseType.LABOR,
            scope=MonthlyExpense.AllocationScope.FACTORY,
            amount=Decimal("100.00"), description="alary",
            created_by=self.authorized, updated_by=self.authorized,
        )

    def _receive_material(self, qty):
        return post_movement(
            reference=f"AUTHZ-RM-{uuid.uuid4().hex[:10]}",
            movement_type=StockMovement.Type.RECEIPT, product=self.material,
            quantity=Decimal(qty), unit=self.kg, actor=self.authorized,
            destination_location=self.raw_location,
        )

    def _receive_finished(self, qty):
        return post_movement(
            reference=f"AUTHZ-FG-{uuid.uuid4().hex[:10]}",
            movement_type=StockMovement.Type.RECEIPT, product=self.product,
            quantity=Decimal(qty), unit=self.pcs, actor=self.authorized,
            destination_location=self.fg_location,
        )

    # --- helpers to grant a single permission to a caller ---

    def _grant(self, user, codename, app):
        user.user_permissions.add(self._perm(codename, app))
        # Clear every Django per-instance permission cache so a grant added
        # after a prior has_perm() call is visible to the same instance.
        for attr in ("_perm_cache", "_all_perm_cache", "_user_perm_cache", "_group_perm_cache"):
            user.__dict__.pop(attr, None)
        return user

    def _assert_denied_no_mutation(self, fn, *args, **kwargs):
        """Call fn, expect ValidationError, and assert nothing changed."""
        movements = StockMovement.objects.count()
        ledger = StockLedgerEntry.objects.count()
        issues = MaterialIssue.objects.count()
        results = ProductionResult.objects.count()
        shipments = self.sales_order.shipments.count()
        expenses = MonthlyExpense.objects.count()
        with self.assertRaises(ValidationError):
            fn(*args, **kwargs)
        self.assertEqual(StockMovement.objects.count(), movements, "stock movement was created")
        self.assertEqual(StockLedgerEntry.objects.count(), ledger, "ledger entry was created")
        self.assertEqual(MaterialIssue.objects.count(), issues, "material issue was created")
        self.assertEqual(ProductionResult.objects.count(), results, "production result was created")
        self.assertEqual(self.sales_order.shipments.count(), shipments, "shipment was created")
        self.assertEqual(MonthlyExpense.objects.count(), expenses, "expense row changed")

    # ---------------- inventory ----------------

    def test_post_movement_requires_staff_and_permission(self):
        call = dict(
            reference=f"AUTHZ-POST-{uuid.uuid4().hex[:8]}",
            movement_type=StockMovement.Type.ADJUSTMENT_OUT, product=self.material,
            quantity=Decimal("1"), unit=self.kg, source_location=self.raw_location,
            reason="تسوية",
        )
        # anonymous
        with self.assertRaises(ValidationError):
            post_movement(actor=self.anonymous, **call)
        # non-staff holding the permission → still denied (staff boundary)
        self._grant(self.nonstaff, "post_movement", "inventory")
        with self.assertRaises(ValidationError):
            post_movement(actor=self.nonstaff, **call)
        # staff missing the permission → denied (permission boundary)
        with self.assertRaises(ValidationError):
            post_movement(actor=self.staff_no_perm, **call)
        before = StockMovement.objects.count()
        # authorized staff
        self._grant(self.authorized, "post_movement", "inventory")
        post_movement(actor=self.authorized, **call)
        self.assertEqual(StockMovement.objects.count(), before + 1)

    def test_reverse_movement_requires_staff_and_permission_and_leaves_no_partial_post(self):
        movement = self._receive_material("5")
        self._grant(self.authorized, "reverse_movement", "inventory")
        # Build a second, reversible movement performed by authorized staff.
        target = self._receive_material("3")
        call = dict(movement=target, reference=f"AUTHZ-REV-{uuid.uuid4().hex[:8]}", reason="سبب")
        with self.assertRaises(ValidationError):
            reverse_movement(actor=self.anonymous, **call)
        self._grant(self.nonstaff, "reverse_movement", "inventory")
        with self.assertRaises(ValidationError):
            reverse_movement(actor=self.nonstaff, **call)
        with self.assertRaises(ValidationError):
            reverse_movement(actor=self.staff_no_perm, **call)
        # No reversal row and original movement untouched.
        self.assertFalse(hasattr(target, "reversal"))
        self.assertEqual(
            StockMovement.objects.filter(movement_type=StockMovement.Type.REVERSAL).count(), 0
        )
        reverse_movement(actor=self.authorized, **call)
        self.assertTrue(StockMovement.objects.filter(reversal_of=target).exists())

    # ---------------- manufacturing ----------------

    def test_issue_material_requires_staff_and_permission(self):
        call = dict(
            order=self.order, requirement=self.requirement, quantity=Decimal("1"),
            unit=self.kg, reference=f"AUTHZ-ISS-{uuid.uuid4().hex[:8]}",
        )
        with self.assertRaises(ValidationError):
            issue_material(actor=self.anonymous, **call)
        # non-staff holding BOTH the issue permission and the nested inventory
        # permission → still denied because staff is required.
        self._grant(self.nonstaff, "issue_material", "manufacturing")
        self._grant(self.nonstaff, "post_movement", "inventory")
        with self.assertRaises(ValidationError):
            issue_material(actor=self.nonstaff, **call)
        # staff missing issue permission (but holding inventory) → denied.
        self._grant(self.staff_no_perm, "post_movement", "inventory")
        with self.assertRaises(ValidationError):
            issue_material(actor=self.staff_no_perm, **call)
        before = MaterialIssue.objects.count()
        self._grant(self.authorized, "issue_material", "manufacturing")
        self._grant(self.authorized, "post_movement", "inventory")
        issue_material(actor=self.authorized, **call)
        self.assertEqual(MaterialIssue.objects.count(), before + 1)

    def test_record_production_result_requires_staff_and_permission(self):
        call = dict(
            order=self.order, good_quantity=Decimal("5"), scrap_quantity=Decimal("1"),
            unit=self.pcs, notes="", occurred_at=timezone.now(),
        )
        with self.assertRaises(ValidationError):
            record_production_result(actor=self.anonymous, **call)
        self._grant(self.nonstaff, "record_production_result", "manufacturing")
        with self.assertRaises(ValidationError):
            record_production_result(actor=self.nonstaff, **call)
        with self.assertRaises(ValidationError):
            record_production_result(actor=self.staff_no_perm, **call)
        before = ProductionResult.objects.count()
        self._grant(self.authorized, "record_production_result", "manufacturing")
        record_production_result(actor=self.authorized, **call)
        self.assertEqual(ProductionResult.objects.count(), before + 1)

    def test_receive_finished_goods_requires_staff_and_permission(self):
        # Receiving is capped by recorded good output, so record a result first
        # as the authorized user to make the success path meaningful.
        self._grant(self.authorized, "record_production_result", "manufacturing")
        record_production_result(
            actor=self.authorized, order=self.order, good_quantity=Decimal("10"),
            scrap_quantity=Decimal("0"), unit=self.pcs, notes="", occurred_at=timezone.now(),
        )
        call = dict(
            order=self.order, quantity=Decimal("2"), unit=self.pcs,
            destination_location=self.fg_location,
            reference=f"AUTHZ-RCV-{uuid.uuid4().hex[:8]}",
        )
        with self.assertRaises(ValidationError):
            receive_finished_goods(actor=self.anonymous, **call)
        self._grant(self.nonstaff, "receive_finished_goods", "manufacturing")
        self._grant(self.nonstaff, "post_movement", "inventory")
        with self.assertRaises(ValidationError):
            receive_finished_goods(actor=self.nonstaff, **call)
        self._grant(self.staff_no_perm, "post_movement", "inventory")
        with self.assertRaises(ValidationError):
            receive_finished_goods(actor=self.staff_no_perm, **call)
        before = StockMovement.objects.count()
        self._grant(self.authorized, "receive_finished_goods", "manufacturing")
        self._grant(self.authorized, "post_movement", "inventory")
        receive_finished_goods(actor=self.authorized, **call)
        self.assertGreater(StockMovement.objects.count(), before)

    # ---------------- sales ----------------

    def test_create_shipment_requires_staff_and_permission(self):
        order_line = self.sales_order.lines.get()
        call = dict(
            order=self.sales_order, actor=None,
            reference=f"AUTHZ-SHIP-{uuid.uuid4().hex[:8]}",
            shipment_date=timezone.localdate(), source_location=self.fg_location,
            lines=[{"order_line": order_line, "quantity": Decimal("1"), "unit": self.pcs}],
        )

        def attempt(actor):
            payload = dict(call)
            payload["actor"] = actor
            payload["reference"] = f"AUTHZ-SHIP-{uuid.uuid4().hex[:8]}"
            return create_shipment(**payload)

        with self.assertRaises(ValidationError):
            attempt(self.anonymous)
        self._grant(self.nonstaff, "create_shipment", "sales")
        self._grant(self.nonstaff, "post_movement", "inventory")
        with self.assertRaises(ValidationError):
            attempt(self.nonstaff)
        self._grant(self.staff_no_perm, "post_movement", "inventory")
        with self.assertRaises(ValidationError):
            attempt(self.staff_no_perm)
        before = self.sales_order.shipments.count()
        self._grant(self.authorized, "create_shipment", "sales")
        self._grant(self.authorized, "post_movement", "inventory")
        attempt(self.authorized)
        self.assertEqual(self.sales_order.shipments.count(), before + 1)

    # ---------------- costing periods ----------------

    def test_finalize_costing_period_requires_staff_and_permission(self):
        def attempt(actor):
            finalize_costing_period(period=self.costing_period, actor=actor)

        with self.assertRaises(ValidationError):
            attempt(self.anonymous)
        self._grant(self.nonstaff, "finalize_costing_period", "costing")
        with self.assertRaises(ValidationError):
            attempt(self.nonstaff)
        with self.assertRaises(ValidationError):
            attempt(self.staff_no_perm)
        self.costing_period.refresh_from_db()
        self.assertEqual(self.costing_period.status, CostingPeriod.Status.DRAFT)
        # The authorized staff user clears the permission gate. The period is
        # incomplete, so business rules may still stop it — but never a
        # permission error, and never a state change to FINALIZED.
        self._grant(self.authorized, "finalize_costing_period", "costing")
        try:
            attempt(self.authorized)
        except ValidationError as exc:
            self.assertNotIn("ليس لديك صلاحية", " ".join(exc.messages))
        self.costing_period.refresh_from_db()
        self.assertEqual(self.costing_period.status, CostingPeriod.Status.DRAFT)

    def test_reopen_costing_period_requires_staff_and_permission(self):
        self.costing_period.status = CostingPeriod.Status.FINALIZED
        self.costing_period.save()
        call_kwargs = dict(reason="إعادة")

        def attempt(actor):
            reopen_costing_period(period=self.costing_period, actor=actor, **call_kwargs)

        with self.assertRaises(ValidationError):
            attempt(self.anonymous)
        self._grant(self.nonstaff, "reopen_costing_period", "costing")
        with self.assertRaises(ValidationError):
            attempt(self.nonstaff)
        with self.assertRaises(ValidationError):
            attempt(self.staff_no_perm)
        # Still finalized → denial did not reopen it.
        self.costing_period.refresh_from_db()
        self.assertEqual(self.costing_period.status, CostingPeriod.Status.FINALIZED)
        self._grant(self.authorized, "reopen_costing_period", "costing")
        reopen_costing_period(period=self.costing_period, actor=self.authorized, **call_kwargs)
        self.costing_period.refresh_from_db()
        self.assertEqual(self.costing_period.status, CostingPeriod.Status.DRAFT)

    # ---------------- monthly profit ----------------

    def test_finalize_monthly_profit_requires_staff_and_permission(self):
        def attempt(actor):
            finalize_monthly_profit(period=self.profit_period, actor=actor)

        with self.assertRaises(ValidationError):
            attempt(self.anonymous)
        self._grant(self.nonstaff, "finalize_monthly_profit", "costing")
        with self.assertRaises(ValidationError):
            attempt(self.nonstaff)
        with self.assertRaises(ValidationError):
            attempt(self.staff_no_perm)
        self.profit_period.refresh_from_db()
        self.assertEqual(self.profit_period.status, MonthlyProfitPeriod.Status.DRAFT)
        # Finalization recalculates the month first, which carries its own
        # permission; grant it so the authorized user reaches the business gate.
        self._grant(self.authorized, "finalize_monthly_profit", "costing")
        self._grant(self.authorized, "calculate_monthly_profit", "costing")
        try:
            attempt(self.authorized)
        except ValidationError as exc:
            self.assertNotIn("ليس لديك صلاحية", " ".join(exc.messages))
        self.profit_period.refresh_from_db()
        self.assertEqual(self.profit_period.status, MonthlyProfitPeriod.Status.DRAFT)

    def test_reopen_monthly_profit_requires_staff_and_permission(self):
        # The model blocks direct ORM transitions to FINALIZED; set the state up
        # through a queryset update the way an earlier finalization would leave it.
        MonthlyProfitPeriod.objects.filter(pk=self.profit_period.pk).update(
            status=MonthlyProfitPeriod.Status.FINALIZED, finalized_at=timezone.now()
        )
        self.profit_period.refresh_from_db()

        def attempt(actor):
            reopen_monthly_profit(period=self.profit_period, actor=actor, reason="إعادة")

        with self.assertRaises(ValidationError):
            attempt(self.anonymous)
        self._grant(self.nonstaff, "reopen_monthly_profit", "costing")
        with self.assertRaises(ValidationError):
            attempt(self.nonstaff)
        with self.assertRaises(ValidationError):
            attempt(self.staff_no_perm)
        self.profit_period.refresh_from_db()
        self.assertEqual(self.profit_period.status, MonthlyProfitPeriod.Status.FINALIZED)
        self._grant(self.authorized, "reopen_monthly_profit", "costing")
        reopen_monthly_profit(period=self.profit_period, actor=self.authorized, reason="إعادة")
        self.profit_period.refresh_from_db()
        self.assertEqual(self.profit_period.status, MonthlyProfitPeriod.Status.DRAFT)

    def test_reverse_monthly_expense_requires_staff_and_permission(self):
        def attempt(actor):
            reverse_monthly_expense(expense=self.expense, actor=actor, reason="سبب")

        with self.assertRaises(ValidationError):
            attempt(self.anonymous)
        self._grant(self.nonstaff, "manage_expenses", "costing")
        with self.assertRaises(ValidationError):
            attempt(self.nonstaff)
        with self.assertRaises(ValidationError):
            attempt(self.staff_no_perm)
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.status, MonthlyExpense.Status.ACTIVE)
        self._grant(self.authorized, "manage_expenses", "costing")
        reverse_monthly_expense(expense=self.expense, actor=self.authorized, reason="سبب")
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.status, MonthlyExpense.Status.REVERSED)

    # ---------------- expense write views ----------------

    def test_expense_views_require_staff_and_manage_expenses_permission(self):
        payload = {
            "year": str(self.year), "month": str(self.month),
            "expense_type": MonthlyExpense.ExpenseType.ELECTRICITY,
            "scope": MonthlyExpense.AllocationScope.FACTORY,
            "amount": "250.00", "description": "فاتورة",
        }
        add_url = reverse("costing:expense_create")

        # anonymous → redirect to login, no row
        before = MonthlyExpense.objects.count()
        self.assertEqual(self.client.post(add_url, payload, secure=True).status_code, 302)
        self.assertEqual(MonthlyExpense.objects.count(), before)

        # authenticated non-staff holding the permission → forbidden (staff boundary)
        self._grant(self.nonstaff, "manage_expenses", "costing")
        self.client.force_login(self.nonstaff)
        self.assertEqual(self.client.post(add_url, payload, secure=True).status_code, 403)
        self.assertEqual(MonthlyExpense.objects.count(), before)

        # staff missing the permission → forbidden (permission boundary)
        self.client.force_login(self.staff_no_perm)
        self.assertEqual(self.client.post(add_url, payload, secure=True).status_code, 403)
        self.assertEqual(MonthlyExpense.objects.count(), before)

        # staff with the permission → created
        self._grant(self.authorized, "manage_expenses", "costing")
        self.client.force_login(self.authorized)
        self.assertEqual(self.client.post(add_url, payload, secure=True).status_code, 302)
        self.assertEqual(MonthlyExpense.objects.count(), before + 1)

    def test_expense_edit_and_reverse_views_require_permission(self):
        self._grant(self.authorized, "manage_expenses", "costing")
        edit_url = reverse("costing:expense_edit", args=[self.expense.pk])
        rev_url = reverse("costing:expense_reverse", args=[self.expense.pk])

        # staff without permission → forbidden on both
        self.client.force_login(self.staff_no_perm)
        self.assertEqual(self.client.get(edit_url, secure=True).status_code, 403)
        self.assertEqual(
            self.client.post(rev_url, {"reason": "سبب"}, secure=True).status_code, 403
        )
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.status, MonthlyExpense.Status.ACTIVE)

        # authorized staff can reach the reverse form and reverse it
        self.client.force_login(self.authorized)
        self.assertEqual(
            self.client.post(rev_url, {"reason": "سبب موثق"}, secure=True).status_code, 302
        )
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.status, MonthlyExpense.Status.REVERSED)

    # ---------------- read boundary ----------------

    def test_costing_financial_read_requires_staff_or_finance_permission(self):
        url = reverse("costing:period_list")
        # anonymous → login redirect
        self.assertEqual(self.client.get(url, secure=True).status_code, 302)
        # plain authenticated → forbidden
        self.client.force_login(self.nonstaff)
        self.assertEqual(self.client.get(url, secure=True).status_code, 403)
        # non-staff with the finance view permission → allowed (read only)
        self._grant(self.nonstaff, "view_costing_financials", "costing")
        self.client.force_login(self.nonstaff)
        self.assertEqual(self.client.get(url, secure=True).status_code, 200)
        # but that read permission must NOT unlock writes
        add_url = reverse("costing:expense_create")
        self.assertEqual(
            self.client.post(add_url, {
                "year": str(self.year), "month": str(self.month),
                "expense_type": MonthlyExpense.ExpenseType.WATER,
                "scope": MonthlyExpense.AllocationScope.FACTORY,
                "amount": "10", "description": "x",
            }, secure=True).status_code, 403
        )
