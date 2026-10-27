import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:paper_factory_mobile/models/customer_models.dart';
import 'package:paper_factory_mobile/services/api_client.dart';
import 'package:paper_factory_mobile/services/customer_service.dart';
import 'package:paper_factory_mobile/services/owner_service.dart';
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
  group('Owner and Customer Services Tests', () {
    late StorageService storageService;

    setUp(() async {
      SharedPreferences.setMockInitialValues({});
      storageService = await StorageService.init(
        tokenStore: InMemoryTokenStore(initialToken: 'test-owner-token'),
      );
    });

    test('OwnerService fetches dashboard summary and costing periods', () async {
      final mockClient = MockClient((request) async {
        if (request.url.path.endsWith('/owner/dashboard/')) {
          expect(request.headers['Authorization'], 'Token test-owner-token');
          return http.Response(
            jsonEncode({
              'period': {},
              'sales': {'net_sales_value': '200000.00', 'orders_total': 25},
              'purchases': {'net_purchases_value': '95000.00'},
              'inventory': {'low_stock_items': []},
              'production': {},
              'costing': {},
              'profit_loss': {'gross_profit': '60000.00', 'monthly_result': '30000.00'},
              'data_quality': [],
              'accounting_available': false,
              'accounting_note': 'Not implemented',
            }),
            200,
          );
        } else if (request.url.path.endsWith('/owner/costing/periods/')) {
          return http.Response(
            jsonEncode({
              'count': 1,
              'results': [
                {
                  'id': 10,
                  'year': 2026,
                  'month': 10,
                  'status': 'FINALIZED',
                  'total_production_cost': '120000.00',
                  'actual_material_cost': '80000.00',
                  'total_labor_expense': '25000.00',
                  'total_electricity_expense': '15000.00',
                  'is_complete': true,
                  'missing_data_notes': [],
                }
              ]
            }),
            200,
          );
        }
        return http.Response('Not Found', 404);
      });

      final apiClient = ApiClient(storageService: storageService, httpClient: mockClient);
      final ownerService = OwnerService(apiClient);

      final dash = await ownerService.getDashboardSummary();
      expect(dash.netSalesValue, '200000.00');
      expect(dash.totalSalesOrders, '25');

      final periods = await ownerService.getCostingPeriods();
      expect(periods.length, 1);
      expect(periods.first.month, 10);
      expect(periods.first.status, 'FINALIZED');
    });

    test('CustomerService fetches catalog and submits order request', () async {
      final mockClient = MockClient((request) async {
        if (request.url.path.endsWith('/customer/products/')) {
          // Use utf8 encoding because product names contain Arabic characters.
          return _utf8Response({
            'count': 1,
            'results': [
              {
                'id': 12,
                'sku': 'CUP-7OZ',
                'name_ar': 'كوب 7 أونصة',
                'name_en': 'Cup 7oz',
                'base_uom': {'code': 'كرتونة'},
                'reference_price': {'amount': '180.00', 'unit': {'name_ar': 'كرتونة'}},
                'units': [],
              }
            ]
          }, 200);
        } else if (request.url.path.endsWith('/customer/order-requests/')) {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          expect(body['customer_reference'], 'PO-2026-X');
          final lines = body['lines'] as List;
          expect(lines.length, 1);
          expect(lines.first['product'], 12);
          expect(lines.first['quantity'], '50.0');

          return http.Response(
            jsonEncode({
              'order_number': 'SO-REQ-001',
              'status': 'DRAFT',
              'pricing_status': 'PENDING_REVIEW',
            }),
            201,
          );
        }
        return http.Response('Not Found', 404);
      });

      final apiClient = ApiClient(storageService: storageService, httpClient: mockClient);
      final customerService = CustomerService(apiClient);

      final prods = await customerService.getProducts();
      expect(prods.length, 1);
      expect(prods.first.sku, 'CUP-7OZ');

      final res = await customerService.submitOrderRequest(
        customerReference: 'PO-2026-X',
        lines: [
          const CustomerOrderRequestLine(product: 12, quantity: '50.0', unit: 1),
        ],
      );
      expect(res['order_number'], 'SO-REQ-001');
      expect(res['pricing_status'], 'PENDING_REVIEW');
    });
  });
}
