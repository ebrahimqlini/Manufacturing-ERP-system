# Architecture decisions

## Recorded baseline

- Use Django 5.2 LTS with Python 3.12 and PostgreSQL 18.6. Python 3.12.15 was the recommended patch at foundation selection, but the exact micro version is not enforced; the project `.venv` was verified with Python 3.12.14. Prefer the latest supported micro release when setting up or deploying. Django's compatibility documentation lists Python 3.12 for Django 5.2 and marks 5.2 as LTS. PostgreSQL lists major 18 as supported through November 2030. The manifest pins Django 5.2.18, Psycopg 3.3.6, and resolved runtime dependencies; Python and PostgreSQL server versions are runtimes, not Python packages.
- Use Django templates for the initial UI. The starter page declares Arabic and RTL; Django localization is enabled and both Arabic and English are configured.
- Use PostgreSQL for deployment. SQLite is only a low-friction local/test default; PostgreSQL-specific behavior must be checked against PostgreSQL before release.
- Read database settings from environment variables. Keep local `.env` ignored and commit only a placeholder example.
- Serve Bootstrap RTL 5.3.8 from the repository's static files, with its license, to avoid a runtime CDN dependency. Keep Arabic content RTL and isolate mixed-direction identifiers explicitly (for example with `<bdi dir="ltr">` for Latin product codes).
- Use `Decimal`/`DecimalField` for money and exact inventory quantities. Never introduce floats into financial or stock calculations. Prompt 1 stores quantities/conversion factors at 12 fractional places and rejects overflow without rounding; future business rounding policies still require owner approval.
- Introduce domain apps incrementally. Do not model presumed factory policies in the foundation.

## Expanded factory scope

The factory has two production lines sharing the same ERP support modules:

1. **Paper cups:** the known process may include printing, cutting, cup forming, bottom assembly where applicable, and packaging. Product/routing configuration must determine which operations apply.
2. **Paper-fiber food containers/trays:** the confirmed process fact is pressing fiber material in a mold. Do not infer drying, pulp production, heating, trimming, other stages, raw-material specifications, machinery, units, or production rates. Represent it as configurable product-specific BOMs and routings; the confirmed operation candidate is seeded, but no product recipe or complete route is assumed.

Both lines use one shared product catalog, warehouse/location structure, stock ledger, purchasing and supplier records, customer/sales flows, chart of accounts, journals, and reporting layer. Do not create parallel inventory, purchasing, sales, or accounting subsystems per line. Production orders and attributable costs carry a production-line dimension; reports support both consolidated factory totals and line-level views. Shared inputs remain shared inventory and are attributed to a line through the related transaction, not through duplicate stock balances.

Prompt 1 established configurable products/categories and units of measure plus shared warehouses, locations, and stock movements. Initial category coverage includes raw materials, work in progress, finished goods, packaging materials, scrap, and consumables. Prompt 2 adds configurable, versioned BOM/routing and production-order records for both lines without hardcoding unconfirmed factory details. Prompt 2 does not create WIP inventory balances. A later monthly product-costing step (see "Production costing decisions") calculates per-product costs from actual production and expense data while still avoiding any WIP balance or accounting posting. Later manufacturing/accounting work must attribute approved production costs by line without creating duplicate ledgers. Detailed boundaries and acceptance tests are in [PROMPT_SPECS.md](PROMPT_SPECS.md).

## Prompt 1 implementation decisions

