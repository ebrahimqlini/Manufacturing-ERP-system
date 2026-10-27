import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:paper_factory_mobile/models/user_model.dart';
import 'package:paper_factory_mobile/services/api_client.dart';
import 'package:paper_factory_mobile/services/auth_service.dart';
import 'package:paper_factory_mobile/services/storage_service.dart';
import 'package:paper_factory_mobile/services/token_store.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Build a mock [http.Response] that correctly encodes [body] as UTF-8.
/// Using [http.Response] directly with a plain Dart string encodes via Latin-1,
/// which fails on any non-ASCII characters (e.g. Arabic text in JSON fixtures).
http.Response _utf8Response(Map<String, dynamic> bodyMap, int statusCode) {
  final encoded = utf8.encode(jsonEncode(bodyMap));
  return http.Response.bytes(
    encoded,
    statusCode,
    headers: {'content-type': 'application/json; charset=utf-8'},
  );
}

void main() {
  group('Authentication and Role Routing Tests', () {
    late StorageService storageService;

    setUp(() async {
      SharedPreferences.setMockInitialValues({});
      storageService = await StorageService.init(
        tokenStore: InMemoryTokenStore(),
      );
    });

    test('Login success exchanges credentials for token and fetches current user', () async {
      final mockClient = MockClient((request) async {
        if (request.url.path.endsWith('/auth/token/')) {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          if (body['username'] == 'valid_user' && body['password'] == 'valid_pass') {
            return http.Response(jsonEncode({'token': 'mock-token-xyz-123'}), 200);
          }
          return http.Response(
            jsonEncode({'non_field_errors': ['Unable to log in with provided credentials.']}),
            400,
          );
        } else if (request.url.path.endsWith('/auth/me/')) {
          expect(request.headers['Authorization'], 'Token mock-token-xyz-123');
          return _utf8Response({
            'username': 'valid_user',
            'first_name': 'محمد',
            'last_name': 'علي',
            'email': 'user@factory.local',
            'is_superuser': false,
            'is_staff': true,
            'is_owner': false,
            'is_employee': true,
            'is_customer': false,
            'role': 'EMPLOYEE',
            'customer_id': null,
            'permissions': ['manufacturing.record_counter_reading'],
          }, 200);
        }
        return http.Response('Not Found', 404);
      });

      final apiClient = ApiClient(storageService: storageService, httpClient: mockClient);
      final authService = AuthService(apiClient: apiClient, storageService: storageService);

      final user = await authService.login('valid_user', 'valid_pass');

      expect(user.username, 'valid_user');
      expect(user.role, UserRole.employee);
      expect(storageService.getToken(), 'mock-token-xyz-123');
      expect(authService.hasSavedToken(), isTrue);

      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString('auth_token'), isNull);
    });

    test('Login failure throws ApiException with backend error message', () async {
      final mockClient = MockClient((request) async {
        return _utf8Response({
          'error': {
            'status': 400,
            'detail': {'non_field_errors': ['بيانات الدخول غير صحيحة.']},
          },
        }, 400);
      });

      final apiClient = ApiClient(storageService: storageService, httpClient: mockClient);
      final authService = AuthService(apiClient: apiClient, storageService: storageService);

      expect(
        () async => await authService.login('wrong_user', 'wrong_pass'),
        throwsA(isA<ApiException>()),
      );
      expect(storageService.getToken(), isNull);
    });

    test('Login clears token when profile discovery fails', () async {
      final mockClient = MockClient((request) async {
        if (request.url.path.endsWith('/auth/token/')) {
          return http.Response(jsonEncode({'token': 'orphan-token'}), 200);
        }
        return _utf8Response({
          'error': {'status': 401, 'detail': 'Invalid token.'},
        }, 401);
      });

      final apiClient = ApiClient(storageService: storageService, httpClient: mockClient);
      final authService = AuthService(apiClient: apiClient, storageService: storageService);

      await expectLater(
        authService.login('valid_user', 'valid_pass'),
        throwsA(isA<ApiException>()),
      );
      expect(storageService.getToken(), isNull);
      expect(authService.hasSavedToken(), isFalse);
    });

    test('Logout clears local token', () async {
      await storageService.saveToken('token-to-be-deleted');
      expect(storageService.getToken(), 'token-to-be-deleted');

      final mockClient = MockClient((request) async {
        return http.Response('', 204);
      });

      final apiClient = ApiClient(storageService: storageService, httpClient: mockClient);
      final authService = AuthService(apiClient: apiClient, storageService: storageService);

      await authService.logout();
      expect(storageService.getToken(), isNull);
      expect(authService.hasSavedToken(), isFalse);
    });
  });
}
