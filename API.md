# Mobile API

The versioned API foundation is implemented under `/api/v1/` with Django REST
Framework. It is intended for the future Flutter customer and owner apps. Mobile
clients must call this API; they must never connect directly to PostgreSQL.

## Authentication and roles

API authentication uses DRF token authentication only. Session authentication is
not enabled for the API, so state-changing requests must authenticate with a
token and use HTTPS in deployment.

```text
POST /api/v1/auth/token/
Content-Type: application/json

{"username": "...", "password": "..."}
```

The response contains exactly one string field, `token`; the OpenAPI schema
describes it with the dedicated `TokenResponse` component. Send it on later requests:

```text
Authorization: Token <token>
```

The token endpoint accepts JSON only and is rate-limited. Inactive users cannot authenticate. Staff users may obtain tokens for owner endpoints; customer endpoints require an active non-staff account linked to an active customer. `POST /api/v1/auth/logout/` revokes the current token.

There are two explicit API roles:

- **Customer:** an active, non-staff user linked one-to-one to an active
  `sales.Customer` record. Staff users cannot use customer endpoints.
- **Owner:** an authenticated active staff user (`is_staff=True`). This reuses the
  existing internal/staff boundary; it is not a separate accounting role.

Customer links are configured by staff on the customer record. No customer
registration or password-reset API is implemented.

## Customer endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/v1/customer/profile/` | The authenticated customer's own contact profile. |
| GET | `/api/v1/customer/products/` | Paginated active products explicitly marked `available_to_customers`. |
| GET | `/api/v1/customer/products/<id>/` | One authorized product with units and optional reference price. |
| GET | `/api/v1/customer/prices/` | Paginated products having an owner reference selling price. |
| GET | `/api/v1/customer/orders/` | Paginated orders belonging only to the authenticated customer. |
| GET | `/api/v1/customer/orders/<order_number>/` | One of the customer's own orders and its lines. |
| POST | `/api/v1/customer/order-requests/` | Create a draft sales-order request. |

Customer product responses deliberately exclude inventory balances, low-stock
thresholds, internal product flags, raw-material prices, production costs,
profit information, supplier data, and costing records. A customer requesting
another customer's order receives `404`, not another customer's data.

### Product visibility and prices

Products appear only when `catalog.Product.active` and
`catalog.Product.available_to_customers` are both true. Staff must explicitly
enable the customer-catalog flag; it is not inferred from the category.

`reference_price` comes from the owner-managed
`costing.ProductSellingPrice` record and is labelled as a reference price, not a
quotation. Customer order lines show the prices recorded on that customer's own
orders. No production cost or margin is exposed.

### Customer order requests

Example:

```json
{
  "requested_delivery_date": "2026-10-25",
  "customer_reference": "CUSTOMER-PO-12",
  "notes": "Please review",
  "lines": [
    {
      "product": 12,
      "quantity": "5.000000000000",
      "unit": 3,
      "notes": "Cups"
    }
  ]
}
```

The server ignores no privileged fields: unknown top-level or line fields such as
`customer`, `status`, `unit_price`, or `created_by` are rejected with `400`.
The customer, order date, status, and submitter are set by the server.

Requests are written through `sales.services.submit_customer_order_request`,
which shares the draft-order writer with the staff sales service. The result is
always a **draft** sales order with `customer_submitted=true` and zero prices
pending staff review. It does **not** confirm the order, reserve or deduct
inventory, create a shipment, or call the inventory posting service. Staff must
price, confirm, and ship it through the existing sales workflow.

Customer order responses expose both the order `status` and a separate
`pricing_status`:

- `PENDING_REVIEW`: a draft customer request still has at least one zero-priced
  line. Staff can edit it, but the sales service rejects confirmation and
  shipment until every line has a positive reviewed price.
- `RECORDED`: every line has a positive price (or the order was not submitted by
  a customer).
- `UNPRICED`: a legacy customer-submitted order was already confirmed with a
  zero price before this guard existed. It is never reported as `PENDING_REVIEW`
  once confirmed, and shipment remains blocked. Staff must correct/recreate the
  order through the normal audited workflow rather than silently shipping it.

The endpoint is not currently idempotent. A client should not blindly retry a
successful-looking request; a future idempotency key would require a schema
decision.

## Owner endpoints

