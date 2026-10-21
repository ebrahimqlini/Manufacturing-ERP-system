# Implementation status

Updated: 2026-10-21

## Prompt 0 — foundation

The Django 5.2.18 foundation, Arabic RTL shell, environment-based PostgreSQL settings, dependency pins, and initial verification are complete. See [PROMPT0_VERIFICATION.md](PROMPT0_VERIFICATION.md) for the historical foundation evidence. This phase remains closed.

## Prompt 1 — shared catalog and inventory

Implemented in this checkout:

- Added `catalog` and `inventory` Django apps with Arabic RTL screens and authentication. All reads require login; configuration changes and stock posting require a staff account. Posted movements are read-only in Django admin.
- Catalog supports configurable products/categories, a globally unique SKU, configurable UOM dimension keys, and product-specific conversion factors. Initial shared categories cover raw materials, WIP, finished goods, packaging materials, scrap, and consumables.
- Inventory supports shared warehouses and locations, a single immutable movement ledger, receipt, issue, transfer, adjustment, controlled opening balances, and explicit reversal records. Transfers create both signed location entries atomically. A unique reference prevents duplicate posting.
- Movement records have an optional generic, unique source-type/source-id pair for future production posting traceability without importing a manufacturing model.
- Stock on hand is aggregated from ledger entries. Auxiliary per-product/location rows provide PostgreSQL transaction locks only; they do not store or replace stock balances.
- Quantities, conversion factors, and base quantities use `Decimal` with 12 fractional places. A conversion that needs more precision or exceeds storage range is rejected without rounding.
- Confirmed factory policy: physical stock must not become negative. The service validates sufficient ledger balance inside the transaction after acquiring product/location locks for issues, transfers, decreasing adjustments, and reversals. Rejected operations write no movement or ledger legs. Production orders may be released with shortages; manufacturing tracks planned, issued-to-date, and remaining quantities independently and accepts later partial issues.
- Posted records reject instance and queryset ORM edits/deletes; PostgreSQL triggers reject direct SQL UPDATE/DELETE for movement headers and ledger entries. This does not stop table owners disabling triggers, TRUNCATE, or unauthorized direct inserts; database privilege management and controlled writers remain necessary.
- No real products, units, conversion values, opening balances, or production-line orders were added to `paper_factory`. Only Prompt 1 category reference rows exist there. The confirmed fiber process fact is pressing fiber material in a mold; other stages and factory-specific inputs remain unconfirmed.

## Prompt 2 — configurable manufacturing

Implemented in this checkout:

- Added `manufacturing` with configurable production lines and operation candidates, versioned product BOMs, product-and-line routings, draft/released/in-progress/completed/cancelled orders, Arabic RTL configuration and order pages, and read access for authenticated users with staff-only changes.
- Seeded line identities for paper cups and fiber containers, cup operation candidates for printing/cutting/forming/optional bottom assembly/packaging, and the one confirmed fiber operation: pressing fiber material in a mold. No product BOM, recipe, production rate, machine, or unconfirmed fiber stage was seeded.
- Releasing an order locks the BOM/routing headers and their component/step rows while taking a consistent snapshot. Configuration saves, child inserts/deletes, and release share those locks and reject edits to revisions used by non-draft orders. Snapshots preserve operation names, instructions, optional flags, requirements, and base UOMs.
- Material requirements are computed using product conversions and exact Decimal precision. Orders can release with shortages; the detail page shows planned, net-issued, remaining, available, and current shortage. Repeated partial issues are posted through the existing shared `inventory.services.post_movement` service and cannot exceed either remaining requirements or available stock.
- Operation progress rows are incremental quantities. Their cumulative total cannot exceed the order plan or the nearest preceding non-skipped operation's processed total; completion rechecks the same limit. Required route steps default to required, and only explicitly optional snapshots may be skipped.
- Progress and production-result forms carry stable UUID submission keys. Replays with the same key and payload return the existing row without posting again; payload changes under a reused key fail. Good output and scrap remain order-level entries capped by the plan; route progress is not treated as a production-yield ledger.
- Finished goods are received in partial receipts through the shared inventory service. Reversing a receipt remains a generic inventory action and leaves a completed order completed. A staff-only replacement receipt path links a new receipt to the reversed original and keeps net receipts within recorded good output; ordinary receipt remains limited to active orders. Cancellation retains history.
- WIP balances/transfers, machine scheduling, accounting entries, and factory-specific BOM quantities are not implemented in this phase. Monthly product costing is implemented separately (see the production-costing section below); its material valuation remains provisional until owner/accountant confirmation, and the WIP transfer policy is still unconfirmed.

### Prompt 2 verification (before Prompt 2.1)

- SQLite: `.venv\Scripts\python.exe manage.py check` — passed; `makemigrations --check --dry-run` — no changes; full `manage.py test` — 43 tests passed, 4 PostgreSQL-specific tests skipped.
- PostgreSQL: the verification process connected as `paper_factory_app` to `paper_factory`; Django's test database resolved to `test_paper_factory`. Full `manage.py test --keepdb` ran all 43 tests against that dedicated database, passed with zero skips, and preserved the test database. Coverage included PostgreSQL trigger checks, two competing manufacturing issues where only one was accepted, and both-line routing/shared-stock behavior.
- PostgreSQL initially exposed a `FOR UPDATE` query error caused by locking nullable BOM/routing joins during release. The service now locks only the production-order row (`select_for_update(of=("self",))`). The full PostgreSQL suite passed after that correction.
- Django applied manufacturing migrations `0001_initial`, `0002_seed_confirmed_lines_and_operations`, and `0003_productionmaterialrequirement_base_unit_snapshot_and_more` only to `test_paper_factory` during the test run. No manufacturing migration was applied to the application database `paper_factory`.
- No inventory/application business rows were created in `paper_factory`; no users were created or changed. SQLite and PostgreSQL test fixtures were confined to their respective test databases.

