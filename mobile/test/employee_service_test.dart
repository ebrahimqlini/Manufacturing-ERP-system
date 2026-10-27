import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:paper_factory_mobile/services/api_client.dart';
import 'package:paper_factory_mobile/services/employee_service.dart';
import 'package:paper_factory_mobile/services/storage_service.dart';
import 'package:paper_factory_mobile/services/token_store.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  group('Employee Service Request Construction and Workflow Tests', () {
    late StorageService storageService;

    setUp(() async {
      SharedPreferences.setMockInitialValues({});
      storageService = await StorageService.init(
        tokenStore: InMemoryTokenStore(initialToken: 'test-operator-token'),
      );
    });

    test('recordProgress sends exact required fields and parses response', () async {
      final mockClient = MockClient((request) async {
        expect(request.url.path, endsWith('/employee/production/progress/'));
        expect(request.headers['Authorization'], 'Token test-operator-token');
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(body['order_id'], 101);
        expect(body['operation_id'], 2);
        expect(body['quantity'], '50.0');
        expect(body['unit_id'], 1);
        expect(body['notes'], 'Shift A progress');

        return http.Response(jsonEncode({'id': 501, 'created': true}), 201);
      });

      final apiClient = ApiClient(storageService: storageService, httpClient: mockClient);
      final service = EmployeeService(apiClient);

      final res = await service.recordProgress(
        orderId: 101,
        operationId: 2,
        quantity: '50.0',
        unitId: 1,
        notes: 'Shift A progress',
      );

      expect(res['id'], 501);
      expect(res['created'], isTrue);
    });

    test('recordWaste sends exact measured waste payload and snapshots', () async {
      final mockClient = MockClient((request) async {
        expect(request.url.path, endsWith('/employee/production/results/201/waste/'));
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(body['material_id'], 7);
        expect(body['quantity_base'], '15.250000000000');
        expect(body['roll_reference'], 'ROLL-OCT-99');

        return http.Response(jsonEncode({'id': 801, 'posted_at': '2026-10-24T12:00:00Z'}), 201);
      });

      final apiClient = ApiClient(storageService: storageService, httpClient: mockClient);
      final service = EmployeeService(apiClient);

      final res = await service.recordWaste(
        resultId: 201,
        materialId: 7,
        quantityBase: '15.250000000000',
        rollReference: 'ROLL-OCT-99',
      );

      expect(res['id'], 801);
      expect(res['posted_at'], isNotNull);
    });

    test('confirmZeroWaste posts empty payload to waste-review endpoint', () async {
      final mockClient = MockClient((request) async {
        expect(request.url.path, endsWith('/employee/production/results/201/waste-review/'));
        expect(request.body, '{}');

        return http.Response(jsonEncode({'result_id': 201, 'reviewed_at': '2026-10-24T12:05:00Z'}), 201);
      });

      final apiClient = ApiClient(storageService: storageService, httpClient: mockClient);
      final service = EmployeeService(apiClient);

      final res = await service.confirmZeroWaste(201);
      expect(res['result_id'], 201);
      expect(res['reviewed_at'], isNotNull);
    });

    test('issueMaterial sends order requirement and unique reference', () async {
      final mockClient = MockClient((request) async {
        expect(request.url.path, endsWith('/employee/material-issues/'));
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(body['order_id'], 101);
        expect(body['requirement_id'], 51);
        expect(body['quantity'], '100.0');
        expect(body['unit_id'], 1);
        expect(body['reference'], 'ISSUE-REF-001');

        return http.Response(jsonEncode({'id': 'UUID-ISSUE-1', 'posted_at': '2026-10-24T12:10:00Z'}), 201);
      });

      final apiClient = ApiClient(storageService: storageService, httpClient: mockClient);
      final service = EmployeeService(apiClient);

      final res = await service.issueMaterial(
        orderId: 101,
        requirementId: 51,
        quantity: '100.0',
        unitId: 1,
        reference: 'ISSUE-REF-001',
      );

      expect(res['id'], 'UUID-ISSUE-1');
    });

    test('receiveFinishedGoods sends valid receipt with location and reference', () async {
      final mockClient = MockClient((request) async {
        expect(request.url.path, endsWith('/employee/finished-receipts/'));
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(body['order_id'], 101);
        expect(body['quantity'], '250.0');
        expect(body['location_id'], 5);
        expect(body['reference'], 'REC-FG-001');

        return http.Response(jsonEncode({'id': 'UUID-REC-1', 'reference': 'REC-FG-001', 'posted_at': '2026-10-24T12:15:00Z'}), 201);
      });

      final apiClient = ApiClient(storageService: storageService, httpClient: mockClient);
      final service = EmployeeService(apiClient);

      final res = await service.receiveFinishedGoods(
        orderId: 101,
        quantity: '250.0',
        unitId: 1,
        locationId: 5,
        reference: 'REC-FG-001',
      );

      expect(res['reference'], 'REC-FG-001');
    });

    test('counter start, close, approve, and correct workflows', () async {
      final mockClient = MockClient((request) async {
        if (request.url.path.endsWith('/employee/counters/')) {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          expect(body['order_id'], 101);
          expect(body['start_reading'], '100.0');
          return http.Response(jsonEncode({'id': 901, 'status': 'PROVISIONAL'}), 201);
        } else if (request.url.path.endsWith('/employee/counters/901/close/')) {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          expect(body['end_reading'], '250.0');
          return http.Response(jsonEncode({'id': 901, 'status': 'COMPLETE'}), 200);
        } else if (request.url.path.endsWith('/employee/counters/901/approve/')) {
          return http.Response(jsonEncode({'id': 901, 'status': 'APPROVED'}), 200);
        } else if (request.url.path.endsWith('/employee/counters/901/correct/')) {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          expect(body['reason'], 'عداد غير مضبوط في البداية');
          return http.Response(jsonEncode({'id': 901, 'status': 'CORRECTED'}), 200);
        }
        return http.Response('Not Found', 404);
      });

      final apiClient = ApiClient(storageService: storageService, httpClient: mockClient);
      final service = EmployeeService(apiClient);

      final start = await service.startCounter(orderId: 101, unitId: 1, startReading: '100.0');
      expect(start['status'], 'PROVISIONAL');

      final close = await service.closeCounter(counterId: 901, endReading: '250.0');
      expect(close['status'], 'COMPLETE');

      final approve = await service.approveCounter(901);
      expect(approve['status'], 'APPROVED');

      final correct = await service.correctCounter(counterId: 901, reason: 'عداد غير مضبوط في البداية');
      expect(correct['status'], 'CORRECTED');
    });
  });
}
