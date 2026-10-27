# Paper-products factory ERP foundation

Shared Django ERP for a paper-products factory with two production lines: paper cups and paper-fiber food containers/trays. Prompt 1 provides the shared catalog, warehouses, and immutable stock ledger. Prompt 2 adds configurable production lines, BOMs, routings, orders, material issues, operation progress, good/scrap results, and finished receipts through the same inventory ledger. A monthly product-costing step (a separate prompt from the later shared-accounting phase) adds monthly product costing: actual material issues priced with issue-time LPC snapshots or a labeled dated fallback, labor/electricity/water/other operating expenses allocated by a selectable policy, explicit monthly start/end attribution with no carry-forward, required labor/electricity/water records with a confirmed zero kept distinct from an unrecorded one, waste handling, per-product and per-order breakdowns, a per-product month-over-month comparison, and a draft/finalize/reopen lifecycle. The only confirmed fiber-container process fact is pressing fiber material in a mold; other stages and factory-specific inputs remain unconfirmed and are not configured as recipes or product routes. A shared purchasing step adds supplier records, draft/confirmed purchase orders, and partial receipts that post into the same inventory ledger, with purchase-price snapshots that update LPC only after a valid receipt is successfully posted. A shared sales step adds customer records, draft/confirmed/completed sales orders, and partial shipments that issue finished goods through the same inventory ledger, with sales-price snapshots kept separate from costing valuation. A versioned mobile API foundation under `/api/v1/` adds token authentication, strict customer data isolation, draft customer order requests, and an owner dashboard projection over existing operational and persisted costing data. Customer-submitted orders with zero prices cannot be confirmed or shipped until staff review every line; dashboard document counts and production date bases are explicit, and persisted costing allocation/reconciliation fields are exposed without recalculating product costs. A monthly management-profit step adds operating-expense recording, COGS for shipped quantities from persisted direct-material costs, finalization controls, cross-month and year-boundary loss carry-forward, a staff profit report, and a read-only owner API projection. Its figures are management operating results, not final accounting net profit.

The ERP uses one shared catalog, inventory ledger, warehouse/location system, and future purchasing, supplier, customer, sales, and accounting capabilities across both lines. BOMs and routings are configurable by product and line; inventory and accounting systems are not duplicated per line. WIP inventory transfers remain out of scope until factory policy is confirmed. Monthly product costing uses per-material Last Purchase Cost (LPC), effective at the successful system posting time of a genuine purchase receipt. Material issues snapshot their price and source at issue posting time; a dated owner-entered price is a clearly labeled fallback when no LPC applies. FIFO and Moving Weighted Average are not used. Measured material-waste records link damaged base quantities to production-result events and snapshot LPC or a valid dated fallback at server posting time; unreviewed evidence and unvalued waste keep costing periods incomplete. Shared purchasing and supplier management is implemented: suppliers, purchase orders, and receipts through the shared ledger, where prices update per-material LPC at successful posting time after a genuine receipt is validated. Shared sales and customer management is implemented: customers, sales orders, and shipments through the shared ledger, with mixed-unit shipment valuation, an explicit audited reopen path when a reversal leaves quantity outstanding, and sales prices recorded for traceability only without creating accounting entries or changing costing valuation. Supplier invoices, customer invoices, payments, and accounting postings remain part of the later accounting phase. See [PROMPT_SPECS.md](PROMPT_SPECS.md) for phase boundaries and acceptance tests.

## Runtime baseline

- Python 3.12 (3.12.15 was the recommended patch at foundation selection; exact micro version is not enforced in code).
- Django 5.2.18 (LTS; pinned in `requirements.txt`).
- PostgreSQL 18.6 (supported major/minor at selection time; intended deployment database).
- Psycopg 3.3.6 (pinned PostgreSQL adapter).
- Django REST Framework 3.18.3 and drf-spectacular 0.30.0 for the versioned mobile API and OpenAPI schema.
- Bootstrap RTL 5.3.8 is self-hosted under `core/static/` so the initial page does not depend on a CDN. Its license is included alongside the asset.