## Prompt 2.1 — manufacturing audit corrections

- Added migration `manufacturing.0004_finishedgoodsreceipt_replacement_for_and_more` for optional route snapshots, stable UUID submission keys, and receipt replacement links. Existing progress/result records receive distinct keys in a data migration before the unique constraints are added.
- Added regression tests for result/progress idempotency and payload mismatch, required versus optional skip, downstream process quantity caps, completed-order receipt reversal/replacement, and non-staff POST access across manufacturing writes. PostgreSQL-only concurrency tests cover order issue and release versus BOM edit serialization.
- SQLite: `.venv\Scripts\python.exe manage.py check` passed; `makemigrations --check --dry-run` reported no changes; the full suite ran 50 tests, passed, and skipped 6 PostgreSQL-only tests. `git diff --check` passed (Git emitted only existing LF-to-CRLF advisories).
- PostgreSQL: verified the configured server target as `127.0.0.1:5432`, connected identity `paper_factory` / `paper_factory_app`, and isolated test database `test_paper_factory`. `.venv\Scripts\python.exe manage.py test --keepdb --verbosity 1` ran 50 tests, passed, and skipped none. This included concurrent material issues with duplicate-reference rollback and release-versus-BOM-edit tests.
- Read-only migration ledger check: `paper_factory` has no applied manufacturing migrations; `test_paper_factory` has `0001_initial` through `0004_finishedgoodsreceipt_replacement_for_and_more`. The real application database was not migrated or otherwise written during this work.

### Database and migration evidence

- `.venv\Scripts\python.exe` is Python 3.12.14; Django 5.2.18; Psycopg 3.3.6.
- PostgreSQL 18.6 is reachable at `127.0.0.1:5432`. The real connection reported database `paper_factory` and role `paper_factory_app`.
- `paper_factory` and `test_paper_factory` are owned by `paper_factory_app`. The role is not superuser and has no `CREATEDB` or `CREATEROLE` privileges.
- Before Prompt 1 migration, no catalog or inventory tables existed in `paper_factory`. The reviewed plan created catalog/inventory tables and seeded generic categories. It contained no drop or alteration of existing tables/data.
- `.venv\Scripts\python.exe manage.py migrate --noinput` applied catalog migrations `0001_initial` and `0002_default_categories`, and inventory migrations `0001_initial`, `0002_stockmovement_source_reference_id_and_more`, and `0003_stockmovement_unique_stock_source_reference` to `paper_factory`. No product, stock movement, or opening balance rows were created.
- PostgreSQL `check --database default` passed. `makemigrations --check --dry-run` reported no changes. `showmigrations` showed all migrations applied, and `migrate --plan` showed no pending operations.
- The app database role remains least-privileged. No database role or password was changed, and credentials are not recorded in project files.
- The dedicated PostgreSQL test database was run with `--keepdb` and preserved. No tests were pointed at `paper_factory`.

### Verification results

- SQLite: `manage.py test` — 23 tests passed; one PostgreSQL row-lock concurrency test skipped because SQLite lacks `SELECT FOR UPDATE`.
- PostgreSQL: `manage.py test --keepdb` — 23 tests passed, zero skipped. This included parallel duplicate-reference posting against PostgreSQL.
- Verification after the no-negative-stock fix (2026-10-15): full SQLite `manage.py test` — 30 tests passed, 3 skipped (PostgreSQL row-lock/direct-trigger cases). Full PostgreSQL `manage.py test --keepdb` — 30 tests passed, no skips, against the preserved `test_paper_factory` database. PostgreSQL coverage included two independent transactions racing to issue 7 units each from a balance of 10: one posted, one was rejected, and the final balance was 3. PostgreSQL direct-SQL UPDATE/DELETE trigger tests passed.
- SQLite and PostgreSQL `manage.py check` / `check --database default` passed. `makemigrations --check --dry-run` reported no model changes. `showmigrations inventory` shows `0001` through `0004` applied; `migrate --plan` reports no pending operations. `git diff --check` passed; Git only reported the existing LF-to-CRLF working-copy notices.
- Applied migration `inventory.0004_posted_records_immutable` to `paper_factory`; its sole operation installs PostgreSQL UPDATE/DELETE triggers and does not rewrite stock data. Before migration and again after all checks, the connected database identity was `paper_factory` / `paper_factory_app`, with zero product, movement, or ledger rows. The migration is recorded as applied and both expected triggers exist. All test writes were confined to SQLite's disposable test DB or the existing PostgreSQL `test_paper_factory` DB.
- A local `.env` now holds this checkout's PostgreSQL connection values; it is ignored by Git and Django does not load it automatically. No credential value is stored in committed project files or this report.
- `manage.py check` and `makemigrations --check --dry-run` passed under SQLite; PostgreSQL checks passed as listed above.
- Django does not load `.env` automatically. `.env` is ignored by Git and `.env.example` contains placeholders only.
- No staff/superuser account was created as part of this implementation. Create the initial administrator locally with `python manage.py createsuperuser`; inventory setup and posting screens require `is_staff`.

## Production costing — monthly product cost (implemented)

