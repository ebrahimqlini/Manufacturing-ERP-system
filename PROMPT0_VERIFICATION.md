# Prompt 0 verification

Date: 2026-10-15
Scope: verify the original Prompt 0 foundation only. No Prompt 1 or business-module work was started.

## 1. Executive summary

The Django foundation passes SQLite checks and its eight-test suite. PostgreSQL 18.6 is installed, the Windows service is running, and the server accepts TCP connections on the verified local port. The ERP project has no database name, application role, or password configured, so PostgreSQL authentication, Django PostgreSQL migrations, a real database operation, and PostgreSQL tests were not attempted. The project now fails closed instead of silently using SQLite when PostgreSQL settings are incomplete.

**Decision: NOT READY FOR PROMPT 1 yet.** Complete the manual database/role configuration in section 10 of the final report, then verify Django against that isolated PostgreSQL database.

## 2. Original Prompt 0 checklist

Statuses apply to the requirement as written. `NOT APPLICABLE` means the original prompt explicitly deferred the capability beyond this foundation phase.

### Core stack and scope

| Status | Original requirement | Evidence |
|---|---|---|
| PASS | Python stable and compatible with Django | `.venv` is Python 3.12.14; Django 5.2.18 and the test suite run there. Python 3.12.15 was the recommended patch at selection, not an exact interpreter pin. Latest-micro support is discussed in the compatibility report. |
| PASS | Supported Django LTS | `requirements.txt` pins Django 5.2.18; Django's official 5.2 documentation identifies the LTS line. |
| PASS | PostgreSQL selected for deployment | PostgreSQL 18.6 is installed and running; actual ERP database authentication remains unverified. |
| PASS | Django templates and Bootstrap for initial UI | `templates/core/home.html` references the self-hosted Bootstrap RTL 5.3.8 CSS in `core/static/`; `findstatic` located it. |
| PASS | Arabic RTL starter interface | Home test asserts `lang="ar" dir="rtl"`; Bootstrap RTL stylesheet is served locally. |
| NOT APPLICABLE | Render actual mixed Arabic/English product codes | Catalog/code rendering is future work. `DECISIONS.md` requires explicit LTR isolation for Latin identifiers using `<bdi>`; there are no product records yet. |
| PASS | Decimal for money and exact quantities | `DECISIONS.md` records `Decimal`/`DecimalField` as a hard data convention; no business amounts or quantities exist in this phase. |
| PASS | Git version control | Git repository exists; current work is committed locally; no push performed. |
| PASS | Django tests or pytest-django | Django test suite passes: 8 tests, 0 failures, 0 skips, using the isolated SQLite test database. |
| PASS | Do not implement all modules in one step | Only project config, `core`, the RTL shell, tests, and docs exist. |
| PASS | Work only on the requested phase | No inventory, purchasing, manufacturing, sales, accounting, or reports behavior added. |

### Non-negotiable engineering rules (all 20)

| # | Status | Requirement and evidence |
|---:|---|---|
| 1 | PASS | Incremental implementation: foundation only; future apps remain unimplemented. |
| 2 | PASS | Current phase only: no Prompt 1/business features. |
| 3 | PASS | Repository was inspected before edits; current work is limited to foundation files/assets. |
| 4 | PASS | Existing file contents were reviewed before edits; no user data or existing work was overwritten/deleted. |
| 5 | PASS | No factory tax, rate, BOM, or accounting assumptions were encoded; open decisions are listed in `DECISIONS.md`. |
| 6 | PASS | Unknown rules are documented as unresolved and deferred to factory/accountant decisions. |
| 7 | PASS | Results below distinguish successful checks from checks blocked by missing credentials. |
| 8 | PASS | Tests use Django's disposable SQLite test database; no production database/data was used. |
| 9 | FAIL | Current code has no hard-coded key and no real credentials are committed. Historical commit `0481df6` did contain a synthetic development-only `SECRET_KEY` placeholder; it was removed in `70831c7`. History was not rewritten, as requested. |
| 10 | PASS | No money/stock calculations exist; `DECISIONS.md` forbids floats for future exact values. |
| 11 | NOT APPLICABLE | No business models/invariants exist yet for database constraints. |
| 12 | NOT APPLICABLE | No multi-record business operation exists yet; service transaction boundaries are documented. |
| 13 | NOT APPLICABLE | No posted inventory/accounting documents exist in this phase. |
| 14 | NOT APPLICABLE | No posting workflow or accounting effects exist in this phase. |
| 15 | PASS | Views only render the starter page; no critical workflow is implemented in views/templates/signals. |
| 16 | PASS | No broad exception handling or swallowed validation failure was found. |
| 17 | PASS | CSRF and authentication middleware remain enabled; Django password validators are present. |
| 18 | PASS | Debug defaults off, non-debug settings require a key and allowed hosts, and no sensitive settings are logged. Production TLS remains unconfigured. |
| 19 | PASS | Eight tests include RTL page behavior and failure cases for absent PostgreSQL settings, invalid engine/port, missing production key/hosts, and SQLite in non-debug mode. |
| 20 | PASS | Modular Django foundation; no microservices or premature domain apps. |

