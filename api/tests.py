from datetime import date, datetime, timedelta
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.generators import SchemaGenerator
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from catalog.models import Product, ProductCategory, ProductUnitConversion, UnitOfMeasure
from costing.models import CostingPeriod, ProductCostingEntry, ProductSellingPrice
from inventory.models import StockMovement, StorageLocation, Warehouse
from inventory.services import balance_for, post_movement, reverse_movement
from manufacturing.models import ProductionLine, ProductionOrder, ProductionResult
from purchasing.models import Supplier
from purchasing.services import (
    confirm_order as confirm_purchase_order,
    create_receipt,
    save_draft_order as save_purchase_draft,
)
from sales.models import Customer, SalesOrder, Shipment
from sales.services import (
    cancel_order as cancel_sales_order,
    confirm_order as confirm_sales_order,
    create_shipment,
    save_draft_order as save_sales_draft,
)


class APITestCase(TestCase):
    def setUp(self):
        self.today = date.today()
        self.suffix = uuid.uuid4().hex[:8]
        User = get_user_model()
        self.owner = User.objects.create_user(
            username=f"owner-{self.suffix}",
            password="owner-test-password",
            is_staff=True,
        )
        self.customer_user = User.objects.create_user(
            username=f"customer-{self.suffix}",
            password="customer-test-password",
        )
        self.other_customer_user = User.objects.create_user(
            username=f"other-{self.suffix}",
            password="other-test-password",
        )
        self.unlinked_user = User.objects.create_user(
            username=f"unlinked-{self.suffix}",
            password="unlinked-test-password",
        )
        self.inactive_customer_user = User.objects.create_user(
            username=f"inactive-{self.suffix}",
            password="inactive-test-password",
        )

        self.pcs = UnitOfMeasure.objects.create(
            code=f"PCS{self.suffix}",
            name_ar="قطعة",
            name_en="Piece",
            dimension="count",
        )
        self.box = UnitOfMeasure.objects.create(
            code=f"BOX{self.suffix}",
            name_ar="كرتونة",
            name_en="Box",
            dimension="count",
        )
        self.kg = UnitOfMeasure.objects.create(
            code=f"KG{self.suffix}",
            name_ar="كيلو",
            name_en="Kilogram",
            dimension="weight",
        )
        self.category, _ = ProductCategory.objects.get_or_create(
            code="finished-goods",
            defaults={"name_ar": "منتجات تامة", "name_en": "Finished goods", "active": True},
        )
        self.product = Product.objects.create(
            sku=f"FG-{self.suffix}",
            name_ar="كوب ورقي",
            name_en="Paper cup",
            category=self.category,
            base_uom=self.pcs,
            available_to_customers=True,
            low_stock_threshold=Decimal("150"),
        )
        self.product2 = Product.objects.create(
            sku=f"FG2-{self.suffix}",
            name_ar="علبة ورقية",
            name_en="Paper container",
            category=self.category,
            base_uom=self.pcs,
            available_to_customers=True,
        )
        self.internal_product = Product.objects.create(
            sku=f"RAW-{self.suffix}",
            name_ar="مادة داخلية",
            name_en="Internal material",
            category=self.category,
            base_uom=self.pcs,
            available_to_customers=False,
        )
        ProductUnitConversion.objects.create(
            product=self.product,
            unit=self.box,
            factor=Decimal("12"),
        )
        ProductSellingPrice.objects.create(
            product=self.product,
            selling_price=Decimal("12.5000"),
            unit=self.pcs,
            updated_by=self.owner,
        )

        self.warehouse = Warehouse.objects.create(
            code=f"WH-{self.suffix}",
            name_ar="المخزن الرئيسي",
        )
        self.location = StorageLocation.objects.create(
            warehouse=self.warehouse,
            code=f"LOC-{self.suffix}",
            name_ar="موقع الإنتاج",
        )
        self._opening(self.product, Decimal("100"), f"OPEN-{self.suffix}-1")
        self._opening(self.product2, Decimal("50"), f"OPEN-{self.suffix}-2")

        self.customer = Customer.objects.create(
            code=f"C-{self.suffix}",
            name_ar="عميل الجملة",
            email="customer@example.invalid",
            user=self.customer_user,
            created_by=self.owner,
            notes="ملاحظة داخلية لا يجب أن تظهر للعميل",
        )
        self.other_customer = Customer.objects.create(
            code=f"C2-{self.suffix}",
            name_ar="عميل آخر",
            user=self.other_customer_user,
            created_by=self.owner,
        )
        self.inactive_customer = Customer.objects.create(
            code=f"C3-{self.suffix}",
            name_ar="عميل غير نشط",
            user=self.inactive_customer_user,
            created_by=self.owner,
            active=False,
        )
        self.supplier = Supplier.objects.create(
            code=f"S-{self.suffix}",
            name_ar="مورد الورق",
            created_by=self.owner,
        )
        self.production_line = ProductionLine.objects.create(
            code=f"line-{self.suffix}",
            name_ar="خط الأكواب",
        )

        self.anon = APIClient()
        self.owner_client = self._client_for(self.owner)
        self.customer_client = self._client_for(self.customer_user)
        self.other_customer_client = self._client_for(self.other_customer_user)
        self.unlinked_client = self._client_for(self.unlinked_user)
        self.inactive_customer_client = self._client_for(self.inactive_customer_user)

    def _client_for(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        return client

    def _opening(self, product, quantity, reference):
        post_movement(
            reference=reference,
            movement_type=StockMovement.Type.OPENING,
            product=product,
            quantity=quantity,
            unit=self.pcs,
            actor=self.owner,
            destination_location=self.location,
        )

    def _create_sales_order(self, *, customer=None, product=None, quantity=Decimal("10"), price=Decimal("2.0000"),
                            confirm=False, ship=False):
        order = save_sales_draft(
            actor=self.owner,
            customer=customer or self.customer,
            order_date=self.today,
            lines=[{
                "product": product or self.product,
                "quantity": quantity,
                "unit": self.pcs,
                "unit_price": price,
                "notes": "",
            }],
        )
        if confirm:
            order = confirm_sales_order(order=order, actor=self.owner)
        if ship:
            create_shipment(
                order=order,
                actor=self.owner,
                reference=f"SAL-{uuid.uuid4().hex[:16]}",
                shipment_date=self.today,
                source_location=self.location,
                lines=[{
                    "order_line": order.lines.get(),
                    "quantity": quantity,
                    "unit": self.pcs,
                }],
            )
            order.refresh_from_db()
        return order

    def _create_purchase_order(self):
        order = save_purchase_draft(
            actor=self.owner,
            supplier=self.supplier,
            order_date=self.today,
            lines=[{
                "material": self.product2,
                "quantity": Decimal("100"),
                "unit": self.pcs,
                "unit_price": Decimal("1.5000"),
                "notes": "",
            }],
        )
        return confirm_purchase_order(order=order, actor=self.owner)

    def _create_production(self):
        order = ProductionOrder.objects.create(
            order_number=f"PRD-{self.suffix}",
            product=self.product2,
            production_line=self.production_line,
            source_location=self.location,
            planned_quantity=Decimal("20"),
            planned_unit=self.pcs,
            planned_quantity_base=Decimal("20"),
            base_unit_snapshot=self.pcs,
            status=ProductionOrder.Status.IN_PROGRESS,
            created_by=self.owner,
        )
        ProductionResult.objects.create(
            order=order,
            good_quantity=Decimal("8"),
            scrap_quantity=Decimal("2"),
            unit=self.pcs,
            good_quantity_base=Decimal("8"),
            scrap_quantity_base=Decimal("2"),
            actor=self.owner,
        )
        return order

    def _create_costing(self):
        period = CostingPeriod.objects.create(
            year=self.today.year,
            month=self.today.month,
            status=CostingPeriod.Status.DRAFT,
            labor_recorded=True,
            labor_total=Decimal("10.00"),
            electricity_total=Decimal("5.00"),
            water_total=Decimal("2.00"),
            other_industrial_total=Decimal("3.00"),
            materials_total=Decimal("80.00"),
            total_production_cost=Decimal("100.00"),
            is_complete=True,
        )
        ProductCostingEntry.objects.create(
            period=period,
            product=self.product,
            production_line=self.production_line,
            good_units_produced=Decimal("8.0000"),
            scrap_units_produced=Decimal("2.0000"),
            actual_material_cost=Decimal("80.00"),
            material_cost_assigned_to_good=Decimal("80.00"),
            waste_material_cost=Decimal("0.00"),
            allocated_labor_cost=Decimal("10.00"),
            allocated_electricity_cost=Decimal("5.00"),
            allocated_water_cost=Decimal("2.00"),
            allocated_other_cost=Decimal("3.00"),
            total_production_cost=Decimal("100.00"),
            cost_per_good_unit=Decimal("12.5000"),
            selling_price_snapshot=Decimal("2.0000"),
            calculation_status="COMPLETE",
        )
        return period

    def _build_operational_fixture(self):
        self._create_sales_order(confirm=True, ship=True)
        self._create_purchase_order()
        self._create_production()
        self._create_costing()

    def _period_params(self):
        return {
            "start_date": self.today.replace(day=1).isoformat(),
            "end_date": self.today.isoformat(),
        }

    def _order_request(self, **overrides):
        payload = {
            "requested_delivery_date": (self.today + timedelta(days=3)).isoformat(),
            "customer_reference": "WEB-REF-1",
            "notes": "طلب عميل",
            "lines": [{
                "product": self.product2.pk,
                "quantity": "5.000000000000",
                "unit": self.pcs.pk,
                "notes": "بند طلب",
            }],
        }
        payload.update(overrides)
        return payload


class APIUSAuthAndPermissionTests(APITestCase):
    def test_token_endpoint_and_unauthenticated_error_envelope(self):
        Token.objects.filter(user=self.customer_user).delete()
        response = self.anon.post(
            reverse("api:token"),
            {"username": self.customer_user.username, "password": "customer-test-password"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.json())

        response = self.anon.get(reverse("api:customer-profile"))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["status"], 401)

    def test_invalid_token_credentials_are_rejected(self):
        response = self.anon.post(
            reverse("api:token"),
            {"username": self.customer_user.username, "password": "wrong-password"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_customer_profile_and_role_boundaries(self):
        response = self.customer_client.get(reverse("api:customer-profile"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["code"], self.customer.code)
        self.assertNotIn("notes", response.json())
        self.assertNotIn("user", response.json())

        self.assertEqual(self.owner_client.get(reverse("api:customer-profile")).status_code, 403)
        self.assertEqual(self.unlinked_client.get(reverse("api:customer-profile")).status_code, 403)
        self.assertEqual(self.inactive_customer_client.get(reverse("api:customer-profile")).status_code, 403)
        self.assertEqual(self.customer_client.get(reverse("api:owner-dashboard")).status_code, 403)
        self.assertEqual(self.owner_client.get(reverse("api:customer-products")).status_code, 403)

    def test_token_logout_revokes_current_token(self):
        response = self.customer_client.post(reverse("api:logout"), format="json")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.customer_client.get(reverse("api:customer-profile")).status_code, 401)

    def test_schema_and_docs_are_staff_only(self):
        schema_response = self.owner_client.get(reverse("api:schema"))
        self.assertEqual(schema_response.status_code, 200)
        schema = SchemaGenerator().get_schema(request=None, public=True)
        token_schema = schema["components"]["schemas"]["TokenResponse"]
        self.assertEqual(token_schema["type"], "object")
        self.assertEqual(token_schema["properties"]["token"]["type"], "string")
        self.assertEqual(self.customer_client.get(reverse("api:schema")).status_code, 403)
        self.assertEqual(self.owner_client.get(reverse("api:docs")).status_code, 200)

        session_client = APIClient()
        session_client.force_login(self.owner)
        self.assertEqual(session_client.get(reverse("api:schema")).status_code, 200)
        self.assertEqual(session_client.get(reverse("api:docs")).status_code, 200)

        customer_session_client = APIClient()
        customer_session_client.force_login(self.customer_user)
        self.assertEqual(customer_session_client.get(reverse("api:schema")).status_code, 403)
        self.assertEqual(customer_session_client.get(reverse("api:docs")).status_code, 403)


class CustomerCatalogAPITests(APITestCase):
    def test_catalog_excludes_internal_products_and_internal_fields(self):
        response = self.customer_client.get(reverse("api:customer-products"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 2)
        skus = {row["sku"] for row in data["results"]}
        self.assertEqual(skus, {self.product.sku, self.product2.sku})
        self.assertNotIn(self.internal_product.sku, skus)
        for row in data["results"]:
            self.assertNotIn("low_stock_threshold", row)
            self.assertNotIn("available_to_customers", row)
            self.assertNotIn("selling_price_info", row)
            self.assertNotIn("cost", row)
        product_row = next(row for row in data["results"] if row["sku"] == self.product.sku)
        self.assertEqual(product_row["reference_price"]["amount"], "12.5000")
        self.assertEqual(product_row["reference_price"]["price_type"], "OWNER_REFERENCE_PRICE")

        detail = self.customer_client.get(
            reverse("api:customer-product-detail", args=[self.product.pk])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(
            self.customer_client.get(
                reverse("api:customer-product-detail", args=[self.internal_product.pk])
            ).status_code,
            404,
        )

    def test_prices_endpoint_only_returns_authorized_reference_prices(self):
        response = self.customer_client.get(reverse("api:customer-prices"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["sku"], self.product.sku)
        self.assertEqual(data["results"][0]["reference_price"]["amount"], "12.5000")

    def test_catalog_pagination_and_invalid_page(self):
        response = self.customer_client.get(reverse("api:customer-products"), {"page_size": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 2)
        self.assertEqual(len(response.json()["results"]), 1)

        response = self.customer_client.get(reverse("api:customer-products"), {"page": "not-a-number"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["status"], 404)


class CustomerOrderAPITests(APITestCase):
    def test_customer_order_list_and_detail_are_isolated(self):
        own = self._create_sales_order()
        other = self._create_sales_order(customer=self.other_customer, product=self.product2)

        response = self.customer_client.get(reverse("api:customer-orders"))
        self.assertEqual(response.status_code, 200)
        numbers = {row["order_number"] for row in response.json()["results"]}
        self.assertEqual(numbers, {own.order_number})
        self.assertNotIn(other.order_number, numbers)

        own_response = self.customer_client.get(
            reverse("api:customer-order-detail", args=[own.order_number])
        )
        self.assertEqual(own_response.status_code, 200)
        self.assertEqual(own_response.json()["order_number"], own.order_number)
        self.assertEqual(
            self.customer_client.get(
                reverse("api:customer-order-detail", args=[other.order_number])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.other_customer_client.get(
                reverse("api:customer-order-detail", args=[own.order_number])
            ).status_code,
            404,
        )

    def test_customer_order_filter_rejects_invalid_status(self):
        response = self.customer_client.get(reverse("api:customer-orders"), {"status": "INVALID"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["status"], 400)

    def test_order_request_creates_unconfirmed_zero_price_draft_without_inventory_effect(self):
        before_movements = StockMovement.objects.count()
        before_shipments = Shipment.objects.count()
        before_balance = balance_for(self.product2, self.location)
        before_orders = SalesOrder.objects.count()

        response = self.customer_client.post(
            reverse("api:customer-order-requests"),
            self._order_request(),
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["status"], SalesOrder.Status.DRAFT)
        self.assertTrue(data["customer_submitted"])
        self.assertEqual(data["pricing_status"], "PENDING_REVIEW")
        self.assertIsNone(data["lines"][0]["unit_price"])
        self.assertIsNone(data["lines"][0]["line_total"])
        self.assertTrue(data["lines"][0]["price_pending"])

        order = SalesOrder.objects.get(order_number=data["order_number"])
        self.assertEqual(order.customer_id, self.customer.pk)
        self.assertEqual(order.created_by_id, self.customer_user.pk)
        self.assertEqual(order.status, SalesOrder.Status.DRAFT)
        self.assertIsNone(order.confirmed_at)
        self.assertTrue(order.customer_submitted)
        self.assertEqual(order.lines.get().unit_price, Decimal("0"))
        self.assertEqual(order.lines.get().quantity_base, Decimal("5.000000000000"))
        self.assertEqual(StockMovement.objects.count(), before_movements)
        self.assertEqual(Shipment.objects.count(), before_shipments)
        self.assertEqual(balance_for(self.product2, self.location), before_balance)
        self.assertEqual(SalesOrder.objects.count(), before_orders + 1)

    def test_customer_request_cannot_be_confirmed_or_shipped_until_priced(self):
        response = self.customer_client.post(
            reverse("api:customer-order-requests"),
            self._order_request(),
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        order = SalesOrder.objects.get(order_number=response.json()["order_number"])
        order_line = order.lines.get()

        with self.assertRaises(ValidationError):
            confirm_sales_order(order=order, actor=self.owner)
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.DRAFT)
        self.assertIsNone(order.confirmed_at)
        self.assertEqual(order.pricing_status, "PENDING_REVIEW")

        # A direct status change simulates a legacy/bypassed confirmation. The
        # shipment service must still refuse to move stock for an unpriced
        # customer request.
        SalesOrder.objects.filter(pk=order.pk).update(status=SalesOrder.Status.CONFIRMED)
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.CONFIRMED)
        self.assertEqual(order.pricing_status, "UNPRICED")
        before_shipments = Shipment.objects.count()
        before_movements = StockMovement.objects.count()
        before_balance = balance_for(self.product2, self.location)
        with self.assertRaises(ValidationError):
            create_shipment(
                order=order,
                actor=self.owner,
                reference=f"SAL-UNPRICED-{uuid.uuid4().hex[:8]}",
                shipment_date=self.today,
                source_location=self.location,
                lines=[{
                    "order_line": order_line,
                    "quantity": Decimal("5"),
                    "unit": self.pcs,
                }],
            )
        self.assertEqual(Shipment.objects.count(), before_shipments)
        self.assertEqual(StockMovement.objects.count(), before_movements)
        self.assertEqual(balance_for(self.product2, self.location), before_balance)

    def test_customer_request_pricing_status_updates_after_staff_review(self):
        response = self.customer_client.post(
            reverse("api:customer-order-requests"),
            self._order_request(),
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        order = SalesOrder.objects.get(order_number=response.json()["order_number"])

        priced = save_sales_draft(
            actor=self.owner,
            order=order,
            lines=[{
                "product": self.product2,
                "quantity": Decimal("5"),
                "unit": self.pcs,
                "unit_price": Decimal("2.0000"),
                "notes": "",
            }],
        )
        self.assertEqual(priced.status, SalesOrder.Status.DRAFT)
        self.assertEqual(priced.pricing_status, "RECORDED")
        confirmed = confirm_sales_order(order=priced, actor=self.owner)
        self.assertEqual(confirmed.status, SalesOrder.Status.CONFIRMED)

        detail = self.customer_client.get(
            reverse("api:customer-order-detail", args=[confirmed.order_number])
        )
        self.assertEqual(detail.status_code, 200)
        data = detail.json()
        self.assertEqual(data["status"], SalesOrder.Status.CONFIRMED)
        self.assertEqual(data["pricing_status"], "RECORDED")
        self.assertEqual(data["lines"][0]["unit_price"], "2.0000")
        self.assertEqual(data["lines"][0]["line_total"], "10.00")
        self.assertFalse(data["lines"][0]["price_pending"])

    def test_order_request_rejects_privilege_fields_and_unavailable_products(self):
        response = self.customer_client.post(
            reverse("api:customer-order-requests"),
            self._order_request(customer=self.other_customer.pk),
            format="json",
        )
        self.assertEqual(response.status_code, 400)

        payload = self._order_request()
        payload["lines"][0]["unit_price"] = "1.0000"
        self.assertEqual(
            self.customer_client.post(
                reverse("api:customer-order-requests"), payload, format="json"
            ).status_code,
            400,
        )

        payload = self._order_request()
        payload["lines"][0]["product"] = self.internal_product.pk
        self.assertEqual(
            self.customer_client.post(
                reverse("api:customer-order-requests"), payload, format="json"
            ).status_code,
            400,
        )

        payload = self._order_request()
        payload["lines"][0]["unit"] = self.kg.pk
        self.assertEqual(
            self.customer_client.post(
                reverse("api:customer-order-requests"), payload, format="json"
            ).status_code,
            400,
        )

        payload = self._order_request()
        payload["lines"] = []
        self.assertEqual(
            self.customer_client.post(
                reverse("api:customer-order-requests"), payload, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(SalesOrder.objects.count(), 0)

    def test_order_request_requires_customer_account(self):
        self.assertEqual(
            self.owner_client.post(
                reverse("api:customer-order-requests"), self._order_request(), format="json"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.unlinked_client.post(
                reverse("api:customer-order-requests"), self._order_request(), format="json"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.inactive_customer_client.post(
                reverse("api:customer-order-requests"), self._order_request(), format="json"
            ).status_code,
            403,
        )


class OwnerDashboardAPITests(APITestCase):
    def test_dashboard_metrics_match_known_records_and_do_not_claim_accounting(self):
        self._build_operational_fixture()
        request_response = self.customer_client.post(
            reverse("api:customer-order-requests"),
            self._order_request(),
            format="json",
        )
        self.assertEqual(request_response.status_code, 201)

        response = self.owner_client.get(reverse("api:owner-dashboard"), self._period_params())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        data = response.json()

        self.assertEqual(data["period"]["start_date"], self.today.replace(day=1).isoformat())
        self.assertEqual(data["period"]["end_date"], self.today.isoformat())
        self.assertEqual(data["sales"]["orders_total"], 2)
        self.assertEqual(data["sales"]["non_cancelled_orders_count"], 2)
        self.assertEqual(data["sales"]["cancelled_orders_count"], 0)
        self.assertEqual(data["sales"]["orders_by_status"], {"CONFIRMED": 1, "DRAFT": 1})
        self.assertEqual(data["sales"]["commercial_order_value"], "20.00")
        self.assertEqual(data["sales"]["shipments_count"], 1)
        self.assertEqual(data["sales"]["active_shipments_count"], 1)
        self.assertEqual(data["sales"]["fully_reversed_shipments_count"], 0)
        self.assertEqual(data["sales"]["net_shipped_value"], "20.00")
        self.assertIn("not accounting revenue", data["sales"]["value_basis"])
        self.assertIn("orders_total", data["sales"]["count_basis"])
        self.assertIn("cancelled_orders_count", data["sales"]["count_basis"])

        self.assertEqual(data["purchases"]["orders_total"], 1)
        self.assertEqual(data["purchases"]["non_cancelled_orders_count"], 1)
        self.assertEqual(data["purchases"]["cancelled_orders_count"], 0)
        self.assertEqual(data["purchases"]["orders_by_status"], {"CONFIRMED": 1})
        self.assertEqual(data["purchases"]["commercial_order_value"], "150.00")
        self.assertEqual(data["purchases"]["receipts_count"], 0)
        self.assertEqual(data["purchases"]["active_receipts_count"], 0)
        self.assertEqual(data["purchases"]["fully_reversed_receipts_count"], 0)
        self.assertIn("not accounting expense", data["purchases"]["value_basis"])
        self.assertIn("receipts_count", data["purchases"]["count_basis"])

        finished = next(
            row for row in data["inventory"]["categories"] if row["code"] == "finished-goods"
        )
        self.assertEqual(finished["product_count"], 2)
        self.assertEqual(
            finished["balances"],
            [{"base_uom": self.pcs.code, "base_uom_name_ar": "قطعة", "quantity_base": "140.000000000000"}],
        )
        self.assertFalse(data["inventory"]["valuation_available"])
        self.assertEqual(len(data["low_stock_alerts"]), 1)
        self.assertEqual(data["low_stock_alerts"][0]["sku"], self.product.sku)
        self.assertEqual(data["low_stock_alerts"][0]["balance_base"], "90.000000000000")
        self.assertEqual(data["low_stock_alerts"][0]["threshold_base"], "150.000000000000")

        production_line = next(
            row for row in data["production"]["lines"] if row["code"] == self.production_line.code
        )
        self.assertEqual(production_line["status_counts"]["IN_PROGRESS"], 1)
        self.assertEqual(
            production_line["output"],
            [{
                "base_uom": self.pcs.code,
                "base_uom_name_ar": "قطعة",
                "good_quantity_base": "8.000000000000",
                "scrap_quantity_base": "2.000000000000",
            }],
        )
        self.assertIn("ProductionOrder.created_at", data["production"]["status_counts_basis"])
        self.assertIn("ProductionResult.occurred_at", data["production"]["output_basis"])

        latest_period = data["costing"]["periods"][0]
        self.assertEqual(latest_period["year"], self.today.year)
        self.assertEqual(latest_period["month"], self.today.month)
        self.assertEqual(latest_period["status"], CostingPeriod.Status.DRAFT)
        self.assertEqual(latest_period["calculation_status"], "COMPLETE")
        self.assertTrue(latest_period["labor_recorded"])
        self.assertEqual(latest_period["labor_completeness"], "RECORDED")
        self.assertEqual(latest_period["allocation_policy"], CostingPeriod.AllocationPolicy.UNITS_PRODUCED)
        self.assertEqual(latest_period["waste_policy"], CostingPeriod.WastePolicy.ABSORBED_NORMAL)
        self.assertEqual(latest_period["material_valuation_method"], "OWNER_STANDARD_PRICE")
        self.assertEqual(latest_period["actual_material_cost"], "80.00")
        self.assertEqual(latest_period["material_cost_assigned_to_good"], "80.00")
        self.assertEqual(latest_period["waste_material_cost"], "0.00")
        self.assertEqual(latest_period["total_production_cost"], "100.00")
        self.assertTrue(latest_period["reconciliation"]["materials_reconcile"])
        self.assertTrue(latest_period["reconciliation"]["cost_total_reconciles"])
        self.assertEqual(len(data["costing"]["latest_period_entries"]), 1)
        latest_entry = data["costing"]["latest_period_entries"][0]
        self.assertEqual(latest_entry["cost_per_good_unit"], "12.5000")
        self.assertEqual(latest_entry["actual_material_cost"], "80.00")
        self.assertEqual(latest_entry["material_cost_assigned_to_good"], "80.00")
        self.assertEqual(latest_entry["waste_material_cost"], "0.00")
        self.assertEqual(latest_entry["allocated_labor_cost"], "10.00")
        self.assertEqual(latest_entry["allocated_electricity_cost"], "5.00")
        self.assertEqual(latest_entry["allocated_water_cost"], "2.00")
        self.assertEqual(latest_entry["allocated_other_cost"], "3.00")
        self.assertEqual(latest_entry["calculation_status"], "COMPLETE")
        self.assertFalse(data["costing"]["allocation_review"]["independent_recalculation"])
        self.assertFalse(data["costing"]["allocation_review"]["accounting_final"])

        self.assertEqual(len(data["recent"]["sales_orders"]), 2)
        self.assertEqual(len(data["recent"]["purchase_orders"]), 1)
        self.assertEqual(len(data["recent"]["shipments"]), 1)
        self.assertEqual(data["customers"]["total"], 3)
        self.assertEqual(data["customers"]["active"], 2)
        self.assertEqual(data["customers"]["with_orders_in_period"], 1)
        self.assertEqual(data["suppliers"]["total"], 1)

        financial = data["financial_integrity"]
        self.assertFalse(financial["accounting_available"])
        self.assertIsNone(financial["profit"])
        self.assertIsNone(financial["net_profit"])
        self.assertIsNone(financial["receivables"])
        self.assertIsNone(financial["payables"])
        self.assertIsNone(financial["cash_balance"])
        self.assertFalse(financial["sales_commercial_value_is_revenue"])
        self.assertFalse(financial["purchase_commercial_value_is_expense"])
        self.assertIn("Shared accounting chart of accounts, journal, and ledger", financial["missing_dependencies"])
        self.assertEqual(data["data_sources"]["accounting"], "unavailable; not implemented")
        self.assertIn("LOW_STOCK", {row["code"] for row in data["operational_alerts"]})

    def test_costing_reconciliation_exposes_separate_abnormal_waste(self):
        period = CostingPeriod.objects.create(
            year=self.today.year,
            month=self.today.month,
            status=CostingPeriod.Status.DRAFT,
            labor_recorded=True,
            labor_total=Decimal("10.00"),
            electricity_total=Decimal("0.00"),
            water_total=Decimal("0.00"),
            other_industrial_total=Decimal("0.00"),
            materials_total=Decimal("100.00"),
            total_production_cost=Decimal("90.00"),
            waste_policy=CostingPeriod.WastePolicy.SEPARATE_ABNORMAL,
            is_complete=True,
        )
        ProductCostingEntry.objects.create(
            period=period,
            product=self.product,
            production_line=self.production_line,
            good_units_produced=Decimal("8.0000"),
            scrap_units_produced=Decimal("2.0000"),
            actual_material_cost=Decimal("100.00"),
            material_cost_assigned_to_good=Decimal("80.00"),
            waste_material_cost=Decimal("20.00"),
            allocated_labor_cost=Decimal("10.00"),
            total_production_cost=Decimal("90.00"),
            cost_per_good_unit=Decimal("11.2500"),
            calculation_status="COMPLETE",
        )

        response = self.owner_client.get(
            reverse("api:owner-costing"), self._period_params()
        )
        self.assertEqual(response.status_code, 200)
        costing = response.json()["costing"]
        row = costing["periods"][0]
        self.assertEqual(row["waste_policy"], CostingPeriod.WastePolicy.SEPARATE_ABNORMAL)
        self.assertEqual(row["actual_material_cost"], "100.00")
        self.assertEqual(row["material_cost_assigned_to_good"], "80.00")
        self.assertEqual(row["waste_material_cost"], "20.00")
        self.assertTrue(row["reconciliation"]["materials_reconcile"])
        self.assertTrue(row["reconciliation"]["cost_total_reconciles"])
        entry = costing["latest_period_entries"][0]
        self.assertEqual(entry["actual_material_cost"], "100.00")
        self.assertEqual(entry["material_cost_assigned_to_good"], "80.00")
        self.assertEqual(entry["waste_material_cost"], "20.00")
        self.assertEqual(entry["allocated_labor_cost"], "10.00")
        self.assertEqual(entry["total_production_cost"], "90.00")

    def test_cancelled_orders_are_counted_but_excluded_from_active_totals(self):
        order = self._create_sales_order()
        cancel_sales_order(order=order, actor=self.owner, reason="اختبار الإلغاء")

        response = self.owner_client.get(reverse("api:owner-dashboard"), self._period_params())
        self.assertEqual(response.status_code, 200)
        sales = response.json()["sales"]
        self.assertEqual(sales["orders_total"], 1)
        self.assertEqual(sales["cancelled_orders_count"], 1)
        self.assertEqual(sales["non_cancelled_orders_count"], 0)
        self.assertEqual(sales["orders_by_status"], {"CANCELLED": 1})
        self.assertEqual(sales["commercial_order_value"], "0.00")

    def test_fully_reversed_shipments_and_receipts_are_reported_separately(self):
        sales_order = self._create_sales_order(confirm=True, ship=True)
        shipment = sales_order.shipments.get()
        reverse_movement(
            movement=shipment.lines.get().movement,
            reference=f"SHP-REV-{uuid.uuid4().hex[:8]}",
            actor=self.owner,
            reason="تصحيح شحن",
        )

        purchase_order = self._create_purchase_order()
        receipt = create_receipt(
            order=purchase_order,
            actor=self.owner,
            reference=f"GRN-{uuid.uuid4().hex[:8]}",
            received_date=self.today,
            destination_location=self.location,
            lines=[{
                "order_line": purchase_order.lines.get(),
                "quantity": Decimal("100"),
                "unit": self.pcs,
            }],
        )
        reverse_movement(
            movement=receipt.lines.get().movement,
            reference=f"GRN-REV-{uuid.uuid4().hex[:8]}",
            actor=self.owner,
            reason="تصحيح استلام",
        )

        response = self.owner_client.get(reverse("api:owner-dashboard"), self._period_params())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["sales"]["shipments_count"], 1)
        self.assertEqual(data["sales"]["active_shipments_count"], 0)
        self.assertEqual(data["sales"]["fully_reversed_shipments_count"], 1)
        self.assertEqual(data["sales"]["net_shipped_value"], "0.00")
        self.assertEqual(data["purchases"]["receipts_count"], 1)
        self.assertEqual(data["purchases"]["active_receipts_count"], 0)
        self.assertEqual(data["purchases"]["fully_reversed_receipts_count"], 1)
        self.assertEqual(data["purchases"]["net_received_value"], "0.00")

    def test_production_status_and_output_use_explicit_different_period_bases(self):
        order = self._create_production()
        if self.today.month == 1:
            prior_year, prior_month = self.today.year - 1, 12
        else:
            prior_year, prior_month = self.today.year, self.today.month - 1
        prior_start = date(prior_year, prior_month, 1)
        prior_end = date(prior_year, prior_month, 28)
        ProductionOrder.objects.filter(pk=order.pk).update(
            created_at=timezone.make_aware(datetime(prior_year, prior_month, 15, 12))
        )

        current = self.owner_client.get(reverse("api:owner-dashboard"), self._period_params())
        self.assertEqual(current.status_code, 200)
        current_line = next(
            row for row in current.json()["production"]["lines"]
            if row["code"] == self.production_line.code
        )
        self.assertEqual(sum(current_line["status_counts"].values()), 0)
        self.assertEqual(
            current_line["output"],
            [{
                "base_uom": self.pcs.code,
                "base_uom_name_ar": "قطعة",
                "good_quantity_base": "8.000000000000",
                "scrap_quantity_base": "2.000000000000",
            }],
        )

        prior = self.owner_client.get(
            reverse("api:owner-dashboard"),
            {"start_date": prior_start.isoformat(), "end_date": prior_end.isoformat()},
        )
        self.assertEqual(prior.status_code, 200)
        prior_line = next(
            row for row in prior.json()["production"]["lines"]
            if row["code"] == self.production_line.code
        )
        self.assertEqual(prior_line["status_counts"]["IN_PROGRESS"], 1)
        self.assertEqual(prior_line["output"], [])

    def test_section_endpoints_and_owner_only_access(self):
        self._build_operational_fixture()
        endpoints = {
            "api:owner-sales": "sales",
            "api:owner-purchases": "purchases",
            "api:owner-inventory": "inventory",
            "api:owner-production": "production",
            "api:owner-costing": "costing",
        }
        for name, key in endpoints.items():
            response = self.owner_client.get(reverse(name), self._period_params())
            self.assertEqual(response.status_code, 200)
            self.assertIn(key, response.json())
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertEqual(
                self.customer_client.get(reverse(name), self._period_params()).status_code,
                403,
            )

    def test_dashboard_query_validation(self):
        start = (self.today - timedelta(days=400)).isoformat()
        cases = [
            {"start_date": self.today.isoformat()},
            {"end_date": self.today.isoformat()},
            {"start_date": self.today.isoformat(), "end_date": (self.today - timedelta(days=1)).isoformat()},
            {"start_date": start, "end_date": self.today.isoformat()},
        ]
        for params in cases:
            response = self.owner_client.get(reverse("api:owner-dashboard"), params)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["status"], 400)

    def test_default_dashboard_period_is_current_and_fresh(self):
        response = self.owner_client.get(reverse("api:owner-dashboard"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["period"]["end_date"], self.today.isoformat())
        self.assertEqual(data["period"]["days"], 30)
        self.assertEqual(response["Cache-Control"], "no-store")
