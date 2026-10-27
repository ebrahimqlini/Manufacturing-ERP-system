import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:paper_factory_mobile/services/api_client.dart';
import 'package:paper_factory_mobile/services/storage_service.dart';
import 'package:paper_factory_mobile/services/token_store.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  group('API base URL normalization', () {
    late StorageService storageService;

    setUp(() async {
      SharedPreferences.setMockInitialValues({});
      storageService = await StorageService.init(tokenStore: InMemoryTokenStore());
    });

    Future<Uri> requestUriFor(String baseUrl) async {
      await storageService.saveBaseUrl(baseUrl);
      late Uri captured;
      final client = ApiClient(
        storageService: storageService,
        httpClient: MockClient((request) async {
          captured = request.url;
          return http.Response('{}', 200);
        }),
      );
      await client.get('/ping/');
      return captured;
    }

    test('accepts a base URL that already includes /api/v1', () async {
      final uri = await requestUriFor('http://example.test:8000/api/v1/');
      expect(uri.toString(), 'http://example.test:8000/api/v1/ping/');
    });

    test('adds /api/v1 exactly once when the base URL omits it', () async {
      final uri = await requestUriFor('http://example.test:8000/');
      expect(uri.toString(), 'http://example.test:8000/api/v1/ping/');
    });
  });
}
