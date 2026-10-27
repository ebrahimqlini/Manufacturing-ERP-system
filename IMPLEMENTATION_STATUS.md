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

- A Flutter Android client is implemented later in Prompt 13B. Push notifications, WebSockets, and live-update transport remain unimplemented.
- No customer registration/password-reset, customer order cancellation, customer price-list administration, or order-request idempotency key.
- No authoritative accounting: invoices, supplier/customer credit notes, payments, tax, receivables, payables, cash, accounting periods, and postings remain unavailable.
- No accounting inventory valuation, FIFO, or moving weighted average; per-material LPC snapshots are used only by production costing. No lot/serial tracking, stock reservation, or historical as-of-date stock balance. Low-stock thresholds are per product across all locations, not per warehouse/location reorder points.
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
- Updated `API.md`, `DECISIONS.md`, `README.md`, `TODO.md`, and this status document for the verified behavior. That phase did not start Flutter or Prompt 8 work; the Flutter client was delivered later (see Prompt 13B).

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

## Prompt 11 — manual machine counters, material scrap costing, monthly profit integration (implemented)

Workers read the physical machine counter (no connectivity or polling); recorded readings are
measurements only and never post stock movements or change the authoritative production
record. Added:

- `MachineCounterReading` (manufacturing) with PROVISIONAL → COMPLETE → APPROVED lifecycle
  (CORRECTED after an audited change, re-approvable), non-negative / end≥start /
  rejected≤output check constraints, continuation + reset-reason documentation for a counter
  that restarts backwards, one open interval per line/order with time-overlap rejection, exact
  unit conversion to base units (rejecting non-representable results), and mandatory-reason
  corrections archived in a `corrections` JSON audit list. Direct `save()`/`delete()` on
  recorded readings is blocked; services enforce the new `record_counter_reading`,
  `approve_counter_reading`, and `correct_counter_reading` permissions plus staff status.
- `ProductionLine.counter_behavior` (UNCONFIRMED default / COUNTS_ALL_OUTPUT /
  COUNTS_GOOD_ONLY): an unconfirmed counter is shown as a provisional physical measurement,
  never as verified good output. `MaterialIssue.roll_reference` is a document-only roll
  reference captured at issue creation.
- `costing/yield_services.py`: threshold classification (WITHIN/ABOVE/NO_THRESHOLD/
  NO_STANDARD/PENDING_APPROVAL/INSUFFICIENT_DATA) is operational only; scrap % is measured
  recorded scrap ÷ total measured output — never derived from yield shortfall. Issued-minus-
  expected material is labeled UNRECONCILED_MATERIAL and deliberately left unclassified —
  the data model cannot separate usable roll remnants from material consumed by recorded
  damaged units — so it is never treated as scrap, never valued, never deducted, and no
  sale/recycling rule is assumed. Valuation reuses `get_price_record_info`; the persisted
  `waste_material_cost` now comes from immutable material-specific waste records and their
  event-time cost snapshots. Missing review or valuation keeps affected periods incomplete;
  order-level output scrap remains an operational measure and is not used to infer raw-material
  damage.
- `RollYieldStandard` specification metadata (supplier text, specification reference,
  revision, effective date, target yield %, max scrap %, updated_by) with range constraints;
  `ProductCostingEntry` snapshots the exact standard used (FK, revision, thresholds) at
  calculation time so later spec edits never rewrite historical classifications.
- Monthly profit integration: `MonthlyProfitPeriod` gained `separated_scrap_loss`,
  `absorbed_scrap_cost` (display-only), `unreconciled_scrap_cost`, and `scrap_treatment`.
  New formula: `monthly_result = gross_profit − operating_total − separated_scrap_loss`.
  The separated scrap is deducted exactly once only when the period waste policy is
  SEPARATE_ABNORMAL, the costing period is FINALIZED and complete, the order-level entry is
  COMPLETE, and the order is COMPLETED (the `_costing_is_authoritative` gate); every other
  separated amount is reported as unreconciled — never deducted, never fabricated. Under the
  default ABSORBED_NORMAL policy nothing is deducted (the scrap already rides inside unit
  cost → COGS), and `absorbed_scrap_cost` shows a hypothetical split for visibility only.
  Water/labor/electricity/maintenance/other expense treatment, `is_complete`, and
  finalization protections are unchanged.
- Arabic RTL staff UI: counter start/close/approve/correct views + order detail section,
  line `counter_behavior` in the line form/admin, yield standard metadata fields, monthly
  yield/scrap report page (`costing:yield-report`), profit period scrap section, and report
  columns. Owner API extended without a new endpoint: `_profit_period_row`/`profit_report`
  now flow scrap figures, treatment labels, formula, and basis notes through `profit_payload`.