Compatibility references: [Django 5.2 installation FAQ](https://docs.djangoproject.com/en/5.2/faq/install/), [Django 5.2 release notes](https://docs.djangoproject.com/en/5.2/releases/5.2/), [PostgreSQL version policy](https://www.postgresql.org/support/versioning/), [Bootstrap RTL guide](https://getbootstrap.com/docs/5.3/getting-started/rtl/).

The `.venv` used for verification runs Python 3.12.14; the host also has Python 3.14.7 and the PostgreSQL 18.6 server/client. Django 5.2 supports Python 3.12 and 3.14; Django officially recommends testing against the latest micro release in a series. The tested 3.12.14 runtime is one patch behind the original 3.12.15 recommendation. That patch was a recommendation, not an enforced project pin; `requirements.txt` does not lock Python. Use the latest supported micro release for deployment. The legacy Windows `py.exe` launcher on this host does not select Python 3.12 reliably, so select the exact interpreter path and verify its version before creating a new venv.

## Local setup

Use a supported Python 3.12 release and Git. PostgreSQL 18 is required when running with `DB_ENGINE=postgres`; SQLite is provided for local development and isolated tests only. `DB_ENGINE=postgres` requires all of `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_PORT`; settings do not guess a database, role, password, host, or port. Non-debug startup requires an explicit secret key and allowed-host list, and rejects SQLite.

```powershell
# Find installed interpreter paths, then select a Python 3.12 executable.
py -0p
$pythonExe = 'C:\Path\To\Python312\python.exe'
& $pythonExe --version  # Confirm this prints Python 3.12.x.
& $pythonExe -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
$env:DJANGO_DEBUG = "true"
$env:DJANGO_SECRET_KEY = (& python -c "import secrets; print(secrets.token_urlsafe(50))")
python manage.py check
python manage.py migrate
python manage.py createsuperuser
python manage.py test
python manage.py runserver
```

`.env.example` documents environment variables but Django does not load `.env` files by itself. The setup commands above set a fresh development key in the current PowerShell session and select debug mode explicitly. `.gitignore` excludes `.env` and `.env.*` (while allowing `.env.example`); a local `.env` now holds this checkout's PostgreSQL values, but it is ignored by Git and is not loaded automatically. The verified local login role is `paper_factory_app` (underscores); the shared test database is `test_paper_factory`, and the production database `paper_factory` must never be targeted by development or test commands.

Use separate settings for each environment:

- **Development:** `DJANGO_DEBUG=true`; SQLite is suitable for quick local smoke tests. PostgreSQL integration and test work must use `DB_ENGINE=postgres`, `DB_NAME=test_paper_factory`, `DB_TEST_NAME=test_paper_factory`, and the `paper_factory_app` role at `127.0.0.1:5432`. Verify Django's effective configuration and live target before any database operation. Never use the production `paper_factory` database for development or tests. Do not put a real password in `.env.example` or Git.
- **Automated PostgreSQL tests:** use a dedicated disposable test database and test credentials. Django's test runner creates its test database; if it must create one, grant `CREATEDB` only to a dedicated test role, not the normal runtime role. Never point tests at production.
- **Production:** `DJANGO_DEBUG=false`, persistent `DJANGO_SECRET_KEY`, production `DJANGO_ALLOWED_HOSTS`, and production `DB_*` values supplied by the deployment secret store/process environment. Django settings require every PostgreSQL connection value and reject SQLite in non-debug mode. Non-debug mode forces HTTPS redirects and secure session/CSRF cookies; HSTS remains unset until the HTTPS hostname and proxy topology are confirmed.

The current settings consume process environment variables. To use an actual local `.env`, add and configure a dotenv loader deliberately; merely creating the file will not load it. `.env.example` shows placeholders for the requested local PostgreSQL names and port, not evidence that the database or role currently exist. Verify the intended target and migration plan before applying migrations.

The checked-in dependency manifest pins Django, Psycopg, Django REST Framework, drf-spectacular, and their resolved runtime dependencies. If the Python minor version changes, recreate `.venv` and rerun checks and tests. For deployments, run `python manage.py check --deploy` only after configuring the intended TLS and host settings; the development environment is not a production deployment profile.

## Mobile API

The API is served under `/api/v1/` and uses DRF token authentication only. It is
not a direct database interface: mobile clients must never connect to
PostgreSQL. Customers, owners, and operational staff have separate API
boundaries. Owners are active superusers or explicit members of the `ERP Owners`
Django group; operational users must be active staff members of `ERP Operational
Staff`. `is_staff=True` alone grants neither role. Customer records are isolated
by the authenticated user's linked customer account. Before rollout, explicitly
assign existing legitimate owners to `ERP Owners` (or confirm they are
superusers); no existing user is promoted automatically and no user-table
migration is needed.

```text
POST /api/v1/auth/token/       obtain a token
GET  /api/v1/auth/me/          server-derived effective role and permissions
POST /api/v1/auth/logout/      revoke the current token
GET  /api/v1/customer/...      customer profile, products, prices, orders
POST /api/v1/customer/order-requests/
GET  /api/v1/owner/dashboard/  owner dashboard snapshot
GET  /api/v1/owner/sales/      sales section
GET  /api/v1/owner/purchases/  purchasing section
GET  /api/v1/owner/inventory/  inventory and low-stock section
GET  /api/v1/owner/production/ production section
GET  /api/v1/owner/costing/    persisted monthly costing section
GET  /api/v1/owner/profit/     monthly management result and loss carry-forward
GET  /api/v1/owner/sales/orders/       paginated sales-order detail
GET  /api/v1/owner/purchases/orders/   paginated purchase-order detail
GET  /api/v1/owner/inventory/stock/    paginated current stock
GET  /api/v1/owner/inventory/movements/ paginated stock movements
GET  /api/v1/owner/production/orders/  paginated production orders
GET  /api/v1/owner/costing/periods/    paginated costing periods
GET  /api/v1/employee/production/orders/  active operational orders
GET  /api/v1/employee/production/orders/{id}/  operations and material requirements
POST /api/v1/employee/production/progress/  record operation progress
POST /api/v1/employee/production/operations/complete/  complete or skip an eligible optional operation
POST /api/v1/employee/production/results/  record good/scrap output
POST /api/v1/employee/production/results/{id}/reverse/  audited result correction
POST /api/v1/employee/production/results/{id}/waste/  record measured material waste
POST /api/v1/employee/production/results/{id}/waste-review/  confirm reviewed zero waste
POST /api/v1/employee/production/waste/{id}/reverse/  audited waste correction
GET  /api/v1/employee/materials/  material stock and active locations
POST /api/v1/employee/material-issues/  issue required material to an order
POST /api/v1/employee/counters/  start counter interval
POST /api/v1/employee/counters/{id}/{close|approve|correct}/  counter lifecycle
POST /api/v1/employee/finished-receipts/  receive production into shared stock
GET  /api/v1/schema/           staff-only OpenAPI schema
GET  /api/v1/docs/             staff-only interactive documentation
```

Customer order requests create draft sales orders only. They never confirm the
order, reserve stock, create a shipment, or deduct inventory. Customer catalog
visibility and low-stock thresholds are explicit product settings; staff must
mark products for the customer catalog. Owner dashboard and detail values are read
from current persisted records with no caching. Accounting-derived net profit,
receivables, payables, and cash are intentionally unavailable because those
modules are not implemented. The separate monthly management result and loss
carry-forward are persisted and clearly labelled as non-accounting; see
[API.md](API.md) for endpoint details, permissions, response semantics, and
limitations.

Employee operations are thin adapters over the manufacturing and counter
services; they do not create stock-ledger rows directly. Request bodies reject
undeclared actor, role, timestamp, and cost fields. Each write still requires
its existing Django model permission. Operational responses omit cost and
profit information. Assign the two groups and each action permission explicitly
in Django admin.

## Flutter Android client

A Flutter Android client is implemented in [`mobile/`](mobile/README.md). It
authenticates with the real API, discovers the effective role from
`/api/v1/auth/me/`, and routes to owner, operational employee, or customer
screens. Tokens are stored through `flutter_secure_storage` and never in
`shared_preferences`; language and API base URL remain non-secret preferences.
The default development URL is `http://10.0.2.2:8000/api/v1/`, configurable in
the app and validated before saving. Cleartext HTTP is enabled only in the debug
Android manifest; release builds use the platform default HTTPS-only policy.
Android backups are disabled for the app.

Owner screens read persisted dashboard, costing, inventory, sales, purchasing,
production, and monthly-profit data. Employee screens use the employee API for
production orders, measured waste, issues, finished receipts, and counter
lifecycle actions. Customer screens use only the customer catalog, price, and
draft-order-request endpoints. Forms select real server-provided IDs; no unit,
product, order, requirement, counter, or location ID is hardcoded. The client
does not contain mock business data or offline success paths.

Flutter commands, roles, security notes, and the verified APK location are in
[`mobile/README.md`](mobile/README.md).

Staff-only web reports are served under `/reports/` for the consolidated overview,
sales, purchasing, inventory, production, costing, and monthly profit. They use the same
read-only reporting service as the owner API, keep quantities separated by base
unit, expose date/status bases, show incomplete-costing and data-quality warnings,
and allow production filtering by line. Sales, purchasing, and the shared
inventory ledger have no duplicated per-line data.

## Migration and test workflow

After changing models, create migrations with `python manage.py makemigrations`, inspect the generated migration, then apply with `python manage.py migrate`. Review migration SQL with `python manage.py sqlmigrate <app> <migration>` where useful. Run `python manage.py check`, `python manage.py makemigrations --check --dry-run`, and `python manage.py test`. SQLite tests are fast local smoke/unit tests; run the suite against a dedicated PostgreSQL test database for transaction and constraint coverage. The runtime app role should not have `CREATEDB`; have an administrator create the dedicated `test_<DB_NAME>` database with the app role as owner, verify it is empty, then run `python manage.py test --keepdb` so Django reuses and preserves that database. Never point tests at the application or production database. Production migrations must be backed up and applied as an explicit release step; do not edit an already-applied migration to change deployed schema.

## Planned app boundaries

`config` holds project settings and URL/ASGI/WSGI entry points. `core` holds the landing page and shared shell. `api` holds the versioned mobile API, serializers, permissions, dashboard projections, and API tests. `reports` is a read-only service and staff web-report app over the shared domain records. Implemented domain apps are `catalog`, `inventory`, `manufacturing`, `costing`, `purchasing`, `sales`, and `reports`. The planned app is `accounting`. Their scope and dependency rules are in [DECISIONS.md](DECISIONS.md); their phased prompt contracts and tests are in [PROMPT_SPECS.md](PROMPT_SPECS.md).

## Project records

- [DECISIONS.md](DECISIONS.md): architecture and open policy decisions.
- [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md): current phase and verified state.
- [TODO.md](TODO.md): ordered next work.
- [PROMPT_SPECS.md](PROMPT_SPECS.md): phase contracts, implemented scope notes, and acceptance tests.
- [API.md](API.md): mobile API endpoints, authentication, permissions, dashboard semantics, and limitations.

Production posting now separates operator-entered production time from server posting time for financial attribution. Staff can record immutable measured raw-material waste against a production result, with an LPC or valid dated-fallback snapshot at event time, and explicitly review a result as having no material damage. Monthly costing and profit finalization remain incomplete when required waste evidence or valuation is missing. Production results use audited full reversal and new corrected postings; material reversals retain event-price snapshots. PostgreSQL verification against `test_paper_factory` is complete; see `IMPLEMENTATION_STATUS.md` for the verified test counts and migration status.