All owner endpoints require an active staff user. Responses are computed from
current persisted records on every request and are sent with
`Cache-Control: no-store` and `Pragma: no-cache`.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/v1/owner/dashboard/` | Complete dashboard snapshot. |
| GET | `/api/v1/owner/sales/` | Sales summary, trends, recent orders, and recent shipments. |
| GET | `/api/v1/owner/purchases/` | Purchase summary, trends, and recent orders. |
| GET | `/api/v1/owner/inventory/` | Inventory by category and low-stock alerts. |
| GET | `/api/v1/owner/production/` | Production status and output by line. |
| GET | `/api/v1/owner/costing/` | Persisted monthly costing periods and latest product outputs. |

Query parameters for all owner endpoints:

- `start_date` and `end_date`: inclusive ISO dates. Supply both or neither. The
  default is the last 30 calendar days, and the maximum range is 366 days.
- `granularity`: `day` or `month`; defaults to `month` for ranges over 90 days.
- `recent_limit`: 1 to 20, default 5.

The dashboard is a read-only projection. It does not recalculate product costs
or create accounting entries.

### Available operational data

- Sales: commercial order values and shipment net values, status counts, and
  daily/monthly trends. `orders_total` and `shipments_count` are document totals
  and include cancelled orders and fully reversed shipments.
  `non_cancelled_orders_count`, `cancelled_orders_count`,
  `active_shipments_count`, and `fully_reversed_shipments_count` make the
  counting basis explicit. Commercial values exclude cancelled orders and
  reversed shipment lines. `count_basis` documents every count in the response.
- Purchases: analogous purchase-order and receipt counts. `receipts_count`
  includes fully reversed receipts, while `active_receipts_count` counts receipts
  with at least one non-reversed line and `fully_reversed_receipts_count` counts
  documents with no non-reversed line. `count_basis` documents the basis.
- Inventory: base-unit quantities by category and base UOM. Quantities in
  different dimensions are never summed, and no inventory valuation is claimed.
- Low stock: active products with a positive `catalog.Product.low_stock_threshold`
  whose total base-unit balance is at or below the threshold. Staff configure the
  threshold on the product.
- Production: production-order status counts and good/scrap output by production
  line, grouped by base UOM. `status_counts_basis` states that status counts use
  `ProductionOrder.created_at`, while `output_basis` states that output uses
  `ProductionResult.occurred_at`. The two date bases are intentionally different;
  output from an order created in another period can appear in the requested
  period. `quantity_note` repeats this caveat.
- Customers and suppliers: contact-master counts and active counts only.
- Costing: the latest persisted monthly periods and the latest period's product
  entries. Each period includes `labor_recorded`, `labor_completeness`,
  `allocation_policy`, `waste_policy`, `material_valuation_method`,
  `actual_material_cost`, `material_cost_assigned_to_good`,
  `waste_material_cost`, and a `reconciliation` object with
  `materials_reconcile` and `cost_total_reconciles`. Product entries expose the
  persisted material, labor, electricity, water, and other-industrial allocation
  components plus `calculation_status`. These values are read from persisted
  rows; the API does not recalculate product costs.

### Financial integrity and unavailable metrics

Commercial sales and purchase document values are **not** accounting revenue or
expense. The API never computes or labels sales minus purchases as profit.
Because shared accounting, invoices, payments, receivables, payables, and
validated postings do not exist, the dashboard returns `null` for:

- profit and net profit;
- outstanding receivables;
- outstanding payables;
- cash balances.

It also returns `accounting_available=false` and lists the missing dependencies.
No currency, tax, payment-term, or accounting-period policy is invented.

Monthly costing outputs remain the source for product costs. The API reads
persisted `CostingPeriod` and `ProductCostingEntry` records and does not
independently recalculate them. Labor, electricity, water, and other-industrial
allocation is performed by the existing monthly costing service; the API only
exposes the persisted result and performs display-level reconciliation arithmetic
on those stored totals. Actual material cost reconciles as
`material_cost_assigned_to_good + waste_material_cost = actual_material_cost`;
the cost total reconciles as
`material_cost_assigned_to_good + labor + electricity + water + other_industrial
= total_production_cost`.

Labor is a required monthly record when production exists. A missing labor record
marks the period incomplete and blocks finalization; an explicitly recorded
`0.00` is a confirmed zero and remains complete. Fallback allocations (a
line/product scope with no matching production, or a product with no usable good
units) are marked `INCOMPLETE` and require review. Costing remains provisional and
not accounting-final until owner/accountant approval; the dashboard response
explicitly records this review status.

## Pagination and errors

Customer list endpoints use page-number pagination:

- `?page=1`
- `?page_size=25` (maximum 100)

Errors use a stable envelope:

```json
{
  "error": {
    "status": 400,
    "detail": {"field": ["message"]}
  }
}
```

## Schema documentation

Staff-only schema and interactive documentation:

- `/api/v1/schema/`
- `/api/v1/docs/`
- `/api/v1/redoc/`

The schema is generated with `drf-spectacular`. The documentation pages use the
staff permission boundary. They accept a logged-in staff Django session (so they
work in a browser) or a staff token. The actual API endpoints remain token-only;
session authentication is enabled only for the schema and documentation views.

## Deliberate limitations

- No Flutter applications, push notifications, WebSockets, or live streaming.
  Mobile clients refresh when returning to a screen or after a write.
- No accounting, invoices, returns/credit notes, payments, tax, receivables,
  payables, cash, or authoritative profit.
- No inventory valuation, lot/serial tracking, or stock reservation.
- No customer registration, password reset, order cancellation, or customer
  price-list administration API.
- No CORS configuration; native mobile HTTP clients do not need it. A Flutter web
  deployment would require an explicit CORS policy.
- Customer order request idempotency keys are not implemented.
