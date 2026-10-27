# Mobile API

The versioned API foundation is implemented under `/api/v1/` with Django REST
Framework. It supports customer, owner, and operational mobile clients. Mobile
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

The token endpoint accepts JSON only and is rate-limited. Inactive users cannot authenticate. `POST /api/v1/auth/logout/` revokes the current token. After login, clients call `GET /api/v1/auth/me/` to obtain the server-derived effective role, customer identity, and permissions. The login response contains only the token; clients must not infer a role from local input.

There are three explicit API roles:

- **Customer:** an active, non-staff user linked one-to-one to an active
  `sales.Customer` record. Staff users cannot use customer endpoints.
- **Owner:** an active superuser or active user assigned to Django group
  `ERP Owners`. `is_staff=True` alone does not grant owner access.
- **Operational staff:** an active staff user assigned to Django group
  `ERP Operational Staff`. Each write also requires its existing manufacturing
  model permission. Operational staff cannot access owner endpoints.

Groups are not assigned automatically. Before rollout, explicitly assign every
existing legitimate owner to `ERP Owners` or confirm it is a superuser, and
assign operators to `ERP Operational Staff` with only their needed action
permissions. This uses existing Django groups and requires no user-table
migration. Customers and unauthenticated users are denied employee and owner
routes.

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
| GET | `/api/v1/owner/profit/` | Persisted monthly management result, operating expenses, and loss carry-forward. |
| GET | `/api/v1/owner/sales/orders/` | Paginated sales-order detail for the selected period. |
| GET | `/api/v1/owner/purchases/orders/` | Paginated purchase-order detail for the selected period. |
| GET | `/api/v1/owner/inventory/stock/` | Paginated current stock rows from the shared ledger. |
| GET | `/api/v1/owner/inventory/movements/` | Paginated stock movements for the selected period. |
| GET | `/api/v1/owner/production/orders/` | Paginated production orders created in the selected period. |
| GET | `/api/v1/owner/costing/periods/` | Paginated persisted costing periods in the selected range. |

Query parameters for all owner endpoints:

- `start_date` and `end_date`: inclusive ISO dates. Supply both or neither. The
  default is the last 30 calendar days, and the maximum range is 366 days.
- `granularity`: `day` or `month`; defaults to `month` for ranges over 90 days.
- `recent_limit`: 1 to 20, default 5.
- `dimension_limit`: 1 to 100, default 20. It limits grouped customer, supplier,
  and product rows plus the costing section's period and order-detail rows and the
  monthly-profit period rows;
  aggregate totals still cover the full period. Paginated detail endpoints use
  `page_size` instead.

The detail endpoints use the same period rules and page-number pagination as the
other API list endpoints (`page`, `page_size`; maximum 100). Additional filters:

- Sales orders: `status`, `customer` (numeric id), and `customer_submitted`
  (`true`/`false`).
- Purchase orders: `status` and `supplier` (numeric id).
- Stock movements: `movement_type` and `product` (numeric id). The date range is
  applied to the movement's `occurred_at` date.
- Production orders: `status` and `production_line` (line code). The date range
  is applied to `ProductionOrder.created_at`.
- Costing periods: `status`.

Invalid choices, non-positive ids, invalid dates, and reversed date ranges return
the standard 400 error envelope. Customer users are rejected from every owner
endpoint, including the detail endpoints.

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
- Inventory: current base-unit quantities by category and base UOM from the
  shared ledger, plus movement activity for the selected period. Quantities in
  different dimensions are never summed, and no inventory valuation is claimed.
  `historical_balance_available=false` makes clear that current stock is not an
  as-of-date historical balance. `movement_summary` excludes reversal entries and
  original movements that have been reversed, then reports those separately to
  avoid double counting.
- Low stock: active products with a positive `catalog.Product.low_stock_threshold`
  whose total base-unit balance is at or below the threshold. Staff configure the
  threshold on the product.
- Production: production-order status counts, planned quantities, active material
  consumption, good/scrap output, and yield by production line, grouped by base
  UOM. `status_counts_basis` states that status counts use
  `ProductionOrder.created_at`, `consumption_basis` uses the issue movement's
  `occurred_at`, and `output_basis` uses `ProductionResult.occurred_at`. The date
  bases are intentionally different; output from an order created in another
  period can appear in the requested period (`cross_period_output_count`).
  `reconciliation` compares total output with the sum attributed to the displayed
  production lines and reports any unallocated output instead of assuming zero.
  Sales, purchasing, and inventory remain shared ledgers without a duplicated
  per-line ledger.
- Customers and suppliers: contact-master counts and active counts only.
- Costing: the latest persisted monthly periods and the latest period's product
  entries. Each period includes `labor_recorded`, `electricity_recorded`,
  `water_recorded`, the corresponding completeness labels,
  `missing_required_expenses`, `allocation_policy`, `waste_policy`,
  `material_valuation_method`, `actual_material_cost`,
  `material_cost_assigned_to_good`, `waste_material_cost`, and a
  `reconciliation` object with `materials_reconcile` and
  `cost_total_reconciles`. Product entries expose the persisted material, labor,
  electricity, water, and other-industrial allocation components plus
  `calculation_status`. These values are read from persisted rows; the API does
  not recalculate product costs. A recorded `0.00` utility is complete; a missing
  record is reported as missing.