- The shared catalog uses a globally unique SKU. Categories are configurable and seeded with shared stock categories; category membership is not production-line identity.
- Unit dimensions are configurable keys. Alternate conversions belong to a product and express base units per alternate unit. Quantity and conversion factors are stored as `Decimal` with 12 fractional places. Conversions that need more precision are rejected; no rounding is applied.
- The inventory ledger stores immutable movement headers plus signed per-location entries. Receipts, issues, adjustments, and openings create one leg; transfers create both legs in one database transaction. Corrections create linked reversal movements. A unique reference is required for each posted movement.
- Warehouse/location balances are computed by aggregating ledger entries; there is no editable quantity on the product. Auxiliary per-item/location lock rows serialize concurrent postings on PostgreSQL and do not store balances.
- Opening balance is staff-only, allowed only before that item has any ledger activity in the selected location, and cannot be repeated there. Adjustment and reversal reasons are mandatory.
- Confirmed factory policy: physical inventory must never become negative. The inventory posting service validates ledger balances inside its transaction after locking each product/location. Production orders may be released with a material shortage; manufacturing tracks planned, issued, and remaining quantities and permits valid partial issues over time.
- PostgreSQL triggers reject UPDATE/DELETE of posted movement and ledger rows, and guarded Django querysets reject normal ORM update/delete/bulk_update calls. These protections do not defend against a database owner disabling triggers, TRUNCATE, or unauthorized direct INSERTs; production database privileges and every writer still require control.
- Partial future production material issue is explicitly allowed: planned requirement, issued-to-date, and outstanding quantity are separate facts. If 120 kg is required and 100 kg is available, a valid 100 kg issue may be posted and 20 kg remains outstanding for a later issue; shortage itself never posts stock.
- Movements expose an optional generic `(source_reference_type, source_reference_id)` pair so future apps can identify their source without a foreign key or import from a not-yet-implemented domain app. When supplied, the pair is unique and represents one source posting event/line, protecting retries from duplicate ledger effects.
- Lot/serial tracking and inventory valuation are not implemented. No production-line identifier is added to shared stock; later manufacturing events can reference this shared service through explicit source references.

## Production costing decisions

- No inventory valuation method is approved and the shared stock ledger carries no unit cost, so monthly costing does not use FIFO or weighted average. Instead it prices actual material issues with owner-entered `RawMaterialPrice` rows and labels the result a provisional standard until an accountant approves a valuation method. The screen shows a "provisional until approved" banner, and a missing or future-dated price is reported instead of being silently reused.
- Material consumption is counted strictly from actual stock movements dated within the calendar month, excluding reversed issues. It is never taken from BOM plan quantities, and each movement is counted once.
- Electricity and water are treated as required utilities for any month with production. A missing utility bill (no record for that month) marks the period incomplete with an explicit note and blocks finalization; it is never assumed to be zero. A deliberately recorded `0.00` is a confirmed zero-cost bill and is stored and shown as such, so it is distinguishable from an unrecorded bill.
- Each period carries explicit `start_date`/`end_date` calendar boundaries, set from its year/month on every calculation (existing rows backfilled by migration `0005`). Material issues and production results are attributed by those boundaries, so one movement is never counted in two periods and recalculating one month leaves other months untouched.
- Utility bills are attributed wholly to the operator-selected month. The model records explicitly that a bill's period may not match the consumption month it covers; because the existing data model has no bill-period fields, bill-vs-consumption timing is documented as a known attribution limitation rather than silently re-based on a new accounting rule.
- Operating expenses (labor / electricity / water / other industrial) are recorded per month with a scope of factory, production line, or product. Line- and product-scoped amounts are attributed directly; factory-wide amounts are allocated by the period's selected policy (units produced, material cost, material weight, or equal-per-line then units). Allocation always reconciles to the recorded bucket total to the cent, so no expense is duplicated or lost across products or periods.
- The waste policy affects the computed numbers, not just a stored flag. `ABSORBED_NORMAL` loads the full material cost on good units; `SEPARATE_ABNORMAL` splits material cost between good output and recorded scrap in proportion to recorded quantities, displayed separately and never added on top of the material total.
- Costing output is financial data and is not exposed to every authenticated user. Every costing page that shows money (periods, breakdowns, comparison, expenses, raw-material prices, reference selling prices) is limited to staff users or to non-staff users explicitly granted the standard Django permission `costing.view_costing_financials`. That permission is never assigned automatically; a finance user receives it deliberately (for example through a group). Write actions stay staff-only, and the project adds no new role/group subsystem.
- Costing is computed bottom-up at the production-order level so order entries sum exactly to product entries and period totals. Periods are draft until a staff user finalizes them; finalization is blocked while required data (prices, utilities, allocation bases) is missing, and reopening a finalized period requires a recorded reason. Costing does not post accounting entries, selling prices are reference-only and never mixed into production cost, and no WIP balances are created.

