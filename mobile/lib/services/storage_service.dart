import 'package:shared_preferences/shared_preferences.dart';
import '../config/api_config.dart';
import 'token_store.dart';

class StorageService {
  static const String _languageKey = 'app_language';
  static const String _baseUrlKey = 'api_base_url';

  final SharedPreferences _prefs;
  final TokenStore _tokenStore;

  String? _cachedToken;

  StorageService(this._prefs, this._tokenStore);

  static Future<StorageService> init({TokenStore? tokenStore}) async {
    final prefs = await SharedPreferences.getInstance();
    final store = tokenStore ?? SecureTokenStore();
    final service = StorageService(prefs, store);
    service._cachedToken = await store.read();
    return service;
  }

  // Token Management. The token is cached in memory for synchronous request
  // headers and persisted only through the platform secure store.
  Future<void> saveToken(String token) async {
    _cachedToken = token;
    await _tokenStore.write(token);
  }

  String? getToken() => _cachedToken;

  Future<void> clearToken() async {
    _cachedToken = null;
    await _tokenStore.delete();
  }

  // Language Preference
  Future<void> saveLanguage(String code) async {
    await _prefs.setString(_languageKey, code);
  }

  String getLanguage() {
    return _prefs.getString(_languageKey) ?? 'ar';
  }

  // Base URL Configuration
  Future<void> saveBaseUrl(String url) async {
    await _prefs.setString(_baseUrlKey, url.trim());
  }

  String getBaseUrl() {
    return _prefs.getString(_baseUrlKey) ?? ApiConfig.defaultEmulatorBaseUrl;
  }
}