- Monthly profit: `/api/v1/owner/profit/` and the dashboard's `profit_loss`
  object read persisted `MonthlyProfitPeriod` and `MonthlyProfitCOGSLine` rows.
  Each period exposes net revenue, COGS, gross profit, each operating-expense
  category, operating-expense total, `monthly_result`,
  `opening_accumulated_loss`, `loss_recovered`,
  `closing_accumulated_loss`, and `profit_after_loss_recovery`, plus
  completeness flags, missing-data notes, finalization/reopen audit metadata,
  COGS product lines, and expense records. `accounting_final` is `false`; the
  monthly result is a management operating result before loss recovery and is
  not final accounting net profit.
- Data quality: `data_quality` returns warning codes for low stock, negative
  stock, customer price review, unpriced legacy orders, missing utilities,
  incomplete costing, fallback costing review, draft orders, open production,
  missing production results, missing monthly operating expenses, incomplete
  COGS, incomplete profit periods, draft profit periods, and profit months not
  yet calculated. Informational codes explicitly state that accounting and
  historical stock are unavailable and that the monthly result is management
  data only.

### Financial integrity and unavailable metrics

Commercial sales and purchase document values are **not** accounting revenue or
expense. The API never computes or labels sales minus purchases as profit.
Because shared accounting, invoices, payments, receivables, payables, and
validated postings do not exist, the dashboard returns `null` for:

- accounting profit and net profit;
- outstanding receivables;
- outstanding payables;
- cash balances.

It also returns `accounting_available=false` and lists the missing dependencies.
The separate `profit_loss` section reports persisted monthly management results
and accumulated-loss recovery, but it explicitly sets `accounting_final=false`
and must not be called final accounting net profit. No currency, tax,
payment-term, or accounting-period policy is invented.

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

Labor, electricity, and water are required monthly records when production
exists. A missing record marks the period incomplete and blocks finalization; an
explicitly recorded `0.00` is a confirmed zero and remains complete. Fallback
allocations (a
line/product scope with no matching production, or a product with no usable good
units) are marked `INCOMPLETE` and require review. Costing remains provisional and
not accounting-final until owner/accountant approval; the dashboard response
explicitly records this review status.

## Pagination and errors

Customer and owner detail-list endpoints use page-number pagination:

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

## Employee operational endpoints

All routes below require the `ERP Operational Staff` group and active staff
status. Writes additionally require the domain permission checked by their
service. Inputs reject undeclared fields; actor identity is the authenticated
user and server services determine posting timestamps and valuation.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/v1/employee/production/orders/` | Active released/in-progress production orders and operation/material context. |
| GET | `/api/v1/employee/production/orders/<id>/` | One active production order with operation and material requirements. |
| POST | `/api/v1/employee/production/progress/` | Idempotent operation progress through `record_operation_progress`. |
| POST | `/api/v1/employee/production/operations/complete/` | Complete an operation or skip an eligible optional step through `complete_operation`. |
| POST | `/api/v1/employee/production/results/` | Production output through `record_production_result`. |
| POST | `/api/v1/employee/production/results/<id>/reverse/` | Audited full result reversal. |
| POST | `/api/v1/employee/production/results/<id>/waste/` | Immutable measured, material-specific waste. |
| POST | `/api/v1/employee/production/results/<id>/waste-review/` | Explicit reviewed-zero evidence when no active waste exists. |
| POST | `/api/v1/employee/production/waste/<id>/reverse/` | Audited waste reversal. |
| GET | `/api/v1/employee/materials/` | Raw-material stock balances and active locations. |
| POST | `/api/v1/employee/material-issues/` | Required material issue through `issue_material`. |
| POST | `/api/v1/employee/counters/` | Start a manual counter interval. |
| POST | `/api/v1/employee/counters/<id>/<action>/` (`close`) | Close a counter interval. |
| POST | `/api/v1/employee/counters/<id>/<action>/` (`approve`) | Approve a completed/corrected interval. |
| POST | `/api/v1/employee/counters/<id>/<action>/` (`correct`) | Correct an interval with an audit reason. |
| POST | `/api/v1/employee/finished-receipts/` | Receive good production via the shared inventory ledger. |

These adapters do not create ledger movements directly and omit cost/profit
fields. Purchasing, sales, and financial writes are not available to employees.

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

- A Flutter Android client is implemented under `mobile/`. iOS, Flutter web,
  push notifications, WebSockets, and live streaming are not implemented. A
  signed release APK is also not built here. Mobile clients refresh when
  returning to a screen or after a write.
- No accounting, invoices, returns/credit notes, payments, tax, receivables,
  payables, cash, or authoritative accounting profit. The monthly management
  result and loss carry-forward are persisted separately and are not final net
  profit.
- No accounting inventory valuation, FIFO, or moving weighted average. Per-material
  Last Purchase Cost (LPC) snapshots are used only by production costing. There is
  no lot/serial tracking, stock reservation, or historical as-of-date stock balance.
- No production-line attribution on sales, purchasing, or shared inventory
  ledger entries. Production reports can be filtered by line; reports never
  allocate or duplicate shared records to manufacture a line breakdown.
- No customer registration, password reset, order cancellation, or customer
  price-list administration API.
- No CORS configuration; native mobile HTTP clients do not need it. A Flutter web
  deployment would require an explicit CORS policy.
- Customer order request idempotency keys are not implemented.