### Prompt 11 verification

- Business-rule review (only confirmed, actually damaged/unusable material is production
  waste): issued-minus-expected material is labeled UNRECONCILED_MATERIAL and deliberately
  left unclassified — the report note states both possible compositions (usable roll
  remnants or material consumed by recorded damaged units) and attaches no valuation,
  deduction, or sale/recycling rule; the scrap-vs-shortfall test now uses numerically
  distinct values (measured scrap 10.00% vs shortfall 18.18%) so a shortfall-derived
  implementation would fail; and a zero-damage test drives the order to COMPLETED and the
  period to FINALIZED through the approved services (`record_operation_progress` →
  `complete_operation` → `receive_finished_goods` → `complete_order` →
  `finalize_costing_period`) with every deduction gate open, then proves the deduction
  stays 0.00 while a 12 kg unexplained material difference exists.
- Focused suite `manufacturing.tests_counter costing.tests_yield_scrap
  costing.tests_scrap_profit --keepdb`: 67 tests, zero failures, exit code 0. Module suites
  `manufacturing costing reports --keepdb`: 191 tests, zero failures, exit code 0. Full
  PostgreSQL suite `manage.py test --keepdb`: 352 tests, zero failures, zero skips, exit
  code 0. Exit codes were captured from file-redirected runs printing `$LASTEXITCODE`; a
  PowerShell 5.1 wrapper quirk reports "Exited with code 1" for commands ending on a
  `2>&1 |` pipeline even though the Python process exits 0.
  Verified target before every DB command: `DB_NAME=test_paper_factory`,
  `DB_USER=paper_factory_app`, `127.0.0.1:5432`; production database `paper_factory` was
  never connected to or modified.
  Migrations `manufacturing.0009` and `costing.0011` are applied only to the test database.
- `manage.py check`, `makemigrations --check`, `spectacular --validate --fail-on-warn`,
  and `git diff --check` all passed with exit code 0.

Known limitations and accepted decisions (flagged for owner review):

- The deduction gate treats a draft costing period's separated scrap as unreconciled (visible
  but not deducted) until the costing period is finalized — the same authority gate COGS
  relies on; recalculation picks it up automatically afterwards.
- Threshold classification is operational only; financial treatment always follows the period
  waste policy, and the two can legitimately disagree (e.g. an above-threshold but absorbed
  scrap).
- Supplier on a yield standard is a free-text field (no purchasing FK by design); residual
  rolls are not inventory-tracked, so issued-minus-expected material stays
  UNRECONCILED_MATERIAL — reported as an unexplained difference, never classified.
- The issued-minus-expected reconciliation is measured against recorded good output only, so
  a positive difference can mix usable roll remnants with material consumed by recorded
  damaged units; the data model cannot separate them, so no classification is invented and
  nothing is valued or deducted from it.
- Prompt 12 supersedes the earlier Prompt 11 assumption that an issue-cost split could serve
  as waste valuation. No cost is allocated from output proportions; missing measured material
  evidence keeps the affected costing periods incomplete.
- Sale or recycling of usable leftover material: no classification, inventory, valuation, or
  accounting rule implemented — these rules are not yet specified.
- Counter starts lock the shared line row before overlap checks; a PostgreSQL concurrency
  regression test covers starts on different orders sharing a line.
- Monthly-profit completeness now includes unresolved separated scrap and missing material-waste evidence; finalization rejects incomplete periods after recalculation.
- Pre-Prompt-11 finalized profit periods keep their default scrap fields (zero/NONE) because
  they were computed before separate scrap recognition existed; their waste was absorbed
  under the default policy.

## Remaining decisions and deployment work

- Confirm the factory's negative-stock policy, lot/serial requirements, and any additional authorization matrix before extending behavior.
- The local PostgreSQL password was supplied in chat and is short. Replace it with a strong unique value before deployment or exposing PostgreSQL beyond local development. The password is not recorded here or in project files.
- PostgreSQL listens on all interfaces. Confirm Windows firewall/network exposure before deployment.
- Configure the actual HTTPS proxy/host and explicitly decide HSTS before deployment. Non-debug mode already enables HTTPS redirects and secure session/CSRF cookies. Expose the mobile API only over HTTPS, keep tokens out of logs, and decide token lifetime/rotation before production use.
- If a Flutter web build is introduced, add an explicit CORS policy for the chosen frontend origins; native mobile clients do not require CORS.
- Use a current supported Python 3.12 patch for deployment and rerun checks.

