# Manufacturing ERP foundation

Minimal Django foundation for a paper cup manufacturer. This phase establishes the project shell, configuration, Arabic RTL landing page, environment-based PostgreSQL configuration, and a smoke test. Factory workflows are intentionally deferred.

## Runtime baseline

- Python 3.12.15 (compatible with Django 5.2; current patch release at selection time).
- Django 5.2.18 (LTS; pinned in `requirements.txt`).
- PostgreSQL 18.6 (supported major/minor at selection time; intended deployment database).
- Psycopg 3.3.6 (pinned PostgreSQL adapter).
- Bootstrap is not bundled in this foundation. Add and pin a local asset or document a controlled CDN strategy when the UI phase begins.

The current workspace has Python 3.12.14 available for checks. It supports Django 5.2 but is behind the selected 3.12.15 patch. Upgrade the local runtime before deployment. Django and PostgreSQL server/client binaries were not available when this foundation was created.

## Local setup

Use Python 3.12.15 and Git. PostgreSQL 18 is required when running with `DB_ENGINE=postgres`; SQLite is provided for isolated development and test runs only.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py check
python manage.py migrate
python manage.py test
python manage.py runserver
```

The sample `.env` selects SQLite. For PostgreSQL, set `DB_ENGINE=postgres` and provide the `DB_*` values shown in `.env.example`; supply `DJANGO_SECRET_KEY` and `DJANGO_ALLOWED_HOSTS` in the deployment environment. Do not commit `.env` or real credentials.

## Migration and test workflow

After changing models, create migrations with `python manage.py makemigrations`, inspect the generated migration, then apply with `python manage.py migrate`. Review migration SQL with `python manage.py sqlmigrate <app> <migration>` where useful. Run `python manage.py check` and `python manage.py test` before integrating a change. Production migrations must be backed up and applied as an explicit release step; do not edit an already-applied migration to change deployed schema.

## Planned app boundaries

`config` holds project settings and URL/ASGI/WSGI entry points. `core` holds cross-cutting primitives only. Planned domain apps are `catalog`, `inventory`, `purchasing`, `manufacturing`, `sales`, `accounting`, and `reports`. Their scope and dependency rules are in [DECISIONS.md](DECISIONS.md). Apps will be introduced in the phase that implements their first approved behavior.

## Project records

- [DECISIONS.md](DECISIONS.md): architecture and open policy decisions.
- [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md): current phase and verified state.
- [TODO.md](TODO.md): ordered next work.
