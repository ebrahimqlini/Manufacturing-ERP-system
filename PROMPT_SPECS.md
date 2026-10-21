# Future implementation prompt specifications

These phase contracts record the approved scope. Prompts 1 and 2 (catalog/inventory and configurable production), monthly production costing, Prompt 4 (purchasing), Prompt 5 (sales), and Prompt 6 (secure mobile API and owner dashboard data) are implemented in this checkout; the remaining prompts are planned. Every phase must stay within its prompt and must not silently decide open factory or accounting policies.

## Shared factory scope

The ERP serves two production lines:

- **Paper cups.** Configure product-specific operations from the confirmed process: printing, cutting, cup forming, bottom assembly where applicable, and packaging.
- **Paper-fiber food containers/trays.** The confirmed process fact is pressing fiber material in a mold. Do not infer drying, pulp production, heating, trimming, other stages, raw-material specifications, machines, units, yields, or rates. Keep product BOMs and routings configurable and unpopulated until factory data is supplied.

Catalog, inventory, warehouses, stock ledger, purchasing, suppliers, customers, sales, accounting, and reporting are shared across both lines. Never implement separate copies of those modules or ledgers per line. Production line is an attribution/configuration dimension, not a second stock system. Use `Decimal` values for exact quantities and money. Do not choose valuation, costing, rounding, tax, or accounting-posting policies without owner/accountant approval. The factory has confirmed that physical inventory must never become negative; this does not require a production order's full planned materials to be available.

## Prompt 1 — Catalog, units, warehouses, and shared inventory

Implement the shared catalog and inventory foundation only.

### Required design

- Configurable product records and categories covering at least raw materials, work in progress, finished goods, packaging materials, and scrap. Keep product category distinct from production-line identity.
- Configurable units of measure and conversions where appropriate, with explicit dimensional compatibility, product/base-unit rules, Decimal arithmetic, and documented precision. Do not assume units or conversion factors for either product line.
- One shared warehouse/location structure and one shared immutable stock movement/ledger system for both lines. Record movement source, destination, quantity, base UOM, time, actor, and source reference as appropriate. Corrections use explicit reversals; do not edit posted movements.
- Shared inventory services enforce atomic transfers and keep balances derived/reconcilable from the common ledger. Do not split stock tables, balances, or movement services by line.
- Leave production-line-specific routing and cost behavior to Prompt 2. Keep inventory interfaces ready for production source references without importing nonexistent manufacturing models.
- Do not implement purchasing, production, sales, accounting, or reports in this prompt.

### Required tests

- Products for both product families coexist in the same catalog, warehouse/location system, and ledger; shared raw/packaging stock is not duplicated per line.
- Stock receipt, issue, transfer, and reversal produce correct ledger-derived balances and are atomic on failure. Exercise two products from different families in a shared warehouse.
- Configured UOM conversions preserve exact Decimal quantities, reject incompatible conversions, and cover fractional quantities. Test boundary/rounding behavior only after the accepted precision rule is recorded.
- Invalid/duplicate movement references and constraints are rejected. Issues, transfer source legs, decreasing adjustments, and reversal legs validate ledger balance inside the transaction after locking relevant product/location rows. Test competing issues against PostgreSQL to ensure successful issues never exceed available stock.
- Posted movements and ledger entries are immutable through supported ORM/admin paths; PostgreSQL triggers reject direct UPDATE/DELETE. Table owners may disable triggers, and TRUNCATE/unauthorized direct INSERT remain subject to database privilege controls.
- Run these tests against SQLite for quick coverage and PostgreSQL for transaction/constraint behavior.

### Implemented decisions and remaining policy gates

