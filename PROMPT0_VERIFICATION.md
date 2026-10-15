# Prompt 0 verification

Date: 2026-10-15
Scope: verify the original Prompt 0 foundation only. No Prompt 1 or business-module work was started.

Subsequent scope-planning update: the factory has paper-cup and paper-fiber container/tray lines. Their shared-module plan and future acceptance tests are documented in [PROMPT_SPECS.md](PROMPT_SPECS.md); the second line's actual process remains unconfirmed. This update does not change the historical Prompt 0 verification or implement business functionality.

## 1. Executive summary

The foundation passes both SQLite and PostgreSQL checks. PostgreSQL 18.6 is running; `paper_factory` and non-superuser role `paper_factory_app` were authenticated and inspected. Built-in migrations applied safely to the verified empty application database, a temporary PostgreSQL write/read rolled back, and all eight tests passed on a separate preserved PostgreSQL test database.

**Decision: READY TO CLOSE PROMPT 0 AND PROCEED TO PROMPT 1.** Replace the short local database password with a strong unique one before deployment or exposing PostgreSQL beyond local development.

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
| 5 | PASS | Authenticated connection to `paper_factory` as `paper_factory_app` succeeded. The role is non-superuser and lacks `CREATEDB`/`CREATEROLE`; credentials are not stored in project files. |
| 6 | PASS | Django `check --database default` connected to PostgreSQL successfully. |
| 7 | PASS | The target was verified empty; the reviewed plan contained only standard Django migrations, which applied successfully. |
| 8 | PASS | PostgreSQL `check --database default` passed with no issues. |
| 9 | PASS | A temporary PostgreSQL table accepted a row, read it back, and rolled back in the same transaction. |
| 10 | PASS | Full suite passed on dedicated PostgreSQL test DB with `--keepdb`: 8 run, 8 passed, 0 failed, 0 skipped. |

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
- `python manage.py test` — SQLite: 8 run, 8 passed, 0 failed, 0 skipped. `python manage.py test --keepdb` — PostgreSQL: 8 run, 8 passed, 0 failed, 0 skipped; dedicated PostgreSQL test database preserved.
- `python manage.py findstatic core/vendor/bootstrap-5.3.8/css/bootstrap.rtl.min.css` — local asset found.
- `python manage.py check --deploy` — command exit 0, but 5 deployment warnings under the local development settings; production configuration unavailable.
- PostgreSQL authentication, reviewed built-in migrations, rollback-safe write/read, and tests were completed. No business models or workflows were added.

## 7. Git and commit review

Commits inspected: `0481df6 Set up Django ERP foundation` and `70831c7 Harden settings and document validation`. Before these verification fixes, `main` was clean at `70831c7`, aligned with `origin/main`. No push or history rewrite was performed. The historical dummy key finding is in section 5. Follow-up commits `e30da87` and `b16e2f7` are local; no push or history rewrite. This final test/documentation fix is currently uncommitted. Local SQLite and `.venv` are ignored; `.env` is absent and ignored.

## 8. Fixes in this verification pass

- PostgreSQL settings no longer assume DB name/user/password/host/port; every value is required and port is validated.
- Unknown database engine no longer falls back to SQLite; SQLite is rejected when debug is off.
- Non-debug startup requires explicit host allowlist and secret key; HTTPS redirect and secure session/CSRF cookies default on.
- Bootstrap RTL 5.3.8 is served locally; vendor license is included; homepage test checks stylesheet presence.
- Dependency manifest pins resolved runtime dependency versions.
- Settings tests cover missing PostgreSQL values, bad engine/port, missing production secret/hosts, SQLite in non-debug mode, and non-debug HTTPS/cookie defaults.

## 9. Remaining deployment considerations