## App responsibilities and dependency rules

| App | Responsibility | May depend on |
|---|---|---|
| `core` | Shared identity, audit primitives, common utilities | Django framework only; no domain app imports |
| `catalog` | Products, categories, units, conversion definitions (implemented) | Django framework |
| `inventory` | Shared warehouses/locations and a single immutable stock ledger for all lines (implemented) | `catalog`, Django framework |
| `manufacturing` | Configurable production lines, BOM revisions/routings, order snapshots, partial material issues, progress, good/scrap records, finished receipts through shared inventory (implemented) | `catalog`, shared `inventory`, `core`; future approved costing integration |
| `costing` | Monthly product costing: actual material issues priced with owner-entered provisional prices, scoped operating-expense allocation, waste handling, per-product/per-order breakdowns, finalize/reopen (implemented) | `catalog`, `manufacturing`, shared `inventory` |
| `purchasing` | Shared supplier-facing orders, receipts and invoices that affect shared stock | `catalog`, `inventory`, posting service interface |
| `sales` | Shared customer orders, deliveries, invoices, returns and receipts | `catalog`, `inventory`, posting service interface |
| `accounting` | One chart of accounts, periods, journals, ledger and statements; supports consolidated totals and a production-line dimension on attributable entries | `core`; posting adapters accept explicit source-document data |
| `reports` | Read-only operational and financial projections, factory-wide and filterable by production line | Domain query/service interfaces; no writes to domain records |

Apps marked implemented exist in this checkout. Avoid circular imports: domain apps call explicit service interfaces, and `core` never imports domain models. Posting coordination belongs in transaction-scoped service functions, not views, templates, or signals.

## Service and transaction conventions

Business operations that change multiple records will live in explicit service functions. Each service will validate the requested transition, use `transaction.atomic()`, enforce idempotency/uniqueness in the database where possible, and record actor/time/source for auditability. Views handle request/response concerns only. Draft documents remain editable according to policy; posted inventory and accounting records are immutable and corrected through explicit reversal/correction documents. Specific lifecycle rules remain undecided until domain phases.

## Naming and data conventions

Use singular PascalCase model names, lowercase `snake_case` fields, explicit foreign-key deletion behavior, and named database constraints for cross-row invariants where PostgreSQL can enforce them. Keep amounts and exact quantities in `Decimal`/`DecimalField`, with field precision chosen when business ranges are known. Render Latin product identifiers in mixed-direction Arabic text in an explicit left-to-right `<bdi>` element; do not infer a product-code format or normalize identifiers without a catalog policy.

## Numbering, valuation, and backups

- Document numbering must be unique under concurrent requests; generate numbers transactionally and enforce a database uniqueness constraint. Format, scope (global/per year/per branch), and gap policy are unresolved.
- No inventory valuation method or accounting integration policy is selected. FIFO, weighted average, standard cost, and treatment of overhead require factory/accountant decisions before implementation.
- Use reviewed, versioned Django migrations. Establish scheduled PostgreSQL backups, retention, encryption, off-machine copies, and restore drills before production; exact schedule and retention require owner approval.
- Run the application with a dedicated non-superuser database role. Keep migration/test privileges separate where practical; do not configure the normal application to use PostgreSQL's `postgres` superuser.

## Open factory/accounting decisions

No tax treatment, currency policy, fiscal calendar, chart of accounts, posting rules, rounding policy, stock costing method, BOM quantities/yield, production rates, scrap treatment, quality acceptance thresholds, or document-number formats are assumed. For fiber containers/trays, only pressing fiber material in a mold is confirmed; materials, machinery, units, other stages, and rates must be confirmed before configuring product data. Obtain and record the factory/accountant's decisions in the corresponding domain phase before implementing behavior. Confirm local tax/legal requirements with a qualified Egyptian accountant.