- Categories are configurable, seeded with the required shared inventory categories, and independent of production-line identity. SKU is unique in the shared catalog.
- Unit dimensions are configurable text keys; alternate conversions are product-specific and express base units per alternate unit. Quantities and factors use `Decimal` with 12 fractional places; conversions that exceed that precision are rejected without rounding.
- Stock is shared across both product families. A posted movement header produces immutable signed ledger entries; transfers write both legs atomically. Balances are aggregated from ledger entries. Unique references prevent duplicate posting; corrections use a linked reversal.
- Confirmed policy: physical stock cannot go negative. Insufficient issue, transfer, adjustment, or reversal requests are rejected without partial records. A production order may still be released with shortages; manufacturing tracks planned, issued-to-date, and outstanding material separately and permits successive partial issues up to available stock. For example, with 120 kg planned and 100 kg available, up to 100 kg may be issued while 20 kg remains outstanding; the shortage itself is not a stock movement.
- Prompt 1 safety follow-up: service validation is performed from the shared ledger after product/location locking inside the posting transaction; PostgreSQL concurrent issue coverage passed. ORM update/delete/bulk_update are rejected for posted records, and a PostgreSQL migration installs direct UPDATE/DELETE triggers. Table-owner privileges, trigger disabling, TRUNCATE, and unauthorized direct inserts remain outside these protections.
- A generic optional, unique source-reference pair is available for future production posting events without an inventory-to-manufacturing foreign key.
- Lot/serial tracking and valuation/costing are not implemented or assumed. No factory-specific UOM, conversion factor, rate, or fiber process is seeded.

## Prompt 2 — Configurable manufacturing for both production lines

Implement manufacturing on top of Prompt 1's shared catalog and inventory services. No purchasing, sales, full accounting, or reports implementation in this prompt. No WIP balance/transfer policy or costing method is confirmed, so do not invent WIP inventory balances or cost calculations.

### Required design

- Configurable production-line identities and product-specific, versioned BOMs and routings. A product/order selects the applicable approved BOM and routing revision; do not create line-specific copies of inventory or accounting.
- Production orders with draft/released/in-progress/completed/cancelled states; snapshot the selected BOM and routing when released. Record planned, issued, and remaining material requirements separately. Allow successive partial issues; shortages are informational and never stock movements. Use the shared inventory posting service for material issues and separate partial finished-goods receipts.
- Record operation progress, good output, and scrap against an order. Enforce order quantity limits and route sequence. Do not create WIP balances or transfers until factory confirms locations and transfer policy. Preserve the history of posted inventory events and cancellation.
- Paper-cup routes must be configurable to represent printing, cutting, cup forming, optional bottom assembly, and packaging. Only include the operations that apply to the specific product and confirmed factory route.
- Fiber-container/tray routes are generic/configurable; pressing fiber material in a mold is the only confirmed operation. Do not add drying, pulp production, heating, trimming, packaging, machinery, raw materials, units, yields, or rates without factory confirmation.
- Attribute orders, quantities, movements, and scrap records to the selected production line. Keep stock in the shared catalog/warehouse/ledger. Do not implement costing until approved method and cost inputs are specified.
- Production transactions are atomic and auditable; order snapshots preserve selected BOM/routing and base-unit meanings, including whether a route step is optional (required by default). Serialize release with all configuration edits to prevent mixed header/child snapshots. Prevent over-issue, overproduction, duplicate posting, cross-order records, and negative stock. Order creation/release does not require full material availability; shortage is informational. Machine scheduling is out of scope.
- Progress and result forms use stable UUID submission keys: exact retries are idempotent and reuse with a different payload is rejected. Progress quantities are incremental; cumulative quantity at each step cannot exceed the planned quantity or the nearest preceding non-skipped step's cumulative quantity. Only explicitly optional route steps may be skipped.
- Reversing a finished-goods inventory movement never silently reopens a completed order. Replacements use an explicit receipt operation linked to the reversed original, with net receipts bounded by recorded good output. Preserve both original and replacement audit history.

### Required tests

- **Paper cups:** test-configured products cover printing, cutting, forming, optional bottom assembly, and packaging; prove omission of inapplicable bottom assembly and keep route order product-specific.
- **Fiber containers/trays:** configure only pressing fiber material in a mold as confirmed. Use generic test BOM data without introducing unconfirmed production facts or expected rates.
- **Shared inventory and movements:** verify both lines use the same warehouse and ledger, material issues and finished receipts reconcile, partial issues and shortages remain independent, and rejected/duplicate movements leave no partial records.
- **Production, scrap, costing, and isolation:** verify good and scrap quantities, partial finished receipts, order quantity limits, BOM/routing snapshots, and no cross-line attribution. Do not test WIP transfer or cost calculations as implemented features; add those acceptance tests after policies are confirmed.
- Test permissions on every write endpoint; route sequence; optional-step skip policy; incremental progress limits; stable-key replay and payload mismatch; receipt reversal/replacement; transaction rollback; PostgreSQL concurrency for material issues, output limits, and release versus BOM/routing edits.

