import os
import secrets
import subprocess
import sys

from django.test import TestCase
from django.urls import reverse


class HomePageTests(TestCase):
    def test_home_page_uses_arabic_rtl_shell(self):
        response = self.client.get(reverse("core:home"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'lang="ar" dir="rtl"')
        self.assertContains(response, "bootstrap.rtl.min.css")


class SettingsSafetyTests(TestCase):
    def run_settings_import(self, code="import config.settings", unset=(), **environment):
        child_environment = os.environ.copy()
        for name in unset:
            child_environment.pop(name, None)
        child_environment.update(environment)
        child_environment["DJANGO_SETTINGS_MODULE"] = "config.settings"
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env=child_environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_postgres_engine_requires_explicit_connection_settings(self):
        result = self.run_settings_import(
            unset=("DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT"),
            DJANGO_DEBUG="true",
            DB_ENGINE="postgres",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set all required PostgreSQL settings", result.stderr)

    def test_unknown_database_engine_does_not_fall_back_to_sqlite(self):
        result = self.run_settings_import(
            DJANGO_DEBUG="true",
            DB_ENGINE="postgress",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DB_ENGINE must be either", result.stderr)

    def test_postgres_rejects_invalid_port(self):
        result = self.run_settings_import(
            DJANGO_DEBUG="true",
            DB_ENGINE="postgres",
            DB_NAME="test_database",
            DB_USER="test_role",
            DB_PASSWORD=secrets.token_urlsafe(32),
            DB_HOST="127.0.0.1",
            DB_PORT="not-a-port",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DB_PORT must be an integer", result.stderr)

    def test_non_debug_requires_secret_key(self):
        result = self.run_settings_import(
            unset=("DJANGO_SECRET_KEY",),
            DJANGO_DEBUG="false",
            DJANGO_ALLOWED_HOSTS="localhost",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set DJANGO_SECRET_KEY", result.stderr)

    def test_non_debug_requires_allowed_hosts(self):
        result = self.run_settings_import(
            unset=("DJANGO_ALLOWED_HOSTS",),
            DJANGO_DEBUG="false",
            DJANGO_SECRET_KEY=secrets.token_urlsafe(50),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set DJANGO_ALLOWED_HOSTS", result.stderr)

    def test_non_debug_rejects_sqlite(self):
        result = self.run_settings_import(
            DJANGO_DEBUG="false",
            DJANGO_SECRET_KEY=secrets.token_urlsafe(50),
            DJANGO_ALLOWED_HOSTS="localhost",
            DB_ENGINE="sqlite",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SQLite is for local development and tests", result.stderr)

    def test_non_debug_enables_https_redirect_and_secure_cookies(self):
        result = self.run_settings_import(
            code=(
                "import config.settings as s; "
                "assert s.SECURE_SSL_REDIRECT and s.SESSION_COOKIE_SECURE and s.CSRF_COOKIE_SECURE"
            ),
            DJANGO_DEBUG="false",
            DJANGO_SECRET_KEY=secrets.token_urlsafe(50),
            DJANGO_ALLOWED_HOSTS="localhost",
            DB_ENGINE="postgres",
            DB_NAME="test_database",
            DB_USER="test_role",
            DB_PASSWORD=secrets.token_urlsafe(32),
            DB_HOST="127.0.0.1",
            DB_PORT="5432",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