### Architecture and business-domain requirements

| Status | Requirement | Evidence |
|---|---|---|
| PASS | Document app responsibilities and dependency rules | `DECISIONS.md` gives the planned `core`, `catalog`, `inventory`, `purchasing`, `manufacturing`, `sales`, `accounting`, and `reports` boundaries; only `config` and `core` exist now. |
| PASS | Document service layer | `DECISIONS.md` requires explicit service functions for domain transitions and posting coordination. |
| PASS | Consistent naming/model conventions | `DECISIONS.md` records model/field naming and constraint conventions. |
| PASS | Explicit transaction boundaries | Future multi-record services use `transaction.atomic()`; no such domain services exist yet. |
| PASS | Auditability strategy | Service convention records actor/time/source for important operations; no audit workflow is implemented yet. |
| PASS | Document numbering strategy | Unique numbering is to be transactionally generated and constrained; scope/format/gap rules remain open. |
| PASS | Document stock valuation/accounting integration decisions | No costing/posting policy is invented; the unresolved choices and dependencies are recorded for later approval. |
| PASS | Migration and backup strategy | README and DECISIONS document reviewed versioned migrations, explicit production releases, backups/retention/restore drills. No backup automation exists yet. |
| PASS | Separate draft and posted documents | The design convention is documented; no documents exist yet. |
| PASS | Repeatable local tests/development setup | `README.md`, fully pinned Python packages, SQLite smoke tests, and migration commands are provided. PostgreSQL integration setup awaits credentials. |
| NOT APPLICABLE | Product/material/UOM catalog and conversions | Explicitly deferred to later phase. |
| NOT APPLICABLE | Warehouses, locations, ledger, valuation | Explicitly deferred; valuation method requires owner/accountant decision. |
| NOT APPLICABLE | Supplier orders/receipts/invoices | Explicitly deferred. |
| NOT APPLICABLE | Versioned BOMs, routings, work centers | Explicitly deferred; quantities/rates are not fabricated. |
| NOT APPLICABLE | Production orders, actual consumption/output/scrap/WIP, quality | Explicitly deferred. |
| NOT APPLICABLE | Customers, sales, deliveries, returns, receipts | Explicitly deferred. |
| NOT APPLICABLE | Chart of accounts, periods, journals, ledger/statements | Explicitly deferred; accounting rules remain open. |
| NOT APPLICABLE | Costing, profitability, dashboards | Explicitly deferred. |
| NOT APPLICABLE | User policy, audit workflows, recoverable backup automation | Future implementation/deployment work; Django's standard authentication/security middleware is retained. |

### Initial-task deliverables

| Status | Requirement | Evidence |
|---|---|---|
| PASS | Inspect repo/environment | Empty repo at initial setup; current Python, dependencies, Git, PostgreSQL service/config and listeners were inspected. |
| PASS | Create minimal skeleton for empty repo | `config/`, `core/`, templates, settings, URLs, ASGI/WSGI, and `manage.py`. |
| PASS | Check selected version compatibility | Django/Python and PostgreSQL versions documented and checked; the real `.venv` patch discrepancy is disclosed. |
| PASS | README, DECISIONS, status, TODO, gitignore, pinned dependencies, env example | All files are present and updated; `.env.example` has placeholders only. |
| PASS | Plan app structure and dependency rules | `DECISIONS.md`. |
| PASS | Document unresolved factory decisions | `DECISIONS.md`. |
| PASS | Small test and migration workflow | README instructions; 7 SQLite tests pass; PostgreSQL integration not yet possible. |
| PASS | Run possible checks | SQLite/Django checks pass; PostgreSQL service readiness was checked. Authenticated PostgreSQL checks are explicitly not claimed. |
| PASS | Stop at foundation | No Prompt 1 features were implemented. |

