import '../models/user_model.dart';
import 'api_client.dart';
import 'storage_service.dart';

class AuthService {
  final ApiClient apiClient;
  final StorageService storageService;

  AuthService({
    required this.apiClient,
    required this.storageService,
  });

  Future<CurrentUser> login(String username, String password) async {
    // 1. Obtain Token
    final tokenRes = await apiClient.post(
      '/auth/token/',
      body: {
        'username': username.trim(),
        'password': password,
      },
      authenticated: false,
    );

    if (tokenRes is! Map || !tokenRes.containsKey('token')) {
      throw ApiException(
        statusCode: 500,
        message: 'استجابة غير صالحة من خادم المصادقة.',
      );
    }

    final String token = tokenRes['token'].toString();
    await storageService.saveToken(token);

    // 2. Fetch authenticated profile and role via /auth/me/. If identity or
    // role discovery fails, do not leave a token that the app cannot use.
    try {
      return await getCurrentUser();
    } catch (_) {
      await storageService.clearToken();
      rethrow;
    }
  }

  Future<CurrentUser> getCurrentUser() async {
    try {
      final res = await apiClient.get('/auth/me/');
      if (res is! Map<String, dynamic>) {
        throw ApiException(
          statusCode: 500,
          message: 'فشل استرجاع بيانات المستخدم.',
        );
      }
      return CurrentUser.fromJson(res);
    } on ApiException catch (e) {
      if (e.statusCode == 401) {
        await storageService.clearToken();
      }
      rethrow;
    }
  }

  Future<void> logout() async {
    try {
      await apiClient.post('/auth/logout/');
    } catch (_) {
      // Ignore network errors on logout
    } finally {
      await storageService.clearToken();
    }
  }

  bool hasSavedToken() {
    final token = storageService.getToken();
    return token != null && token.isNotEmpty;
  }
}
