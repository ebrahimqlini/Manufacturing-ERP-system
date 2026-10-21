# TODO and implementation sequence

## Foundation

- [x] Initialize Git and create the minimal Django foundation.
- [x] Pin runtime dependencies and document Python interpreter selection.
- [x] Verify SQLite and PostgreSQL checks, migrations, rollback-safe database read/write, and tests.
- [x] Record both production lines and their still-open process requirements without adding business functionality.
- [ ] Before deployment, rotate the short local PostgreSQL password, confirm network exposure, configure HTTPS/HSTS, and test a current Python patch.
- [ ] Before operational stock use, create an authorized staff administrator and have the factory confirm lot/serial requirements.

## Prompt 1 — shared catalog and inventory (implemented)

- [x] Implement configurable product categories for raw materials, WIP, finished goods, packaging materials, scrap, and consumables.
- [x] Implement configurable units of measure and product-specific Decimal conversions with a documented 12-decimal precision limit and reject-on-overflow behavior.
- [x] Implement shared warehouses/locations and one immutable stock ledger for both production lines.
- [x] Test both product families, shared stock, movements/reversals, conversions, atomicity, constraints, authorization, and PostgreSQL behavior.
- [x] Keep manufacturing, purchasing, sales, and accounting workflows outside Prompt 1.
- [x] Enforce the confirmed no-negative-physical-stock policy transactionally for issues, transfers, decreasing adjustments, and reversals; allow later valid partial production issues without creating shortage movements.
- [x] Guard posted movement and ledger immutability through supported ORM operations and PostgreSQL UPDATE/DELETE triggers; record remaining privilege-level bypass limits.

## Prompt 2 — configurable manufacturing (implemented; verification in IMPLEMENTATION_STATUS.md)

- [x] Add configurable production lines, product-specific versioned BOMs/routings, orders, operation progress, production quantities, scrap, and line attribution.
- [x] Configure paper-cup routes from product-specific printing/cutting/forming/optional bottom assembly/packaging operations.
- [x] Record the confirmed fiber operation (pressing fiber material in a mold) without inventing other stages, materials, machines, units, or rates; keep product BOMs/routes configurable.
- [x] Reuse the shared inventory ledger for partial material issues and separate finished-goods receipts; record shortages as information, not movements.
- [x] Test route/BOM snapshots, partial issues, shortage and stock limits, good/scrap quantities, finished receipts, order limits, authorization, and database behavior.
- [x] Defer WIP balances/transfers because factory policy is unconfirmed.
- [x] Implement monthly product costing (actual material issues with owner-entered provisional prices; labor/electricity/water/other allocated by policy; waste handling; per-product/per-order breakdowns; cross-month comparison; draft/finalize/reopen).
- [ ] Obtain owner/accountant approval of the material valuation method and waste treatment before treating costing output as accounting-final; confirm the WIP location/transfer policy and fiber inputs/stages before extending workflows.

## Production costing — monthly product cost (implemented; verification in IMPLEMENTATION_STATUS.md)

- [x] Add `costing` models, services, views, admin, Arabic RTL templates, and migrations for monthly product costing.
- [x] Ensure labor, electricity, and water are recorded per month and flagged (not assumed zero) when missing for a producing month; show allocated amounts separately, and keep a confirmed `0.00` distinct from a missing record.
- [x] Store explicit period start/end dates and attribute material, output, and expenses by them; recalculating one month replaces its entries without touching other months.
- [x] Allocate factory/line/product-scoped expenses by the selected policy with cent-exact reconciliation and no double counting; mark fallback allocations and missing good-unit costs as review-required rather than complete.
- [x] Provide per-product and per-order breakdowns plus a per-product cross-month comparison with month-over-month changes; expose persisted allocation components and reconciliation in the API.
- [x] Restrict every costing page that exposes financial data to staff users or users explicitly granted the `costing.view_costing_financials` permission (migration `0004`); write actions remain staff-only.
- [x] Applied the approved costing migrations (`0002`–`0005`) to the shared test database `test_paper_factory` and ran the full suite there with `--keepdb` (85 tests, zero skips, database preserved). `paper_factory` was never targeted.

## Purchasing and supplier management (implemented; verification in IMPLEMENTATION_STATUS.md)

- [x] Add `purchasing` models, services, views, admin, Arabic RTL templates, and migration `0001_initial` for suppliers, purchase orders, and receipts.
- [x] Keep purchase orders as commercial documents and post receipts only through the shared inventory ledger with unit-conversion validation and atomic rollback.
- [x] Support partial/complete receiving with derived received/outstanding quantities, duplicate-reference and over-receipt prevention, and reversal-based correction.
- [x] Snapshot purchase prices for traceability without changing `RawMaterialPrice` or the costing valuation method.
- [x] Restrict all purchasing pages and writes to staff users and hide the navigation link from other users.
- [x] Ran `manage.py check`, `makemigrations --check --dry-run`, and the full PostgreSQL suite with `--keepdb` (123 tests, zero skips, `test_paper_factory` preserved; `paper_factory` never targeted).

## Sales and customer management (implemented; verification in IMPLEMENTATION_STATUS.md)