The revised phase order remains: Prompt 0 foundation (closed) → Prompt 1 shared catalog/inventory (implemented) → Prompt 2 configurable manufacturing for both lines → shared accounting → purchasing (implemented) → sales (implemented) → Prompt 6 secure mobile API and owner dashboard data (implemented) → Prompt 7 consolidated and line-level reporting (implemented) → Prompt 8 monthly profit, operating expenses, and loss carry-forward (implemented). Cross-cutting deployment controls remain future work. See [PROMPT_SPECS.md](PROMPT_SPECS.md).

## Prompt 12 — LPC provenance, measured waste valuation, and posting-time history

- Purchase receipts now update LPC only after the purchasing service has persisted a real
  `PurchaseReceiptLine` and its matching stock movement. The service checks the order, line,
  material, source identity, quantity, base quantity, unit, reference, positive price, and
  reversal state. Caller-provided source labels alone cannot establish receipt provenance.
- LPC history uses the movement's successful system `posted_at`; entered receipt dates cannot
  move prices backward. Historical issue lookups use this append-only history, and reversal
  events restore the latest still-valid receipt at reversal time or record no applicable price
  as `NULL` (never zero). Issue snapshots remain immutable and use a labeled dated-price fallback
  only when no LPC applies.
- `ProductionResult.posted_at` is authoritative for costing-period attribution; entered
  `occurred_at` stays operational. Legacy rows with null posting time are not backfilled and
  surface as incomplete. Measured material waste is linked to result/order/material and stores
  the event-time LPC or valid dated fallback snapshot. Explicit review records distinguish zero
  from missing evidence. No quantity is inferred from output rejection, counters, yield, or
  unexplained stock differences.
- Production-result corrections are full audited reversals; the original remains immutable and
  corrected output is entered as a new result. Reversed results are excluded from active output
  and costing reports. Existing finished-goods receipts must be reversed first when they exceed
  the remaining active output.
- At the time of this checkpoint, the new migration files were pending and the PostgreSQL driver
  had not loaded. The final remediation and successful verification are recorded below.

### Prompt 12 verification (2026-10-24)

- Focused PostgreSQL suite `costing.tests_last_purchase_cost
  costing.tests_yield_scrap costing.tests_scrap_profit manufacturing.tests_counter --keepdb`:
  100 tests passed, zero failures, exit code 0. A focused cross-month regression was rerun
  after its fixture correction and passed (1 test).
- Relevant app suite `inventory purchasing manufacturing costing reports api --keepdb` first
  ran 306 tests and exposed one legacy assertion expecting an affected scrap period to remain
  complete. The assertion was updated to require the estimate limitation note and incomplete
  status; its targeted regression passed. The final full-suite run below then passed all tests.
- Final PostgreSQL suite `manage.py test --keepdb`: 385 tests passed, zero failures, zero
  skips, exit code 0. The existing `test_paper_factory` database was preserved.
- `manage.py check`: exit code 0. `manage.py makemigrations --check --dry-run`: exit code 0,
  no changes detected. `manage.py spectacular --validate --fail-on-warn`: exit code 0.
  Python `py_compile` for all touched Python and migration files: exit code 0.
  `git diff --check`: exit code 0 (Git emitted only LF-to-CRLF advisories).
- Before every database-dependent command, effective settings and the live PostgreSQL
  connection were verified as database `test_paper_factory`, user `paper_factory_app`,
  host `127.0.0.1`, port `5432`. `paper_factory` was never connected to or modified.
- `showmigrations --plan` on that verified database showed all relevant migrations applied:
  `inventory.0006`, `inventory.0007`, `manufacturing.0009`, `manufacturing.0010`,
  `manufacturing.0011`, and `costing.0011` through `costing.0014`.

## Final backend remediation (2026-10-24)