Implemented in this checkout (committed as `7ccb3a0`):

- Added the `costing` app: monthly `CostingPeriod`, `ProductCostingEntry`, `ProductionOrderCostingEntry`, `MonthlyExpense` (labor / electricity / water / other industrial; scoped to factory, line, or product), owner-entered `RawMaterialPrice`, `RollYieldStandard`, and reference-only `ProductSellingPrice`.
- Material cost comes from actual material issues dated inside the calendar month (reversed issues excluded), priced with owner-entered prices labelled as a provisional standard. FIFO/weighted-average are deliberately not used: no valuation method is approved and the shared ledger carries no cost. A missing or future-dated price is reported explicitly and marks the period incomplete instead of silently reusing another price.
- Electricity, water, and labor are required monthly records for any month with production: a missing record marks the period incomplete with an explicit Arabic note and blocks finalization rather than being assumed zero. A recorded `0.00` is a confirmed zero and is kept distinct from a missing record (`CostingPeriod.labor_recorded` for labor). Each utility bill is charged entirely to the operator-selected month, and the model documents that a utility bill's period may differ from its consumption month (no bill-period fields or accounting rule are invented).
- A scoped expense with no matching production is allocated through an explicit fallback and marks the period incomplete; affected product entries are `INCOMPLETE`. A product with no good units has no usable good-unit cost and cannot be finalized silently.
- Multiple expense rows in the same month/type/scope are intentionally allowed and summed because a month can contain more than one legitimate bill or payroll entry. No automatic duplicate detector is implemented; descriptions/notes and period review remain the control.
- Labor/electricity/water/other shared expenses are allocated by the period policy (units produced, material cost, material weight, or equal-per-line then units). Product- and line-scoped expenses are attributed directly. Allocation parts always sum to the recorded bucket total (cent-exact); this was verified for all four policies.
- The waste policy changes the numbers, not just a stored flag: `ABSORBED_NORMAL` loads the full material cost on good units; `SEPARATE_ABNORMAL` splits material cost between good output and recorded scrap in proportion to recorded quantities, shown separately and never added on top of the material total.
- Product entries are computed bottom-up from production-order entries so order sums equal product entries and period totals exactly. Each period stores its explicit `start_date`/`end_date` (set on every calculation; earlier rows backfilled by migration `0005`). Per-product and per-order breakdowns, a recorded-vs-allocated reconciliation table, and a cross-month comparison page show electricity and water separately; the comparison selects one product and shows its monthly material/labor/utilities/other/waste/total, cost per good unit, production and waste quantities, and month-over-month changes.
- Finalized periods are protected by the expense model (`clean`/`delete`) and by the costing views/services; reopening requires a reason and is recorded.

### Production costing verification

- SQLite (isolated in-memory test database): `manage.py check` passed; `makemigrations --check --dry-run` reported no changes; the full `manage.py test` ran 85 tests, passed, and skipped 6 PostgreSQL-only tests. 35 of the passing tests are costing tests (including electricity/water attribution and isolation, zero-vs-missing, scope/policy allocation with cent-exact reconciliation, recalculation idempotency, comparison-view detail, finalized-period guards, and costing access control).
- Migrations `costing.0001_initial`, `0002_costingperiod_calculated_by_and_more`, `0003_costingperiod_water_total_and_more`, `0004_alter_costingperiod_options`, and `0005_remove_monthlyexpense_monthly_expense_amount_positive_and_more` were applied to the local SQLite development database.
- PostgreSQL: connected to `test_paper_factory` as `paper_factory_app` (PostgreSQL 18.6). The reported login with a space ("paper factory app") is not a valid role. With explicit approval, `costing.0002`–`0005` were applied to `test_paper_factory`, and `manage.py test --keepdb` ran all 85 tests with zero skips and preserved the test database. `paper_factory` was never connected to or modified.
- Finalized-period protection is enforced on the instance and bulk/queryset write paths (`MonthlyExpense.save`/`delete` and `MonthlyExpense`/`CostingPeriod` queryset `update`/`delete`) and in admin (finalized periods are fully read-only and product-costing rows are inline read-only). Regression test `CostingModelTests.test_finalized_period_blocks_orm_and_bulk_writes` covers the bulk paths.
- Access control: every costing page that exposes financial data (period list/detail, product and order breakdowns, cross-month comparison, monthly expenses, raw-material prices, and reference selling prices) requires a staff user or a user explicitly granted the `costing.view_costing_financials` permission. Ordinary authenticated users receive 403; write actions remain staff-only. The nav link is hidden from users without access. The permission is never assigned automatically.
- The per-order breakdown totals row was corrected during this work.

## Purchasing and supplier management (implemented)

Implemented in this checkout:

- Added the `purchasing` app: `Supplier`, `PurchaseOrder`, `PurchaseOrderLine`, `PurchaseReceipt`, and `PurchaseReceiptLine`, with migration `0001_initial`.
- Supplier records hold contact data only (code, names, contact person, phone, email, address, notes, active flag) and no bank/tax/payment details; server-side validation normalizes the code and requires the Arabic name and a unique code.
- Purchase orders use a generated unique reference, a supplier, order/expected dates, an optional supplier reference, notes, and lines (material, quantity, purchase unit, agreed unit price, notes). Lines validate the product's configured unit conversions and store the base quantity, base price, and line total. The lifecycle is `DRAFT → CONFIRMED`, with `CANCELLED` reachable from either state only while no receipt exists and requiring a reason; confirmed lines are locked.
- Receipts post through `inventory.services.post_movement` into the shared ledger (one movement per receipt line, each with a unique `purchasing.purchase_receipt` source reference). They support partial receiving, derive received/outstanding quantities from non-reversed lines, block duplicate references and over-receiving, reject zero/negative quantities, and run atomically so a failed line rolls back the whole receipt. Correcting a receipt uses inventory reversal, which restores the outstanding quantity and derived state.
- Purchase prices are snapshotted on each receipt line for traceability and shown on a read-only price-history page. Purchasing never writes `RawMaterialPrice` and does not change the costing valuation method; costing continues to price material issues with owner-entered provisional prices.
- All purchasing pages and writes are staff-only, and the navigation link is hidden from other users. Arabic RTL templates follow the existing Bootstrap shell and pagination.

