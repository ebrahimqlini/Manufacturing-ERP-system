# Paper-products factory ERP foundation

Shared Django ERP for a paper-products factory with two production lines: paper cups and paper-fiber food containers/trays. Prompt 1 provides the shared catalog, warehouses, and immutable stock ledger. Prompt 2 adds configurable production lines, BOMs, routings, orders, material issues, operation progress, good/scrap results, and finished receipts through the same inventory ledger. A monthly product-costing step (a separate prompt from the later shared-accounting phase) adds monthly product costing: actual material issues priced with owner-entered provisional prices, labor/electricity/water/other operating expenses allocated by a selectable policy, explicit monthly start/end attribution with no carry-forward, a confirmed zero-cost bill kept distinct from an unrecorded one, waste handling, per-product and per-order breakdowns, a per-product month-over-month comparison, and a draft/finalize/reopen lifecycle. The only confirmed fiber-container process fact is pressing fiber material in a mold; other stages and factory-specific inputs remain unconfirmed and are not configured as recipes or product routes.

The ERP uses one shared catalog, inventory ledger, warehouse/location system, and future purchasing, supplier, customer, sales, and accounting capabilities across both lines. BOMs and routings are configurable by product and line; inventory and accounting systems are not duplicated per line. WIP inventory transfers remain out of scope until factory policy is confirmed. Monthly product costing is implemented, but it prices material consumption with owner-entered provisional standard prices and does not use FIFO or weighted average, because no valuation method is approved and the stock ledger carries no cost; costs are therefore provisional until an accountant approves a valuation method. See [PROMPT_SPECS.md](PROMPT_SPECS.md) for phase boundaries and acceptance tests.

## Runtime baseline

- Python 3.12 (3.12.15 was the recommended patch at foundation selection; exact micro version is not enforced in code).
- Django 5.2.18 (LTS; pinned in `requirements.txt`).
- PostgreSQL 18.6 (supported major/minor at selection time; intended deployment database).
- Psycopg 3.3.6 (pinned PostgreSQL adapter).
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

- **Development:** `DJANGO_DEBUG=true`; SQLite is suitable for quick local smoke tests. For PostgreSQL integration work, use `DB_ENGINE=postgres` with the local `paper_factory` database and `paper_factory_app` role after confirming/creating them in pgAdmin. Do not put a real password in `.env.example` or Git.
- **Automated PostgreSQL tests:** use a dedicated disposable test database and test credentials. Django's test runner creates its test database; if it must create one, grant `CREATEDB` only to a dedicated test role, not the normal runtime role. Never point tests at production.
- **Production:** `DJANGO_DEBUG=false`, persistent `DJANGO_SECRET_KEY`, production `DJANGO_ALLOWED_HOSTS`, and production `DB_*` values supplied by the deployment secret store/process environment. Django settings require every PostgreSQL connection value and reject SQLite in non-debug mode. Non-debug mode forces HTTPS redirects and secure session/CSRF cookies; HSTS remains unset until the HTTPS hostname and proxy topology are confirmed.

The current settings consume process environment variables. To use an actual local `.env`, add and configure a dotenv loader deliberately; merely creating the file will not load it. `.env.example` shows placeholders for the requested local PostgreSQL names and port, not evidence that the database or role currently exist. Verify the intended target and migration plan before applying migrations.

The checked-in dependency manifest pins Django, Psycopg, and their resolved runtime dependencies. If the Python minor version changes, recreate `.venv` and rerun checks and tests. For deployments, run `python manage.py check --deploy` only after configuring the intended TLS and host settings; the development environment is not a production deployment profile.

## Migration and test workflow

After changing models, create migrations with `python manage.py makemigrations`, inspect the generated migration, then apply with `python manage.py migrate`. Review migration SQL with `python manage.py sqlmigrate <app> <migration>` where useful. Run `python manage.py check`, `python manage.py makemigrations --check --dry-run`, and `python manage.py test`. SQLite tests are fast local smoke/unit tests; run the suite against a dedicated PostgreSQL test database for transaction and constraint coverage. The runtime app role should not have `CREATEDB`; have an administrator create the dedicated `test_<DB_NAME>` database with the app role as owner, verify it is empty, then run `python manage.py test --keepdb` so Django reuses and preserves that database. Never point tests at the application or production database. Production migrations must be backed up and applied as an explicit release step; do not edit an already-applied migration to change deployed schema.

## Planned app boundaries

`config` holds project settings and URL/ASGI/WSGI entry points. `core` holds the landing page and shared shell. Implemented domain apps are `catalog`, `inventory`, `manufacturing`, and `costing`. Planned apps are `accounting`, `purchasing`, `sales`, and `reports`. Their scope and dependency rules are in [DECISIONS.md](DECISIONS.md); their phased prompt contracts and tests are in [PROMPT_SPECS.md](PROMPT_SPECS.md).

## Project records

- [DECISIONS.md](DECISIONS.md): architecture and open policy decisions.
- [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md): current phase and verified state.
- [TODO.md](TODO.md): ordered next work.
- [PROMPT_SPECS.md](PROMPT_SPECS.md): implemented Prompt 1 contract and later implementation scope and acceptance tests.