### Acceptance gate

Confirm cup product-specific route applicability, fiber materials/equipment/units and any stages beyond pressing in a mold, BOM ownership/versioning, WIP locations/transfers, scrap disposition, quality controls, and costing method/inputs. Production configuration remains generic until product-specific factory facts are provided.

## Production costing — monthly product cost (implemented)

Calculate per-product manufacturing cost for each calendar month from actual production and expense data, without creating WIP balances or accounting entries. This is the production-costing prompt; the shared-accounting phase below remains a separate later step.

### Required design

- Material cost is derived from actual material issues dated within the month (reversed issues excluded), priced with owner-entered `RawMaterialPrice` rows. Because no valuation method is approved and the ledger carries no cost, the price is a labelled provisional standard; FIFO/weighted average are not used, and a missing or future-dated price is reported and marks the period incomplete instead of being silently reused.
- Record monthly operating expenses with a type (labor, electricity, water, other industrial) and a scope (factory, production line, or product). Labor, electricity, and water are required for any producing month; a missing record is flagged and blocks finalization rather than being assumed zero. A deliberately recorded `0.00` is a confirmed zero, kept distinct from a missing one. Bills are attributed wholly to the operator-selected month, and the model documents that a bill's period may differ from its consumption month.
- Each period stores explicit `start_date`/`end_date` boundaries; material issues and production results are attributed by those boundaries, and recalculation replaces a month's entries without affecting other months.
- Allocate factory-wide expenses with the period policy (units produced, material cost, material weight, or equal-per-line then units); attribute line- and product-scoped expenses directly. Allocation parts must sum exactly to each recorded bucket total.
- The waste policy must change the computed result: absorbed normal waste loads full material cost on good units; separate abnormal waste splits material cost between good output and recorded scrap in proportion to recorded quantities and shows it separately.
- Product entries are computed from production-order entries so order, product, and period totals reconcile exactly. Provide per-product and per-order breakdowns, recorded-vs-allocated reconciliation, separate labor/electricity/water columns, and a cross-month comparison that selects one product and shows its monthly material/labor/utility/other/waste/total, cost per good unit, production and waste quantities, and month-over-month changes. Fallback allocations and missing good-unit costs remain explicitly incomplete. Periods move draft → finalized, and reopening a finalized period requires a recorded reason.
- Costing exposes financial data, so its read pages are limited to staff users or users explicitly granted the `costing.view_costing_financials` permission; ordinary authenticated users are refused. Write actions remain staff-only. The permission is never auto-assigned.

### Required tests

- Material cost reflects actual issues (not planned BOM) and excludes reversed issues; a missing price marks the period incomplete and blocks finalization.
- Labor, electricity, and water appear separately in product cost; a producing month missing any required record is flagged incomplete, while a recorded `0.00` is treated as a confirmed zero and not as missing. Missing labor is distinguishable from explicit zero labor.
- Each month uses its own recorded expenses with no carry-forward; factory/line/product scopes allocate to the correct products; totals reconcile without duplicate allocations for every policy; recalculation does not duplicate costs.
- A fallback line/product allocation or a product with no usable good-unit cost is marked incomplete and blocks finalization rather than silently finalizing.
- Waste policies produce the expected split; zero-good-unit and zero-base periods do not crash; finalized periods reject recalculation and require a reason to reopen.
- The cross-month comparison shows consistent per-product values and month-over-month changes for the selected product and periods.
- Existing catalog, inventory, and manufacturing workflows remain unaffected.

### Status

Implemented and verified on both the isolated SQLite test database and the approved PostgreSQL test database `test_paper_factory` (85 tests, zero skips). `paper_factory` must never be used for development or tests.

## Later prompts — shared support modules

### Prompt 3 — Shared accounting foundation (distinct from the production-costing step above)

Create one chart of accounts, periods, journal, and ledger system for the whole factory. Support factory-wide consolidated accounting and an optional production-line dimension on attributable costs/entries, including explicitly unallocated amounts. Define posting adapters for shared purchasing, sales, inventory, and manufacturing events; do not build one ledger per line. Add tests that line-level entries reconcile to consolidated totals, cross-line postings are not duplicated, reversals preserve audit history, and missing line attribution remains visible as unallocated. Obtain accountant approval for currency, tax, periods, accounts, posting rules, and cost integration before implementation.