### Purchasing verification

- `manage.py check` passed and `makemigrations --check --dry-run` reported no changes.
- SQLite (isolated in-memory test database): `manage.py test purchasing` ran 37 tests; 35 passed and 2 PostgreSQL row-lock concurrency tests were skipped.
- PostgreSQL (`test_paper_factory` as `paper_factory_app`, PostgreSQL 18.6): `manage.py test --keepdb` ran the full suite — 123 tests passed, zero skipped — and preserved the test database. This includes the two purchasing concurrency tests (parallel duplicate-receipt reference and parallel over-receipt). `purchasing.0001_initial` is recorded as applied to `test_paper_factory`. `paper_factory` was never connected to or modified.
- Purchasing tests cover supplier validation, the order lifecycle, partial/complete receiving with unit conversion, duplicate-reference and over-receipt prevention, rollback when a receipt line fails, inventory-reversal reconciliation of outstanding quantities, receipt immutability, price traceability without changing costing valuation, staff-only access control, and navigation gating.

## Sales and customer management (implemented)

Implemented in this checkout:

- Added the `sales` app: `Customer`, `SalesOrder`, `SalesOrderLine`, `Shipment`, and `ShipmentLine`, with migrations `0001_initial` and `0002_sales_order_reopen`.
- Customer records hold contact data only (code, Arabic/English names, contact person, phone, email, address, notes, active flag) and no national ID, bank, tax, or payment details; server-side validation normalizes the code and requires the Arabic name and a unique code.
- Sales orders use a generated unique reference, a customer, order/requested-delivery dates, an optional customer reference, notes, and lines (product, quantity, sales unit, agreed unit price, notes). Lines validate the product's configured unit conversions and store the base quantity, base price, and line total. The lifecycle is `DRAFT → CONFIRMED → COMPLETED`, with `CANCELLED` reachable from draft or confirmed only while no shipment exists and requiring a reason; confirmed lines are locked. Completion requires the full ordered quantity to be shipped (net of reversals) and at least one shipment.
- Shipments post through `inventory.services.post_movement` into the shared ledger (one ISSUE movement per shipment line, each with a unique `sales.shipment` source reference). They support partial shipping, derive shipped/outstanding quantities from non-reversed lines, block duplicate references and over-shipping, reject zero/negative quantities, and run atomically so a failed line rolls back the whole shipment. Confirming an order never issues stock. Correcting a shipment uses inventory reversal, which restores the outstanding quantity and derived state; reversal movements are terminal and cannot themselves be reversed.
- A completed order remains `COMPLETED` after a shipment reversal. If the reversal leaves outstanding quantity, staff can explicitly reopen it to `CONFIRMED` with a mandatory reason; the action is POST-only, CSRF-protected, row-locked, and records actor/time/reason. The outstanding quantity can then be shipped and the order completed again. The order detail page shows the reversal warning, the audit trail, and the action only when the preconditions are met.
- Sales prices are snapshotted on the order line and again on each shipment line for traceability. Shipment value is calculated from the shipment quantity in base units multiplied by the order line's snapshotted base-unit price, then rounded to cents, so compatible alternate units remain correct in both directions. `ShipmentLine.unit_price` is the order-line unit price and is labelled as such; document value is retained after reversal while net shipped value excludes reversed lines. Sales never writes `RawMaterialPrice` and does not change the costing valuation method or production cost; costing continues to price material issues with owner-entered provisional prices.
- All customer and sales pages and writes are staff-only, and the navigation link is hidden from other users. Arabic RTL templates follow the existing Bootstrap shell and pagination. No customer invoices, payments, tax, returns, or accounting postings are implemented in this phase.

### Sales verification

- `manage.py check` passed and `makemigrations --check --dry-run` reported no changes.
- PostgreSQL (`test_paper_factory` as `paper_factory_app`, PostgreSQL 18.6): `manage.py test sales --keepdb` ran 51 tests, passed, zero skipped. The focused inventory reversal tests ran 5 tests, passed, zero skipped. The full `manage.py test --keepdb` ran 176 tests, passed, zero skipped, and preserved the test database. `sales.0001_initial` and `sales.0002_sales_order_reopen` are recorded as applied to `test_paper_factory`. `paper_factory` was never connected to or modified.
- Sales tests cover customer validation and uniqueness, order totals and line validation, the full lifecycle and cancellation/completion restrictions, successful partial shipping through the shared inventory service with unit conversion, mixed-unit shipment valuation in both directions (cartons ordered/pieces shipped and pieces ordered/cartons shipped), insufficient stock, duplicate shipment/retry protection, whole-shipment rollback when a movement fails, PostgreSQL row-lock concurrency for duplicate references and over-shipping (including shared stock across different orders), inventory-reversal reconciliation, completed-order reversal and explicit audited reopen/re-ship/re-complete, reversal display, shipment immutability, staff-only access and navigation gating, CSRF rejection, and confirmation that sales prices do not alter costing valuation or `RawMaterialPrice` rows. Inventory tests cover rejection of reversal-of-reversal and duplicate reversal.

