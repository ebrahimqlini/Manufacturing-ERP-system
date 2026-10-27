import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Persistence contract for the authentication token.
///
/// The production implementation uses the platform secure store (Android
/// Keystore-backed encryption, iOS Keychain). Tests inject an in-memory
/// implementation so no platform channel is required.
abstract class TokenStore {
  Future<String?> read();
  Future<void> write(String token);
  Future<void> delete();
}

class SecureTokenStore implements TokenStore {
  static const String _tokenKey = 'auth_token';

  final FlutterSecureStorage _storage;

  SecureTokenStore({FlutterSecureStorage? storage})
      : _storage = storage ?? const FlutterSecureStorage();

  @override
  Future<String?> read() => _storage.read(key: _tokenKey);

  @override
  Future<void> write(String token) => _storage.write(key: _tokenKey, value: token);

  @override
  Future<void> delete() => _storage.delete(key: _tokenKey);
}

class InMemoryTokenStore implements TokenStore {
  String? _token;

  InMemoryTokenStore({String? initialToken}) : _token = initialToken;

  @override
  Future<String?> read() async => _token;

  @override
  Future<void> write(String token) async {
    _token = token;
  }

  @override
  Future<void> delete() async {
    _token = null;
  }
}
