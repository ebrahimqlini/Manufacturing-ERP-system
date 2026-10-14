# Manufacturing ERP foundation

Minimal Django foundation for a paper cup manufacturer. This phase establishes the project shell, configuration, Arabic RTL landing page, environment-based PostgreSQL configuration, and a smoke test. Factory workflows are intentionally deferred.

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
python manage.py test
python manage.py runserver
```

`.env.example` documents environment variables but Django does not load `.env` files by itself. The setup commands above set a fresh development key in the current PowerShell session and select debug mode explicitly. For PostgreSQL, set `DB_ENGINE=postgres` and all five `DB_*` values shown in `.env.example`; set a persistent `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false`, and the correct `DJANGO_ALLOWED_HOSTS` in the deployment environment. Do not commit `.env` or real credentials. Settings fail fast if debug is disabled and required secret/host settings are missing, if the database engine is unknown, or if PostgreSQL connection values are incomplete. They also reject SQLite when debug is disabled. Non-debug mode forces HTTPS redirects and secure session/CSRF cookies; HSTS remains unset until the HTTPS hostname and proxy topology are confirmed.

The checked-in dependency manifest pins Django, Psycopg, and their resolved runtime dependencies. If the Python minor version changes, recreate `.venv` and rerun checks and tests. For deployments, run `python manage.py check --deploy` only after configuring the intended TLS and host settings; the development environment is not a production deployment profile.

## Migration and test workflow

After changing models, create migrations with `python manage.py makemigrations`, inspect the generated migration, then apply with `python manage.py migrate`. Review migration SQL with `python manage.py sqlmigrate <app> <migration>` where useful. Run `python manage.py check`, `python manage.py makemigrations --check --dry-run`, and `python manage.py test` before integrating a change. SQLite tests are fast local smoke/unit tests; run the suite against a dedicated PostgreSQL test database for integration coverage. Production migrations must be backed up and applied as an explicit release step; do not edit an already-applied migration to change deployed schema.

## Planned app boundaries

`config` holds project settings and URL/ASGI/WSGI entry points. `core` holds cross-cutting primitives only. Planned domain apps are `catalog`, `inventory`, `purchasing`, `manufacturing`, `sales`, `accounting`, and `reports`. Their scope and dependency rules are in [DECISIONS.md](DECISIONS.md). Apps will be introduced in the phase that implements their first approved behavior.

## Project records

- [DECISIONS.md](DECISIONS.md): architecture and open policy decisions.
- [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md): current phase and verified state.
- [TODO.md](TODO.md): ordered next work.