- Added immutable `ProductionMaterialWaste` records tied to production result/order/material, with optional existing issue reference, measured base quantity, responsible actor, system posting time, and LPC or valid dated-fallback cost snapshots. Missing valuation stays null and makes costing incomplete. `ProductionWasteReview` is a separate immutable review event, so reviewed zero is distinct from missing review. Staff can record waste or explicitly confirm no material waste from the order result page; the costing report exposes the measured event values.
- Replaced proportional scrap cost allocation in monthly costing with the measured event snapshot. Under the separate policy, measured waste is separated from good-unit material cost and remains subject to the period close; under absorbed policy it remains in production cost and is not deducted again. Missing review or valuation keeps costing incomplete. Waste reversals preserve the original price snapshot.
- Monthly profit completeness now includes unresolved waste evidence and unreconciled separated scrap; the finalizer recalculates and rejects incomplete results with detail in its error. An active measured waste event counts as evidence; explicit zero review is only available when no active waste event exists.
- Added an audited, immutable full reversal for a production result. The original remains present; a corrected result is entered as a new result. Costing, yield, order totals, and production reports omit reversed results. Reversal checks both applicable costing and profit-period locks.
- Counter starts now lock the shared production-line row before overlap checks. A PostgreSQL-only concurrency regression test was added.
- Migrations: `manufacturing.0012_productionmaterialwaste_and_review`, `manufacturing.0013_productionresultreversal`, `manufacturing.0014_productionresult_posted_at_server_default`, and `costing.0015_measured_scrap_cost_breakdown`. All four were applied by the PostgreSQL test runner to the verified test database. The post-test migration plan has no pending operations.
- Production results are assigned to costing periods by `posted_at`, not operator-entered `occurred_at`. Legacy rows with null `posted_at` are not silently backfilled and are surfaced as incomplete when their entered date falls in a reviewed period.

Verification results (2026-10-24):

- Driver diagnosis: the earlier failure indicated that psycopg 3 had no usable libpq implementation and no psycopg2 fallback. In this run, the existing `.venv` already contained `psycopg==3.3.6` and `psycopg-binary==3.3.6`; importing `psycopg.pq` selected the binary implementation and reported libpq 18.4. No driver reinstall or system libpq change was needed. `requirements.txt` now keeps the reproducible `psycopg[binary]==3.3.6` declaration and removes a redundant direct pin of the same binary distribution. `pip check` passed.
- Before database work, Django's effective settings and a live server query confirmed PostgreSQL database `test_paper_factory`, role `paper_factory_app`, address `127.0.0.1`, port `5432`; `TEST.NAME` was also `test_paper_factory`. No credential values were printed.
- The migration plan before tests showed exactly the four migrations listed above. They contain only `CreateModel`, `AlterField`, and `AddConstraint` operations; there are no data migrations, raw SQL, deletes, or field removals. The test runner applied them. A later read-only migration plan showed no pending operations, and all four are recorded in `django_migrations`.
- Focused PostgreSQL regression suites: 273 tests passed, zero failures, zero errors, zero skips. A subsequent single concurrency retest also passed.
- Full PostgreSQL suite, `.venv\Scripts\python.exe manage.py test --keepdb`: 389 tests passed, zero failures, zero errors, zero skips. The existing test database was preserved.
- `manage.py check`: passed. `manage.py makemigrations --check --dry-run`: passed, no changes detected. `manage.py spectacular --validate --fail-on-warn`: passed with zero warnings. Python `compileall`: passed. `pip check`: passed. `git diff --check`: passed (Git emitted only line-ending advisories).
- Verification fixes: active measured waste now satisfies the evidence gate; reviewed zero cannot be recorded over an active measured event; reversed waste does not satisfy active evidence; regression fixtures distinguish output scrap from physically measured material waste and attribute backdated events by actual system posting time.
- The live query and database-backed commands used only `test_paper_factory`; production database `paper_factory` was never connected to or modified. Original and final HEAD are unchanged at `a8c52beb9fa97411242ce932fa70c8be2101cae6`. Nothing was staged, committed, pushed, reset, stashed, or discarded.

## Prompt 13A — API authorization and employee operational contract (2026-10-24)

- Replaced the `is_staff` owner API boundary with explicit roles: owners are active superusers or active members of Django group `ERP Owners`; operational API users must be active staff members of `ERP Operational Staff`. Customer checks remain isolated to active non-staff users linked to an active customer. Unauthenticated access remains denied. A normal staff account with neither role is denied both internal API groups.
- No user schema migration or automatic role promotion was added. Before rollout, an administrator must explicitly assign existing owners to `ERP Owners` (or confirm superuser status) and employees to `ERP Operational Staff`, with only the required model permissions. This preserves existing user rows and avoids granting every staff account owner access.
- Added `/api/v1/employee/` routes for active production orders and their operations, material requirements, current output/counters/waste; operation progress and completion; production result posting and audited reversal; measured material waste, reviewed-zero evidence, and waste reversal; raw-material balances per active location; material issues; counter start/close/approve/correct; and finished-goods receipts. All write views require the employee group and call existing domain services, which continue enforcing model permissions, state/quantity rules, period locks, idempotency/references, concurrency and ledger invariants.
- Employee input serializers reject undeclared fields. Actor identity comes from the token; timestamps and cost snapshots are server-derived. Employee responses do not expose cost or profit fields. No purchasing, sales, or financial writes were added.
- PostgreSQL verification used only `.env` values after a live connection check confirmed `test_paper_factory`, `paper_factory_app`, `127.0.0.1:5432`; no production database connection was made. Full API app tests: 27 passed. Final role/employee adapter subset: 8 passed. Focused existing manufacturing/counter service regressions: 16 passed. Django `check`, migration consistency (`makemigrations --check --dry-run`), strict OpenAPI validation, Python compilation, and `git diff --check` passed. No migration changes were required.
- This change updates the API contract only; it does not create mobile UI. Existing owner accounts need explicit group assignment before the role change is deployed.