## Prompt 6 — secure mobile API foundation and owner dashboard data (implemented)

Implemented in this checkout:

- Added the `api` app with Django REST Framework 3.18.3 and drf-spectacular 0.30.0. The API is versioned under `/api/v1/`, uses token authentication only, JSON renderers/parsers, page-number pagination (maximum 100), request throttling, a stable error envelope, and staff-only OpenAPI schema/docs. Session authentication is not enabled for the API endpoints; the schema/docs views accept a staff session or token so they can be used in a browser.
- Added explicit roles: customers are active non-staff users linked one-to-one to an active `sales.Customer`; owners are active staff users using the existing internal/staff boundary. Staff cannot call customer endpoints, customers cannot call owner endpoints, unlinked users are rejected, and inactive customers are rejected.
- Customer endpoints cover the authenticated customer's own profile, products explicitly marked `Product.available_to_customers`, owner reference selling prices, the customer's own order list/detail, and draft order requests. Customer responses exclude inventory balances, low-stock thresholds, internal costs, supplier data, and costing records. Cross-customer order access returns `404`.
- Customer order requests are created through `sales.services.submit_customer_order_request`, which shares the draft-order writer with the staff sales service. They create a `DRAFT` order with `customer_submitted=true` and zero prices pending staff review; they do not confirm, reserve stock, create a shipment, or post inventory. The API rejects attempts to submit a customer, status, or price field. Customer order-request idempotency keys are not implemented.
- Customer-submitted orders cannot be confirmed while any line has a zero price, and shipment creation independently rejects an unpriced customer-submitted order (including a legacy/bypassed confirmed order). Customer `pricing_status` is `PENDING_REVIEW` only for an unpriced draft, `RECORDED` when all lines have positive prices, and `UNPRICED` for a legacy confirmed zero-priced order. The staff order list/detail and Django admin show the customer-submitted and pricing state.
- Added owner dashboard and section endpoints for sales, purchases, inventory by category, configured low-stock alerts, production status/output by line, recent sales/purchase/shipment documents, customer/supplier summaries, operational alerts, and persisted monthly costing outputs. Dashboard responses are recomputed from current persisted records and marked `Cache-Control: no-store`; no WebSockets, push notifications, or other real-time infrastructure was added.
- Dashboard document totals now include cancelled orders and fully reversed shipments/receipts, with separate `non_cancelled_orders_count`, `cancelled_orders_count`, `active_shipments_count`/`active_receipts_count`, and `fully_reversed_shipments_count`/`fully_reversed_receipts_count` fields and a `count_basis` object. Production `status_counts_basis` uses `ProductionOrder.created_at`, while `output_basis` uses `ProductionResult.occurred_at`; they are intentionally different cohorts.
- Costing projections expose persisted `labor_recorded`, allocation-policy/waste-policy labels, material valuation method, actual/assigned/waste material amounts, reconciliation flags, and per-product allocated labor/electricity/water/other components. The API performs only display-level arithmetic on stored rows.
- Added explicit catalog configuration fields `Product.available_to_customers` and `Product.low_stock_threshold` (zero disables the alert), a one-to-one `Customer.user` link, and `SalesOrder.customer_submitted`. Staff forms/admin expose the catalog and customer-link fields. Migrations `catalog.0003_product_available_to_customers_and_more` and `sales.0003_customer_user_salesorder_customer_submitted` were generated.
- Commercial sales/purchase document values are labelled non-accounting. The dashboard returns `null` for accounting profit, net profit, receivables, payables, and cash, with `accounting_available=false` and the missing dependencies. It never computes sales minus purchases as profit. Costing is read from persisted monthly records and is not recalculated by the API; electricity/labor allocation remains a costing-service result that is not accounting-final. Prompt 8 adds a separate persisted monthly management result and loss carry-forward, explicitly marked as non-accounting.
- Added API documentation in `API.md`, updated `README.md`, `DECISIONS.md`, `PROMPT_SPECS.md`, and `TODO.md`, and retained the existing web application and staff-only sales/customer pages.

### API verification

- `manage.py check` passed with no issues. `makemigrations --check --dry-run` reported `No changes detected`.
- Focused API tests: `manage.py test api --keepdb` ran 17 tests, passed, zero skipped.
- Full PostgreSQL suite (Prompt 6 baseline before 6.1 hardening): `manage.py test --keepdb` ran 193 tests, passed, zero skipped, and preserved `test_paper_factory`. The post-hardening result is 204 tests; see Prompt 6.1 below. The verified target was `DB_ENGINE=postgres`, `DB_NAME=test_paper_factory`, `DB_USER=paper_factory_app`; `paper_factory` was never connected to or modified.
- The test run applied `catalog.0003_product_available_to_customers_and_more`, `sales.0003_customer_user_salesorder_customer_submitted`, and the DRF authtoken migrations only to `test_paper_factory`. No production migration was run. The `api` app has no database models or migrations of its own.
- OpenAPI generation with `manage.py spectacular --validate` exited successfully, produced a 23,077-byte schema, and emitted zero warnings. The schema/docs endpoints were tested with staff tokens and staff sessions; customer sessions are rejected. The token endpoint is documented as unauthenticated JSON input and is rate-limited.
- API tests cover token issue/revocation, unauthenticated and role-boundary behavior, customer isolation, catalog/field exclusions, pagination and invalid-page/error envelopes, customer order-request validation and absence of stock/shipment effects, dashboard metrics against known records, configured low-stock alerts, production/costing projections, financial-integrity nulls, query validation, and no-store headers.

