# Implementation status

Updated: 2026-10-15

## Prompt 0 — project foundation

The foundation contains the Django project (`config`), `core` app, Arabic RTL starter page using a self-hosted Bootstrap RTL stylesheet, environment-selected database settings, pinned dependencies, migration/test documentation, and tests for the page shell and unsafe configuration failures.

No inventory, purchasing, manufacturing, sales, accounting, or reports workflows are implemented. No factory tax, costing, BOM, production, quality, or accounting policies have been assumed.

## Environment inspected

- Project `.venv`: Python 3.12.14; Django 5.2.18; Psycopg 3.3.6 and psycopg-binary 3.3.6. `pip check` reports no broken requirements.
- Host also has Python 3.14.7, while the legacy `py.exe` launcher defaults to Python 3.7.9 and does not reliably select 3.12. README now instructs selecting and verifying the exact interpreter path. Python 3.12.15 is a recommended patch, not an enforced dependency pin.
- PostgreSQL Windows service `postgresql-x64-18` is running and set to automatic start. PostgreSQL and `psql` report 18.6. Active configuration reports port 5432, `listen_addresses=*`, and host authentication via SCRAM-SHA-256. `pg_isready -h 127.0.0.1 -p 5432` reports accepting connections.
- No `.env`, PostgreSQL password file/service file, or relevant `DJANGO_*`, `DB_*`, `PG*`, or `DATABASE_URL` environment variables were present. Therefore no ERP database name, app role, or app password is configured/known. Django settings are strict: PostgreSQL requires all five `DB_*` values; it never falls back to SQLite on an unknown engine; SQLite is rejected when debug is off.
- No Django-owned migrations exist yet. Only built-in Django migrations are applied in the ignored workspace SQLite database. No migration was run against PostgreSQL.
- The PostgreSQL server listens on all interfaces. Windows firewall profiles are enabled; an enabled port-specific inbound allow rule for 5432 was not found by the inspection. Confirm firewall/network exposure before deployment.

## Verification performed

All Django commands below used `.venv\Scripts\python.exe`, `DJANGO_DEBUG=true`, an ephemeral test-only secret, and explicit `DB_ENGINE=sqlite`, unless otherwise stated.

- `python -m pip check` — passed; no broken requirements.
- `python manage.py check` — passed; no issues.
- `python manage.py check --database default` — passed against SQLite; this is not a PostgreSQL connection test.
- `python manage.py makemigrations --check --dry-run` — passed; no changes detected.
- `python manage.py showmigrations` — built-in admin/auth/contenttypes/sessions migrations shown applied in SQLite; no `core` migrations.
- `python manage.py migrate --plan` — no pending operations in SQLite.
- `python manage.py test` — passed: 8 tests, 0 failures, 0 skips, using Django's isolated SQLite test database.
- `python manage.py findstatic core/vendor/bootstrap-5.3.8/css/bootstrap.rtl.min.css` — found the checked-in local CSS asset.
- `python manage.py check --deploy` — run only with local development settings; exited 0 with 5 security warnings (TLS/HSTS, secure cookies, and DEBUG). A deployment-profile check is not complete because deployment TLS/hosts and the real PostgreSQL application configuration are not set.
- Django `check` with PostgreSQL backend and complete synthetic values previously validated backend loading only; no database connection was attempted. It is not PostgreSQL connectivity evidence.
- `pg_isready` — PostgreSQL accepts TCP connections on the verified host/port, but no username/password/database authentication was attempted because project credentials are absent.

## Remaining verification blockers

1. In pgAdmin, identify or create a dedicated non-superuser ERP database and application role. Set its password locally; do not send it in chat or commit it.
2. Set `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, and `DJANGO_DEBUG=false` in the local process environment. No `.env` auto-loader is installed.
3. Inspect the identified database and migration plan before applying migrations. Then run `check --database default`, migrations, a rollback-safe temporary record operation, and the test suite against a dedicated PostgreSQL test database/role.
4. Configure TLS/proxy and secure cookie/HSTS policy before deployment; HSTS duration must be selected deliberately.

See [PROMPT0_VERIFICATION.md](PROMPT0_VERIFICATION.md) for the original-requirement checklist and individual evidence/status.
