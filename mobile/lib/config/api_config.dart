/// Centralized API configuration for the Manufacturing ERP mobile application.
/// Supports separate development (Android emulator, LAN) and production (HTTPS)
/// environments. The API client accepts the base URL with or without the
/// trailing `/api/v1` segment.
class ApiConfig {
  // Default development URL for the Android emulator connecting to the host.
  static const String defaultEmulatorBaseUrl = 'http://10.0.2.2:8000/api/v1';
  // Default development URL for a physical device or desktop.
  static const String defaultLocalhostBaseUrl = 'http://127.0.0.1:8000/api/v1';

  static const String apiVersion = '/api/v1';

  static const Duration connectTimeout = Duration(seconds: 15);
  static const Duration receiveTimeout = Duration(seconds: 20);

  /// Preset environment targets.
  static const List<EnvironmentPreset> presets = [
    EnvironmentPreset(
      nameAr: 'محاكي أندرويد (10.0.2.2)',
      nameEn: 'Android Emulator (10.0.2.2)',
      url: defaultEmulatorBaseUrl,
    ),
    EnvironmentPreset(
      nameAr: 'جهاز محلي (127.0.0.1)',
      nameEn: 'Local Desktop (127.0.0.1)',
      url: defaultLocalhostBaseUrl,
    ),
  ];
}

class EnvironmentPreset {
  final String nameAr;
  final String nameEn;
  final String url;

  const EnvironmentPreset({
    required this.nameAr,
    required this.nameEn,
    required this.url,
  });
}