### Prompt 6.1 targeted hardening verification

- Remediated the pre-acceptance audit findings without changing the Prompt 6 architecture or adding Prompt 7 reporting/Flutter work.
- Added pricing guards to `sales.services.confirm_order` and `sales.services.create_shipment`; zero-priced customer-submitted orders remain `DRAFT`, and a legacy confirmed zero-priced order still cannot ship. Added staff list/detail/admin visibility and regression tests for draft request, blocked confirmation, blocked shipment, price review, successful confirmation, and customer `pricing_status` consistency.
- Dashboard counts now separate document totals from cancelled/non-cancelled orders and active/fully reversed shipments/receipts, with `count_basis`; production status/output date fields are explicit through `status_counts_basis` and `output_basis`. Added focused tests for cancelled orders, fully reversed shipment/receipt documents, and production output recorded in a different period from order creation.
- Costing now marks labor as a required monthly record (`labor_recorded`), with migration `costing.0006_costingperiod_labor_recorded` backfilling periods that already have a labor expense row. It blocks finalization for missing labor, fallback allocations, and missing usable good-unit costs, and exposes persisted allocation components/reconciliation through the API. Added focused tests for missing versus explicit-zero labor, line/product fallback allocations, no-good-unit costing, and separate-abnormal-waste reconciliation.
- Replaced the token endpoint's response schema with `TokenResponseSerializer` (an object with one string `token` field). `manage.py spectacular --validate` succeeded.
- Verification: `manage.py check` passed; `makemigrations --check --dry-run` reported no changes; focused API/sales/costing tests passed; the full PostgreSQL suite ran 204 tests with zero failures/skips and preserved `test_paper_factory`. `costing.0006_costingperiod_labor_recorded` is recorded as applied to `test_paper_factory`. `git diff --check` passed (Git emitted only existing LF-to-CRLF advisories). `paper_factory` was never connected to or modified and no commit/push was performed.

### API limitations and future dependencies

- No Flutter applications, push notifications, WebSockets, or live-update transport.
- No customer registration/password-reset, customer order cancellation, customer price-list administration, or order-request idempotency key.
- No authoritative accounting: invoices, supplier/customer credit notes, payments, tax, receivables, payables, cash, accounting periods, and postings remain unavailable.
- No inventory valuation, lot/serial tracking, stock reservation, or historical as-of-date stock balance. Low-stock thresholds are per product across all locations, not per warehouse/location reorder points.
- No production-line attribution on sales, purchasing, or shared inventory ledger records. Production reports can be filtered by line; shared records are never duplicated or artificially allocated to produce a line breakdown.
- No CORS policy; native mobile clients do not need one. A Flutter web client would require an explicit policy.
- Product prices exposed to customers are owner reference prices and their own order snapshots, not quotations; no cost or margin is exposed.

## Prompt 7 — consolidated and line-level reporting (implemented)

Implemented in this checkout:

- Added a read-only `reports` app with a shared service layer used by both the staff web reports and the existing owner API. It defines no models and writes no domain records; no parallel inventory/sales/purchasing ledger or duplicate posting logic was introduced.
- Added staff-only Arabic RTL web pages at `/reports/` for overview, sales, purchasing, inventory, production, and costing, with shared period/granularity filters, empty states, explicit unit labels, source/date/status bases, and data-quality warnings. Production also supports filtering by production line; sales, purchasing, and shared inventory are not artificially split because their records have no duplicated per-line ledger.
- Reused the existing `/api/v1/owner/` section endpoints and added paginated detail endpoints for sales orders, purchase orders, current stock, stock movements, production orders, and costing periods. The detail endpoints support the documented period/filter/pagination rules, reject customer users, and return the standard error envelope for invalid filters.
- Sales reporting separates commercial order value, net shipped value, total shipment value, and reversed value. Cancelled orders are excluded from active values and quantities but retained in audit counts. Shipments with reversed lines are counted separately as partially or fully reversed, and reversal lines are excluded from net totals to avoid double counting.
- Purchasing reporting applies the same rules to purchase orders and receipts. Receipt state and outstanding quantities are derived from active received quantities; cancelled orders and reversed receipt lines are excluded from active values and quantities.
- Inventory reporting reads current per-product balances from `inventory.services.ledger_balances` and movement activity from the immutable stock ledger. Active movement totals exclude reversal entries and original movements that have been reversed; reversal and reversed-original counts are reported separately. Current balances are explicitly not presented as historical as-of-date balances, and no inventory valuation is claimed.
- Production reporting covers order status counts by creation date, planned quantities, active material consumption by issue movement date, good/scrap output and yield by result date, cross-period output, open orders, orders without results, and recent activity. It reports output reconciliation by base UOM against the sum attributed to production lines with an explicit unallocated check. Quantity dimensions are never mixed.
- Costing reporting reads persisted `CostingPeriod`, `ProductCostingEntry`, and `ProductionOrderCostingEntry` records without recalculating product cost, allocation drivers, or expenses. It exposes period/product/order components, completeness, review flags, missing-data notes, and display-level reconciliation. Added persisted `electricity_recorded` and `water_recorded` snapshots plus migration `costing.0007_costingperiod_electricity_water_recorded`; a missing utility record differs from an explicitly recorded `0.00`.
- Reports return customer/supplier contact-master summaries, recent documents/activity, and `data_quality` warnings for low/negative stock, customer price review, unpriced legacy orders, missing utilities, incomplete/fallback costing, draft orders, open production, and missing production results. Informational warnings state that accounting and historical stock are unavailable.
- Commercial sales/purchase values remain explicitly non-accounting. No revenue, profit, receivables, payables, cash, WIP balance, allocation driver, or historical stock figure is invented. Report responses are request-time fresh with no caching, WebSockets, or push notifications.
- Updated `API.md`, `DECISIONS.md`, `README.md`, `TODO.md`, and this status document for the verified behavior. No Flutter work or Prompt 8 controls were started.