## Prompt 13B — Flutter Android MVP against the real API (2026-10-26)

- Added the Flutter Android client in `mobile/`. It authenticates through `/api/v1/auth/token/`, discovers the effective role from `/api/v1/auth/me/`, and routes to owner, operational-employee, or customer screens. Failed profile discovery and API `401` responses clear the token instead of leaving a half-authenticated session.
- Added a `TokenStore` abstraction. Production uses `flutter_secure_storage` 11.2.0 backed by the Android Keystore; tests inject `InMemoryTokenStore`. The token is not written to `shared_preferences`, and there is no plaintext fallback. Language and the configurable API base URL remain non-secret preferences.
- API URL defaults and presets include `/api/v1`, and the client normalizes either `http://host:8000/api/v1/` or `http://host:8000/` without duplicating the version segment. The emulator default is `http://10.0.2.2:8000/api/v1/`. Release builds reject non-HTTPS URLs.
- Android configuration: `allowBackup="false"` on the application, cleartext HTTP enabled only in `src/debug/AndroidManifest.xml`, and no cleartext override in the main/release manifest. The effective debug manifest has `minSdkVersion=24`, `targetSdkVersion=36`, `allowBackup=false`, and `usesCleartextTraffic=true`; the main manifest has no cleartext flag.
- Owner screens read persisted dashboard, costing, inventory, sales, purchasing, production, and monthly-profit payloads, including the real `net_shipped_value`, `net_received_value`, `range_totals`, `financial_integrity`, and `low_stock_alerts` keys. Employee screens use real production orders, requirements, counters, materials, and locations for issues, measured waste, results, finished receipts, and counter lifecycle actions. Customer screens use real catalog products and units and submit draft order requests. No unit, product, order, requirement, counter, or location ID is hardcoded.
- Added additive backend IDs required by the client: requirement `unit_id`, employee order `planned_unit_id`, material `base_unit_id`, and customer unit/product/reference-price `id` values. Focused API assertions verify these fields and the employee role boundary.
- Localization was completed for the mobile screens in Arabic RTL and English LTR; no visible screen string remains hardcoded in Arabic except the language names themselves.
- Windows build note: the pub cache and project can live on different drives, which makes Kotlin incremental caches fail with a different-roots error. `mobile/android/gradle.properties` disables Kotlin incremental compilation for reproducible local builds. NDK `28.2.13676358` and CMake `3.22.1` were installed into the local Android SDK to complete the debug build.

### Prompt 13B verification

- `flutter pub get`: exit code 0; `flutter_secure_storage` resolved to `11.2.0`.
- `flutter analyze`: exit code 0, `No issues found!`.
- `flutter test --reporter compact`: exit code 0, 23 tests passed.
- `flutter build apk --debug`: exit code 0. APK: `mobile/build/app/outputs/flutter-apk/app-debug.apk` (188,189,960 bytes).
- Focused PostgreSQL suite `costing.tests_last_purchase_cost costing.tests costing.tests_yield_scrap costing.tests_scrap_profit manufacturing.tests_counter manufacturing.tests purchasing.tests inventory.tests api.tests sales.tests reports.tests` with `--keepdb`: 326 tests passed, zero failures, exit code 0 (2026-10-27).
- `manage.py test --keepdb`: 394 tests passed, zero failures, zero errors, zero skips (verified rerun on 2026-10-27 took 753.532 seconds); `test_paper_factory` was preserved. The test database was `test_paper_factory` with role `paper_factory_app` at `127.0.0.1:5432`; production `paper_factory` was not accessed.
- `manage.py check`, `manage.py makemigrations --check --dry-run`, `manage.py spectacular --validate --fail-on-warn`, Python compilation, `pip check`, and `git diff --check` passed. No new migration was required for the additive serializer fields.
- Remaining limits: the verified mobile build is Android debug only; iOS, Flutter web, and a signed release APK are not built. True per-material physical scrap attribution still depends on measured waste records and is not inferred from shortfall, counters, leftovers, or unexplained stock differences.
