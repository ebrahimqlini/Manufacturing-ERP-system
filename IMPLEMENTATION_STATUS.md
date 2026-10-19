# Implementation status

Updated: 2026-10-19

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

Implemented in this checkout (currently uncommitted):

- Added the `costing` app: monthly `CostingPeriod`, `ProductCostingEntry`, `ProductionOrderCostingEntry`, `MonthlyExpense` (labor / electricity / water / other industrial; scoped to factory, line, or product), owner-entered `RawMaterialPrice`, `RollYieldStandard`, and reference-only `ProductSellingPrice`.
- Material cost comes from actual material issues dated inside the calendar month (reversed issues excluded), priced with owner-entered prices labelled as a provisional standard. FIFO/weighted-average are deliberately not used: no valuation method is approved and the shared ledger carries no cost. A missing or future-dated price is reported explicitly and marks the period incomplete instead of silently reusing another price.
- Electricity and water are required utilities for any month with production: a missing utility bill (no record at all) marks the period incomplete with an explicit Arabic note and blocks finalization rather than being assumed zero. A recorded `0.00` is a confirmed zero-cost bill and is kept distinct from a missing one. Each bill is charged entirely to the operator-selected month, and the model documents that a utility bill's period may differ from its consumption month (no bill-period fields or accounting rule are invented).
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

## Remaining decisions and deployment work

- Confirm the factory's negative-stock policy, lot/serial requirements, and any additional authorization matrix before extending behavior.
- The local PostgreSQL password was supplied in chat and is short. Replace it with a strong unique value before deployment or exposing PostgreSQL beyond local development. The password is not recorded here or in project files.
- PostgreSQL listens on all interfaces. Confirm Windows firewall/network exposure before deployment.
- Configure the actual HTTPS proxy/host and explicitly decide HSTS before deployment. Non-debug mode already enables HTTPS redirects and secure session/CSRF cookies.
- Use a current supported Python 3.12 patch for deployment and rerun checks.

The revised phase order remains: Prompt 0 foundation (closed) → Prompt 1 shared catalog/inventory (implemented) → Prompt 2 configurable manufacturing for both lines → shared accounting → purchasing → sales → consolidated and line-level reporting → cross-cutting deployment controls. See [PROMPT_SPECS.md](PROMPT_SPECS.md).