### Reporting verification

- `manage.py check` passed with no issues and `makemigrations --check --dry-run` reported `No changes detected`.
- Focused report tests passed: 13 tests covering sales, purchasing, inventory, production, costing, reversals, units, line filtering/reconciliation, web rendering/authorization, invalid-period fallback, and monthly-profit reporting/warnings.
- Focused owner API tests passed: 9 tests covering existing dashboard contracts plus detail-endpoint authorization, filtering, pagination, invalid status handling, and response fields.
- OpenAPI generation with `manage.py spectacular --validate` exited successfully with zero warnings. Explicit enum-name overrides were added for the sales, purchase, production, and costing status enums.
- Full PostgreSQL suite: `manage.py test --keepdb` ran 217 tests with zero failures and zero skips, preserving `test_paper_factory`. The verified target was `DB_ENGINE=postgres`, `DB_NAME=test_paper_factory`, `DB_USER=paper_factory_app`; `paper_factory` was never connected to or modified.
- The migration `costing.0007_costingperiod_electricity_water_recorded` is recorded as applied to `test_paper_factory`. No production migration was run.
- `git diff --check` passed apart from Git's existing LF-to-CRLF advisory warnings. Nothing was staged, committed, or pushed.

## Prompt 8 — monthly profit, operating expenses, and loss carry-forward (implemented)

Implemented in this checkout:

- Added persisted monthly management profit with `MonthlyProfitPeriod` and
  `MonthlyProfitCOGSLine`. Gross profit is net sales revenue minus cost of goods sold;
  the monthly result is gross profit minus recorded labor, electricity, maintenance, and
  other operating expenses. It is explicitly a management operating result, not accounting
  net profit, tax, or a distribution.
- COGS is calculated only for quantities actually shipped. It uses the latest persisted
  direct-material cost per good unit on or before the shipment month, never a new product
  cost calculation. Unsold manufactured inventory remains inventory and is not expensed.
  Labor, electricity, water, maintenance, and other overhead are not embedded in COGS, so
  operating expenses are deducted once rather than twice.
- Added the `MAINTENANCE` expense category and extended `MonthlyExpense` with expense and
  payment dates, reference, active/reversed status, reversal actor/time/reason, and
  updater audit fields. Active expenses are summed once per month and are not arbitrarily
  reallocated across production lines. A missing category is different from an explicitly
  recorded `0.00`; missing labor, electricity, maintenance, or other expenses block
  monthly-profit finalization. Water remains a recorded costing expense but is excluded
  from the agreed monthly profit calculation.
- Loss carry-forward is persisted from the latest finalized earlier month and continues
  across year boundaries without resetting. The period stores revenue, COGS, gross profit,
  each operating expense category, the monthly result, opening accumulated loss, loss
  recovered, closing accumulated loss, and profit remaining after recovery. Carried losses
  are not recorded as new expenses.
- Finalization is idempotent and blocked while data is incomplete. A later finalized month
  blocks finalizing or reopening an older month until the later month is reopened first.
  Reopening requires a reason and preserves the original finalization metadata plus reopen
  actor/time/reason. Finalized profit periods protect expenses, costing recalculation, new
  shipments in that month, shipment reversals, and deletion. Draft successor periods are
  recalculated against a newly finalized balance.
- Added staff web pages under `/costing/profit/` for listing, calculating, viewing,
  finalizing, and reopening monthly results, plus an audited expense-reversal page. Added a
  staff `/reports/profit/` report and profit warnings to the existing report data-quality
  panel. Extended the owner dashboard and added the read-only `/api/v1/owner/profit/`
  endpoint; all responses label the result as management data and do not claim final
  accounting net profit.
- Migration `costing.0008_monthly_profit_and_expense_controls` adds the new fields/models
  and backfills existing expense dates and direct-material unit-cost snapshots from already
  persisted amounts; it does not rerun costing or rewrite finalized product costs.

### Prompt 8 verification

- Required scenario passed: month 1 result `−EGP 20,000` with closing accumulated loss
  `EGP 20,000`; month 2 result `EGP 35,000` recovers `EGP 20,000`, closes the loss at
  `EGP 0`, and leaves `EGP 15,000` after recovery.
- Partial recovery passed: a `EGP 10,000` result against a `EGP 20,000` opening loss
  recovers `EGP 10,000`, closes at `EGP 10,000`, and leaves no remaining profit.
- Additional tests cover consecutive losses, a zero-result month, year-boundary
  carry-forward, profitable months with no prior loss, missing versus explicit-zero
  expenses, cross-period expense charging, unsold inventory, partial and credit sales,
  reversed shipments and cancelled orders, duplicate expense rows, labor double-counting,
  decimal precision, permissions, finalized-period protection, authorized corrections,
  incomplete costing, multiple expense scopes, reports, and API exposure.
