# Architecture decisions

## Recorded baseline

- Use Django 5.2 LTS with Python 3.12 and PostgreSQL 18.6. Python 3.12.15 was the recommended patch at foundation selection, but the exact micro version is not enforced; the project `.venv` was verified with Python 3.12.14. Prefer the latest supported micro release when setting up or deploying. Django's compatibility documentation lists Python 3.12 for Django 5.2 and marks 5.2 as LTS. PostgreSQL lists major 18 as supported through November 2030. The manifest pins Django 5.2.18, Psycopg 3.3.6, and resolved runtime dependencies; Python and PostgreSQL server versions are runtimes, not Python packages.
- Use Django templates for the initial UI. The starter page declares Arabic and RTL; Django localization is enabled and both Arabic and English are configured.
- Use PostgreSQL for deployment. SQLite is only a low-friction local/test default; PostgreSQL-specific behavior must be checked against PostgreSQL before release.
- Read database settings from environment variables. Keep local `.env` ignored and commit only a placeholder example.
- Serve Bootstrap RTL 5.3.8 from the repository's static files, with its license, to avoid a runtime CDN dependency. Keep Arabic content RTL and isolate mixed-direction identifiers explicitly (for example with `<bdi dir="ltr">` for Latin product codes).
- Use `Decimal`/`DecimalField` for money and exact inventory quantities. Never introduce floats into financial or stock calculations; define precision and rounding only after business owners approve the policies.
- Introduce domain apps incrementally. Do not model presumed factory policies in the foundation.

## App responsibilities and dependency rules

| App | Responsibility | May depend on |
|---|---|---|
| `core` | Shared identity, audit primitives, common utilities | Django framework only; no domain app imports |
| `catalog` | Products, materials, units, conversion definitions | `core` |
| `inventory` | Warehouses, locations, immutable stock ledger, valuation records | `catalog`, `core` |
| `purchasing` | Supplier-facing order, receipt, invoice documents | `catalog`, `inventory`, `accounting` service interfaces |
| `manufacturing` | BOM revisions, routings, production orders, operation and quality records | `catalog`, `inventory`, `accounting` service interfaces |
| `sales` | Customer orders, deliveries, invoices, returns, receipts | `catalog`, `inventory`, `accounting` service interfaces |
| `accounting` | Accounts, periods, journals, ledger and statements | `core`; posting adapters accept explicit source-document data |
| `reports` | Read-only operational and financial projections | Domain query/service interfaces; no writes to domain records |

The table is a target, not a claim that these apps already exist. Avoid circular imports: domain apps call explicit service interfaces, and `core` never imports domain models. Posting coordination belongs in transaction-scoped service functions, not views, templates, or signals.

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

No tax treatment, currency policy, fiscal calendar, chart of accounts, posting rules, rounding policy, stock costing method, BOM quantities/yield, production rates, scrap treatment, quality acceptance thresholds, or document-number formats are assumed. Obtain and record the factory/accountant's decisions in the corresponding domain phase before implementing behavior. Confirm local tax/legal requirements with a qualified Egyptian accountant.