### Prompt 4 — Shared purchasing and suppliers

Implement supplier, purchase order, receipt, and invoice workflows against the shared catalog, warehouses, inventory ledger, and accounting posting interface. Include tests for receipts into shared stock, UOM conversion, partial receipt, duplicate posting protection, rollback, and reports/postings attributable to a production line only where the source explicitly provides one. Do not divide suppliers or stock by production line.

Status: suppliers, draft/confirmed purchase orders, partial receipts through the shared ledger, duplicate/over-receipt protection, atomic rollback, unit-conversion validation, and read-only purchase-price traceability are implemented in the `purchasing` app. Supplier invoices, payments, tax, and accounting postings are deferred to the shared-accounting phase because they need accountant decisions and do not affect the shared stock ledger.

### Prompt 5 — Shared sales and customers

Implement customers, orders, deliveries, invoices, returns, and receipts against the shared catalog, common stock ledger, and accounting interface. Include tests for both product families, shared availability, delivery/return movements, line attribution where a finished product has an approved line association, consolidated sales totals, and no duplicate accounting/inventory effects.

Status: customer records, draft/confirmed/completed sales orders, partial shipments through the shared inventory ledger, duplicate/over-shipment protection, atomic rollback, unit-conversion validation, mixed-unit shipment valuation, terminal reversals with an explicit audited reopen path for completed orders, and price traceability are implemented in the `sales` app. Confirming an order does not issue stock; shipments post ISSUE movements through `inventory.services.post_movement`. Customer invoices, returns/credit notes, payments, tax, and accounting postings are deferred to the shared-accounting phase because they need accountant decisions. Sales prices never change `RawMaterialPrice`, production cost, or the costing valuation method.

### Prompt 6 — Secure mobile API foundation and owner dashboard data

Implement a versioned REST API foundation for the future Flutter customer and owner applications without creating a second inventory, sales, purchasing, or costing ledger. Use token authentication and explicit customer/owner permissions. Customer endpoints may read only the authenticated customer's own profile, explicitly customer-visible products, authorized prices, and own orders, and may submit draft order requests through the existing sales service. Owner endpoints may project current persisted sales, purchasing, inventory, production, customer/supplier, and costing data for the dashboard.

Status: implemented and hardened under `/api/v1/` with Django REST Framework and drf-spectacular. Customer catalog visibility is controlled by `Product.available_to_customers`; low-stock alerts use the explicit `Product.low_stock_threshold`; customer accounts are active non-staff users linked to an active customer. Customer order requests create draft sales orders with zero prices pending staff review and never confirm, reserve, ship, or deduct inventory. Confirmation and shipment are blocked until every line has a positive reviewed price, with `PENDING_REVIEW`/`RECORDED`/`UNPRICED` pricing status exposed separately from order status. The owner dashboard marks commercial document values as non-accounting, separates total from cancelled/active/reversed document counts, states production order-created versus output-recorded date bases, reads persisted monthly costing allocations/reconciliation without recalculating product costs, and returns `null` plus missing dependencies for profit, receivables, payables, and cash because accounting/invoicing/payments are not implemented. Responses are computed from current persisted data with no caching; no WebSockets, push notifications, or live-update infrastructure is implemented. Customer order-request idempotency keys and the Flutter applications remain future work. This API phase does not implement the consolidated reporting scope below.

### Prompt 7 — Consolidated and line-level reports

Implement read-only reports over shared data for factory-wide totals and separate paper-cup/fiber-container line filters. Cover production quantity, WIP, scrap, material consumption, costs, purchasing, sales, inventory, and accounting as available. Test that consolidated totals reconcile to attributed line totals plus unallocated amounts and that filtering never duplicates shared inventory/accounting entries. Do not invent KPI formulas or rates.

### Prompt 8 — Cross-cutting controls and deployment readiness

Implement the approved authorization matrix, audit review, backup/restore automation, operational monitoring, and deployment hardening only after policies and environment are confirmed. Test least privilege, audit coverage, backup restoration, and secure settings. Keep the shared architecture and avoid per-line duplicated controls or databases.
