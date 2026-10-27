import 'package:flutter/material.dart';
import '../models/user_model.dart';
import '../services/api_client.dart';
import '../services/auth_service.dart';

enum AuthStatus {
  initial,
  loading,
  authenticated,
  unauthenticated,
}

class AuthProvider extends ChangeNotifier {
  final AuthService _authService;

  AuthStatus _status = AuthStatus.initial;
  CurrentUser? _currentUser;
  String? _errorMessage;

  AuthProvider(this._authService);

  AuthStatus get status => _status;
  CurrentUser? get currentUser => _currentUser;
  String? get errorMessage => _errorMessage;
  bool get isAuthenticated => _status == AuthStatus.authenticated && _currentUser != null;

  Future<void> checkAuthStatus() async {
    if (!_authService.hasSavedToken()) {
      _status = AuthStatus.unauthenticated;
      notifyListeners();
      return;
    }

    _status = AuthStatus.loading;
    notifyListeners();

    try {
      _currentUser = await _authService.getCurrentUser();
      _status = AuthStatus.authenticated;
      _errorMessage = null;
    } on ApiException catch (e) {
      _status = AuthStatus.unauthenticated;
      _currentUser = null;
      _errorMessage = e.message;
    } catch (e) {
      _status = AuthStatus.unauthenticated;
      _currentUser = null;
      _errorMessage = 'حدث خطأ أثناء فحص جلسة الدخول: $e';
    }
    notifyListeners();
  }

  Future<bool> login(String username, String password) async {
    _status = AuthStatus.loading;
    _errorMessage = null;
    notifyListeners();

    try {
      _currentUser = await _authService.login(username, password);
      _status = AuthStatus.authenticated;
      _errorMessage = null;
      notifyListeners();
      return true;
    } on ApiException catch (e) {
      _status = AuthStatus.unauthenticated;
      _currentUser = null;
      _errorMessage = e.message;
      notifyListeners();
      return false;
    } catch (e) {
      _status = AuthStatus.unauthenticated;
      _currentUser = null;
      _errorMessage = 'فشل الاتصال بالخادم: $e';
      notifyListeners();
      return false;
    }
  }

  Future<void> logout() async {
    _status = AuthStatus.loading;
    notifyListeners();

    await _authService.logout();
    _currentUser = null;
    _status = AuthStatus.unauthenticated;
    _errorMessage = null;
    notifyListeners();
  }
}
