# Manufacturing ERP — Flutter Android client

Android Flutter client for the shared manufacturing ERP API. The app is a thin
client over the existing Django REST API under `/api/v1/`; it has no local
database, no mock business responses, and no direct PostgreSQL access.

## Requirements

- Flutter 3.47.8 / Dart 3.13.7 (the verified toolchain).
- Android SDK with the NDK version required by Flutter (`28.2.13676358` at the
  time of verification) and CMake `3.22.1`.
- A reachable Django backend with the mobile API enabled and at least one
  correctly assigned user.

The Android app uses `minSdkVersion 24`, `targetSdkVersion 36`, and disables
Android application backups.

## Configure the backend URL

The default emulator URL is:

```text
http://10.0.2.2:8000/api/v1/
```

`10.0.2.2` is the Android emulator alias for the host machine. Use the host
machine's LAN address for a physical device, or the HTTPS API host in
production. The URL is editable in the app settings and is validated before it
is saved. Both of these forms are accepted and normalized to one `/api/v1`
segment:

```text
http://10.0.2.2:8000/
http://10.0.2.2:8000/api/v1/
```

Cleartext HTTP is enabled only in the debug Android manifest. Release builds
use Android's default cleartext block and the app rejects non-HTTPS URLs.

Never put credentials, tokens, or production secrets in source files, build
arguments, or checked-in configuration.

## Backend roles

The token response contains only the token. The app then calls
`GET /api/v1/auth/me/` and uses the server-derived effective role:

- **Owner:** active superuser or member of Django group `ERP Owners`.
- **Operational employee:** active staff user in `ERP Operational Staff` with the
  required model permissions.
- **Customer:** active non-staff user linked to an active customer.

`is_staff=True` alone grants no owner or employee API role. Assign groups and
permissions explicitly in Django admin before signing in. A failed profile
request or a `401` response clears the stored token.

## Token storage

Authentication tokens are persisted through `flutter_secure_storage`, using the
Android Keystore-backed secure store. Tests inject `InMemoryTokenStore`; there is
no plaintext token fallback and tokens are not stored in `shared_preferences`.
Language and API base URL are non-secret preferences.

## Implemented screens

- Login, role routing, splash restoration, Arabic/English localization, and RTL/LTR.
- Owner dashboard, costing periods, inventory/stock, sales, purchasing,
  production, costing, and monthly-profit views.
- Employee production order list/detail, operation progress, good/scrap results,
  measured material waste and reviewed-zero evidence, material issues, finished
  receipts, and counter start/close/approve/correct.
- Customer product/price catalog and draft order requests.

The verification in this phase produced an Android debug APK; iOS, Flutter web, and a signed release APK remain future work.

Forms use IDs returned by the API for products, units, orders, requirements,
counters, and locations. Do not add hardcoded IDs or client-side business rules
that are not present on the backend.

## Commands

Run from this directory:

```powershell
flutter pub get
flutter analyze
flutter test --reporter compact
flutter build apk --debug
```

Verified result at the latest handoff:

- `flutter pub get`: exit 0.
- `flutter analyze`: exit 0, no issues.
- `flutter test --reporter compact`: exit 0, 23 tests passed.
- `flutter build apk --debug`: exit 0.
- APK: `build/app/outputs/flutter-apk/app-debug.apk`
  (188,189,960 bytes).

## Windows build notes

If the pub cache and project are on different Windows drives, Kotlin incremental
caches can fail with a `different roots` error. This project sets
`kotlin.incremental=false` in `android/gradle.properties` so local builds do not
depend on that cache behavior.

The Flutter Android build may install CMake automatically. If the NDK is
missing, install the exact version reported by the build. For this workspace:

```powershell
$sdk = Join-Path $env:LOCALAPPDATA 'Android\sdk'
& "$sdk\cmdline-tools\latest\bin\android.exe" sdk install 'ndk/28.2.13676358'
```

## Limits

- The verified build is Android debug only. iOS, Flutter web, and a signed
  release APK are not built here.
- There is no offline write queue, push notification, or WebSocket transport.
  Screens refresh from the API on demand and after writes.
- Financial screens show persisted management/non-accounting values and backend
  warnings; the client does not recompute costs, profit, waste, or inventory.
- Measured material waste is shown only when a real employee waste record exists.
  Shortfall, counter differences, usable leftovers, and unexplained stock
  differences are never converted into waste by the client.