- [x] Add `sales` models, services, views, admin, Arabic RTL templates, and migrations `0001_initial`/`0002_sales_order_reopen` for customers, sales orders, and shipments.
- [x] Keep sales orders as commercial documents and post shipments only through the shared inventory ledger with unit-conversion validation and atomic rollback.
- [x] Support partial/complete shipping with derived shipped/outstanding quantities, duplicate-reference and over-shipment prevention, and reversal-based correction.
- [x] Calculate shipment value from base quantity × snapshotted base price, with correct mixed-unit valuation in both directions.
- [x] Support explicit, audited reopening of a completed order when a reversal leaves outstanding quantity, then re-ship and re-complete it.
- [x] Prevent a reversal movement from being reversed again, keeping sales reconciliation terminal and unambiguous.
- [x] Snapshot sales prices for traceability without changing `RawMaterialPrice`, production cost, or the costing valuation method.
- [x] Restrict all customer and sales pages and writes to staff users and hide the navigation link from other users.
- [x] Ran `manage.py check`, `makemigrations --check --dry-run`, focused sales/inventory tests, and the full PostgreSQL suite with `--keepdb` (51 sales tests, 176 total, zero skips, `test_paper_factory` preserved; `paper_factory` never targeted).

## Prompt 6 — secure mobile API foundation and owner dashboard data (implemented; verification in IMPLEMENTATION_STATUS.md)

- [x] Add Django REST Framework 3.18.3 and drf-spectacular 0.30.0 with a versioned `/api/v1/` namespace, token authentication, pagination, throttling, stable errors, JSON-only API responses, and staff-only schema/docs.
- [x] Add explicit customer and owner/staff permissions. Customer accounts are active non-staff users linked to active customer records; owner endpoints use the existing staff boundary. Customer and owner roles cannot use each other's endpoints.
- [x] Add customer profile, authorized product catalog, reference-price, own-order list/detail, and draft order-request endpoints. Enforce customer isolation and exclude internal costs, balances, supplier data, and costing records.
- [x] Add customer order requests through the sales service as draft-only orders with zero prices pending staff review; verify no confirmation, stock reservation, shipment, or inventory posting occurs.
- [x] Block confirmation and shipment of customer-submitted orders while any line is unpriced; expose `PENDING_REVIEW`/`RECORDED`/`UNPRICED` pricing status in customer responses and staff list/detail/admin views.
- [x] Add owner dashboard and section endpoints for sales, purchases, inventory by category, configured low-stock alerts, production by line, recent documents, customer/supplier summaries, persisted monthly costing outputs, and current operational alerts.
- [x] Separate total document counts from non-cancelled/active counts, expose cancelled/fully reversed counts and `count_basis`, and state the explicit production order-created vs output-recorded date bases.
- [x] Keep dashboard responses cache-free and computed from current persisted data; do not introduce WebSockets, push notifications, or live-update claims.
- [x] Return `null` and explicit missing dependencies for profit, receivables, payables, and cash; never calculate sales minus purchases as profit.
- [x] Add `Product.available_to_customers` and `Product.low_stock_threshold`, link `Customer.user`, and record `SalesOrder.customer_submitted` through migrations; update staff forms/admin.
- [x] Add API tests for authentication, role boundaries, customer isolation, catalog exclusions, pagination, invalid input/error envelopes, order-request workflow effects, dashboard metrics, low stock, production, persisted costing, financial-integrity nulls, and no-store behavior.
- [x] Ran `manage.py check`, `makemigrations --check --dry-run`, focused API tests, and the full PostgreSQL suite with `--keepdb` (193 tests, zero skips, `test_paper_factory` preserved; `paper_factory` never targeted).
- [ ] Future mobile work (not Prompt 6): Flutter apps, push/live refresh transport, customer order-request idempotency keys, customer registration/password reset, and any customer-facing order cancellation policy.
- [ ] Future accounting work: shared chart of accounts, invoices, returns/credit notes, payments, tax, receivables, payables, cash, and validated postings. Until then dashboard profit/position metrics remain intentionally unavailable.

## Prompt 6.1 — targeted hardening (implemented; verification in IMPLEMENTATION_STATUS.md)

- [x] Guard customer-submitted order confirmation and shipment against zero/unreviewed prices; keep customer pricing status consistent with order status.
- [x] Show customer-submitted and pending-pricing state in staff order list, order detail, and Django admin.
- [x] Add regression tests for draft requests, blocked confirmation, blocked shipment, price review, and successful confirmation after valid pricing.
- [x] Separate dashboard document totals from cancelled/active/reversed counts and document production status/output date bases with focused tests.
- [x] Expose persisted costing allocation/reconciliation fields, require explicit labor records, mark fallback/missing-good-unit results incomplete, and test missing/zero labor, fallbacks, and separate abnormal waste.
- [x] Add a dedicated token response schema and validate the OpenAPI schema.
- [x] Ran focused tests, full PostgreSQL suite with `--keepdb` (204 tests, zero skips), `manage.py check`, `makemigrations --check --dry-run`, schema validation, and `git diff --check`; no commit or push.

## Later prompts

- [ ] Shared accounting foundation with consolidated and production-line cost dimensions (originally labelled "Prompt 3"; the production-costing work above is the separate costing prompt).
- [x] Prompt 4: shared supplier/purchasing/receiving workflows against the common inventory ledger (implemented; see the section above). Supplier invoices, payments, tax, and accounting postings remain deferred to the accounting phase.
- [x] Prompt 5: shared customer/sales/delivery workflows against the common inventory interface (implemented; see the section above). Customer invoices, returns, payments, tax, and accounting postings remain deferred to the accounting phase.
- [x] Prompt 6: versioned mobile API foundation and owner dashboard data (implemented; see the section above). Reporting is not implemented by this API phase.
- [ ] Prompt 7: read-only factory-wide and production-line reports, with reconciled consolidated totals.
- [ ] Prompt 8: approved authorization matrix, audit review, backups/restore, monitoring, and deployment hardening.

See [PROMPT_SPECS.md](PROMPT_SPECS.md) for scope, acceptance gates, and required tests for every planned prompt. See [DECISIONS.md](DECISIONS.md) for architecture and unresolved factory/accounting policies.