1. Replace the short local database password with a strong unique password before deployment or exposing PostgreSQL beyond local development.
2. PostgreSQL listens on all interfaces; confirm Windows firewall/network exposure before deployment.
3. Configure the deployment HTTPS proxy/host and decide HSTS duration before deployment. Non-debug mode enables HTTPS redirects and secure session/CSRF cookies; HSTS remains opt-in.
4. Python 3.12.14 was tested; test the latest supported patch before deployment. The originally documented 3.12.15 was a recommendation, not a pin.
5. Historical commit `0481df6` included a synthetic development key placeholder, removed in `70831c7`; history was intentionally not rewritten.

## 10. Exact manual actions

Database setup, migrations, temporary read/write check, and PostgreSQL tests are complete. No manual database action is needed to close Prompt 0. Before deployment, change the short local password to a strong unique value and configure the actual HTTPS proxy/host and HSTS policy. The project does not auto-load `.env`; set process environment variables or deliberately add a dotenv loader if file-based configuration is desired. No system installation or service changes were made.

## 11. Final decision
**READY TO CLOSE PROMPT 0 AND PROCEED TO PROMPT 1.** Actual PostgreSQL authentication, safe migrations, a rollback-safe write/read, and the complete eight-test suite against a dedicated PostgreSQL test database passed. No Prompt 1 features were started.

## PostgreSQL follow-up (2026-10-15)

This section supersedes the earlier PostgreSQL-unverified statements above.

- Service `postgresql-x64-18` is Running/Automatic. `pg_isready -h 127.0.0.1 -p 5432` reports accepting connections; PostgreSQL server version is 18.6.
- Authenticated `psql` connection to database `paper_factory` as role `paper_factory_app` succeeded. Credentials were supplied only through the process environment for these checks and were not printed in output or saved in files.
- Catalog inspection verified `paper_factory` is owned by `paper_factory_app`; the role is not a superuser and has neither `CREATEDB` nor `CREATEROLE`. It has `USAGE` and `CREATE` on `public`. Before migrations, the application database contained no user tables.
- Using `.venv\Scripts\python.exe` with the actual PostgreSQL connection values in a temporary process environment: `manage.py check --database default` passed; `makemigrations --check --dry-run` reported no model changes; `showmigrations` showed the built-in admin/auth/contenttypes/sessions migrations unapplied; `migrate --plan` showed only those standard Django migrations.
- After reviewing the empty target and non-destructive built-in migration plan, `manage.py migrate --noinput` applied all built-in migrations successfully. A following `check --database default` passed, all built-in migrations showed `[X]`, and a following `migrate --plan` reported no pending operations.
- A PostgreSQL read/write smoke test created a temporary table, inserted/read a row, and rolled back its transaction. It passed and left no persistent probe table.
- The first PostgreSQL `manage.py test` attempt was blocked because the default `test_paper_factory` database did not yet exist and the runtime role correctly has `CREATEDB=false`. No tests ran on that attempt.
- After an administrator created `test_paper_factory` with owner `paper_factory_app`, read-only inspection verified it was empty and owned by that role. `manage.py test --keepdb` then passed on PostgreSQL: 8 run, 8 passed, 0 failed, 0 skipped. `--keepdb` preserved this dedicated test database.
- The first PostgreSQL test run also caught a test assumption: the home-page request used HTTP while non-debug settings enforce HTTPS. The test was corrected to request HTTPS; the full rerun passed.
- The eight-test suite also passes on SQLite (8 run, 8 passed, 0 failed, 0 skipped). SQLite and PostgreSQL results are reported separately.
- `.env` is absent and remains ignored/untracked. PostgreSQL values were provided only to individual verification processes; the project does not auto-load `.env`.

### Final decision

**READY TO CLOSE PROMPT 0 AND PROCEED TO PROMPT 1.** The actual PostgreSQL application database authenticated successfully, was verified empty before safe built-in migrations, passed a rollback-safe read/write operation, and passed the complete eight-test suite on a separate preserved PostgreSQL test database. SQLite checks also pass. No Prompt 1 features were started. Replace the short password used for local verification with a strong unique value before deployment or exposing the service beyond local development. No password value is recorded in this report.