- Full PostgreSQL suite: `manage.py test --keepdb` passed with zero failures and zero
  skips. The verified target was `DB_ENGINE=postgres`, `DB_NAME=test_paper_factory`,
  `DB_USER=paper_factory_app`; `paper_factory` was never connected to or modified.
  `costing.0008_monthly_profit_and_expense_controls` was applied only to
  `test_paper_factory`. No production migration was run.
- `manage.py check` passed, `makemigrations --check --dry-run` reported no changes, and
  OpenAPI schema validation passed. Nothing was staged, committed, or pushed.

## Prompt 9 — end-to-end audit (implemented)

An evidence-based audit of inventory, manufacturing, purchasing, sales, costing, operating
expenses, monthly profit, loss carry-forward, reports, and owner APIs. Confirmed defects were
fixed with regression tests; existing behavior was preserved.

Defects found and fixed:

- Ordered finalization: `finalize_monthly_profit` now refuses to finalize a month while an
  earlier `MonthlyProfitPeriod` is still DRAFT, so skip months cannot silently drop their
  losses from the accumulated balance. Reopening a finalized month refreshes draft successor
  balances via `_refresh_draft_successors`.
- COGS source integrity: reopening a costing period is blocked while a later finalized profit
  period uses it as a `MonthlyProfitCOGSLine.source_period`. Finalizing profit detaches draft
  source links before replacing entries, so finalized COGS rows stay frozen.
- Finalized costing months: `finalize_costing_period` recalculates a stale draft under the row
  lock when real production-order detail exists. New material issues, production results, and
  material-issue reversals are rejected when the movement month's costing period is FINALIZED.
- Duplicate expense submissions: the staff expense form posts an idempotency token stored as
  `submission_key`; a pre-check plus a unique constraint (migration
  `costing.0009_expense_submission_and_profit_year_constraints`) turns duplicate resubmissions
  into a user-side validation error instead of a second real expense. The same migration adds
  a database-level check that monthly profit year/month values are sane.
- Unit validation: `to_base_quantity` rejects inactive measurement units before computing a
  base quantity for any movement.
- Concurrency: the first monthly-profit calculation for a month now uses
  `get_or_create` under `select_for_update` so two concurrent calculations cannot race to
  create the same period row.

### Prompt 9 verification

- Focused suite `costing.tests_profit costing.tests manufacturing.tests inventory.tests`
  with `--keepdb`: 114 tests, zero failures. Full PostgreSQL suite
  `manage.py test --keepdb`: 253 tests, zero failures and zero skips. The verified target was
  `DB_ENGINE=postgres`, `DB_NAME=test_paper_factory`, `DB_USER=paper_factory_app`; the
  production database `paper_factory` was never connected to or modified. Migrations
  `costing.0007` through `costing.0009` are applied only to `test_paper_factory`; no
  production migration was run.
- `manage.py check`, `makemigrations --check --dry-run`, `manage.py spectacular --validate
  --fail-on-warn`, and `git diff --check` all passed.

Known limitations and accepted risks (not fixed, by design or deferred policy decisions):

- Period-open checks before movement writes are check-then-act; a concurrent finalization can
  still slip a dated movement into a month that finalizes moments later. The movement remains
  ledger-visible, and reopening the period recalculates the month.
- `MaterialIssue`, `FinishedGoodsReceipt`, `ProductionResult`, and `ProductionOrder` lack the
  ORM update/delete guards and database triggers the stock ledger has; direct ORM or SQL
  writes by trusted staff could still change costing inputs. Preventing this was deferred as
  a hardening decision, not a user-facing defect.
- `Product`/`StorageLocation` identity-stability and unit-dimension rules are enforced in
  model `clean()` (forms) only; raw queryset updates can bypass them.
- Cancelling a production order deliberately keeps issued materials and finished-goods
  receipts for audit instead of forced reversal; the costing model later counts those issues
  in the month issued.
- The web form token protects staff double-submission; the public API is read-only for
  mutations of financial data, so no API idempotency key was added.

## Remaining decisions and deployment work

- Confirm the factory's negative-stock policy, lot/serial requirements, and any additional authorization matrix before extending behavior.
- The local PostgreSQL password was supplied in chat and is short. Replace it with a strong unique value before deployment or exposing PostgreSQL beyond local development. The password is not recorded here or in project files.
- PostgreSQL listens on all interfaces. Confirm Windows firewall/network exposure before deployment.
- Configure the actual HTTPS proxy/host and explicitly decide HSTS before deployment. Non-debug mode already enables HTTPS redirects and secure session/CSRF cookies. Expose the mobile API only over HTTPS, keep tokens out of logs, and decide token lifetime/rotation before production use.
- If a Flutter web build is introduced, add an explicit CORS policy for the chosen frontend origins; native mobile clients do not require CORS.
- Use a current supported Python 3.12 patch for deployment and rerun checks.

The revised phase order remains: Prompt 0 foundation (closed) → Prompt 1 shared catalog/inventory (implemented) → Prompt 2 configurable manufacturing for both lines → shared accounting → purchasing (implemented) → sales (implemented) → Prompt 6 secure mobile API and owner dashboard data (implemented) → Prompt 7 consolidated and line-level reporting (implemented) → Prompt 8 monthly profit, operating expenses, and loss carry-forward (implemented). Cross-cutting deployment controls remain future work. See [PROMPT_SPECS.md](PROMPT_SPECS.md).