## 3. PostgreSQL verification

| # | Status | Result |
|---:|---|---|
| 1 | PASS | Windows service `postgresql-x64-18`: Running, Automatic start. |
| 2 | PASS | Installed server/client 18.6; active port 5432; `listen_addresses=*`; `pg_isready -h 127.0.0.1 -p 5432` says accepting connections. Host authentication config uses SCRAM-SHA-256. |
| 3 | PASS | `.venv` contains Psycopg 3.3.6 and `psycopg-binary` 3.3.6; import resolves inside `.venv`. `pip check` passes. |
| 4 | PASS | `config/settings.py` reads `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_PORT` from environment. It fails on missing values/unknown engine and will not silently choose SQLite for non-debug runs. |
| 5 | NOT TESTED | No ERP `DB_*` environment variables, project `.env`, pgpass file, or pg_service file exists. The ERP database name, app role and password are unknown; existence/authentication were not guessed. |
| 6 | NOT TESTED | No real Django-to-PostgreSQL login was attempted. `pg_isready` verifies server readiness only, not credentials. |
| 7 | NOT TESTED | No migrations were applied to PostgreSQL because the target database/role are not identified. Project has no custom migrations; Django built-ins are the only migration scope. |
| 8 | NOT TESTED | `check --database default` passed only with explicit SQLite test settings. PostgreSQL DB checks require app credentials. |
| 9 | NOT TESTED | No temporary record was written to PostgreSQL; no dedicated test DB is configured. |
| 10 | NOT TESTED | Django suite has not run against PostgreSQL. SQLite test result is not represented as PostgreSQL coverage. |

## 4. Python and dependencies

- Actual test interpreter: `G:\Back_end Python\Django_Projects\Manufacturing ERP system\.venv\Scripts\python.exe`, Python 3.12.14.
- `.venv` Django 5.2.18, Psycopg/psycopg-binary 3.3.6. `pip freeze` contains exact pins for Django and all resolved runtime dependencies; `pip check` reports none broken.
- Host also has Python 3.14.7. Legacy `C:\Windows\py.exe` is 3.7.9 and maps requested `py -3.12` unexpectedly to Python 3.14.7. README now asks developers to inspect `py -0p`, select an exact executable path, and verify the version before making a new venv.
- Python 3.12.15 in the original docs was a recommended patch, not a `requirements.txt` constraint. The verified Python 3.12.14 is compatible in observed runs but is one micro patch behind the recommendation; latest-micro deployment testing remains recommended.
- Django 5.2 compatibility and LTS are supported by official Django docs. PostgreSQL 18.6 is supported according to the official PostgreSQL version policy. Bootstrap RTL 5.3.8 is stored locally with its license; the downloaded CSS SHA-384 matched the official documented SRI hash.

## 5. Security and configuration

- Current source has no literal Django secret; non-debug startup requires `DJANGO_SECRET_KEY` and `DJANGO_ALLOWED_HOSTS`. Debug-only secret generation is ephemeral.
- No real passwords, tokens, DB credentials, `.env`, local database, or `.venv` are tracked. `.gitignore` excludes `.env`, `.env.*` (except `.env.example`), `db.sqlite3`, and virtual environments.
- Django does not load `.env` automatically; README tells users to set process environment variables. `.env.example` is a placeholder/example, not an active config.
- SQLite is local-debug/test only. PostgreSQL requires all connection values; bad engines/ports fail at startup.
- CSRF/auth/session middleware and password validators remain enabled.
- `check --deploy` was run with a development profile and returned 5 warnings for HSTS, SSL redirect, secure session/CSRF cookies, and DEBUG. Non-debug settings now enable HTTPS redirect and secure session/CSRF cookies by default; HSTS remains opt-in until hostname/proxy behavior is confirmed. This is not a production-profile check.
- Historical commit `0481df6` includes a synthetic hard-coded development key placeholder. It was removed in `70831c7`; no real credentials were found. History was not rewritten.
- Server listens on all interfaces. Windows firewall profiles are enabled; inspection did not find an enabled rule specifically allowing TCP 5432. Confirm network exposure before production.

## 6. Migrations and tests

Commands run in `.venv` with explicit local SQLite/debug environment:

- `python -m pip check` — passed.
- `python manage.py check` — passed, 0 issues.
- `python manage.py check --database default` — passed against SQLite.
- `python manage.py makemigrations --check --dry-run` — passed, no changes detected.
- `python manage.py showmigrations` — built-in admin/auth/contenttypes/sessions migrations applied; no `core` migration exists.
- `python manage.py migrate --plan` — no pending operations.
- `python manage.py migrate --noinput` — passed; no migrations to apply to the identified workspace SQLite database.
- `python manage.py test` — 8 run, 8 passed, 0 failed, 0 skipped; SQLite test DB created/destroyed by Django.
- `python manage.py findstatic core/vendor/bootstrap-5.3.8/css/bootstrap.rtl.min.css` — local asset found.
- `python manage.py check --deploy` — command exit 0, but 5 deployment warnings under the local development settings; production configuration unavailable.
- PostgreSQL checks/migrations/tests were not run due missing application database credentials. No target PostgreSQL database was altered.

## 7. Git and commit review

Commits inspected: `0481df6 Set up Django ERP foundation` and `70831c7 Harden settings and document validation`. Before these verification fixes, `main` was clean at `70831c7`, aligned with `origin/main`. No push or history rewrite was performed. The historical dummy key finding is in section 5. Follow-up commit `e30da87 Verify Prompt 0 foundation and PostgreSQL readiness` contains these verification changes. Branch `main` is one local commit ahead of `origin/main`; no push or history rewrite. Local SQLite, `.venv`, and `.env` remain ignored.

## 8. Fixes in this verification pass

- PostgreSQL settings no longer assume DB name/user/password/host/port; every value is required and port is validated.
- Unknown database engine no longer falls back to SQLite; SQLite is rejected when debug is off.
- Non-debug startup requires explicit host allowlist and secret key; HTTPS redirect and secure session/CSRF cookies default on.
- Bootstrap RTL 5.3.8 is served locally; vendor license is included; homepage test checks stylesheet presence.
- Dependency manifest pins resolved runtime dependency versions.
- Settings tests cover missing PostgreSQL values, bad engine/port, missing production secret/hosts, SQLite in non-debug mode, and non-debug HTTPS/cookie defaults.

## 9. Remaining blockers

1. PostgreSQL ERP database name and non-superuser application role/password are not configured or authenticated.
2. PostgreSQL-specific migration, `check --database`, temporary record, and Django test-suite validation cannot run until item 1 is resolved and a dedicated/test database is identified.
3. `check --deploy` remains warning-only until TLS/proxy, secure cookies, and HSTS policy are decided.
4. The tested Python patch is 3.12.14, not the recommended 3.12.15 micro release.
5. Commit `0481df6` retains a historical synthetic development key placeholder; history is intentionally not rewritten.

## 10. Exact manual actions

1. In pgAdmin, inspect existing databases and roles. Identify an existing ERP-specific database/role or create a dedicated database and least-privilege non-superuser application role. Do not use the `postgres` superuser for application traffic. Select/set its password locally; do not share it in chat or commit it. No system installation or service changes are needed.
2. In the PowerShell session used to run Django, set `DB_ENGINE=postgres`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST=127.0.0.1`, `DB_PORT=5432`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, and `DJANGO_DEBUG=false`. The project does not auto-load `.env`. Keep credentials in the local environment.
3. Before any migration, verify that `DB_NAME` is the intended dedicated database and review `.venv\Scripts\python.exe manage.py migrate --plan` with those exact settings. If it is not a disposable/identified ERP database, stop before migration.
4. Run `manage.py check --database default`, `showmigrations`, and `migrate --plan` against that database. Apply migrations only after confirming the target and plan. Then exercise a temporary record in a transaction and roll it back, and run tests with a dedicated PostgreSQL test database. Django's test runner may need test-database creation permission; provide it only to a dedicated test role if required, not casually to the normal runtime role.
5. After database checks, configure the actual HTTPS proxy/host and decide whether/when to enable HSTS. Non-debug settings already turn on HTTPS redirect and secure session/CSRF cookies; HSTS remains off until the deployment hostname/proxy is known.
6. Optionally update the project venv to Python 3.12.15 (or another current supported patch) only after scheduling that compatibility check. Python 3.12.14 is the interpreter that was actually tested; no system Python was changed.

## 11. Final decision

**NOT READY FOR PROMPT 1.** Prompt 0 code and SQLite verification pass, but actual PostgreSQL authentication, database identity, migrations, a real PostgreSQL operation, and PostgreSQL test-suite results remain unverified. The database configuration is intentionally strict, and no unknown database was modified. The next required step is to complete the local database/role configuration and repeat the PostgreSQL-specific checks above. No Prompt 1 feature work was started.
