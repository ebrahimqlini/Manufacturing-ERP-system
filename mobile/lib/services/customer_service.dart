import '../models/customer_models.dart';
import 'api_client.dart';

class CustomerService {
  final ApiClient _apiClient;

  CustomerService(this._apiClient);

  Future<CustomerProfile> getProfile() async {
    final res = await _apiClient.get('/customer/profile/');
    return CustomerProfile.fromJson(res as Map<String, dynamic>);
  }

  Future<List<CustomerProduct>> getProducts({String? category, String? search}) async {
    final queryParams = <String, String>{};
    if (category != null && category.isNotEmpty) {
      queryParams['category'] = category;
    }
    if (search != null && search.isNotEmpty) {
      queryParams['q'] = search;
    }
    final res = await _apiClient.get('/customer/products/', queryParams: queryParams.isNotEmpty ? queryParams : null);
    if (res is Map && res.containsKey('results') && res['results'] is List) {
      return (res['results'] as List)
          .map((e) => CustomerProduct.fromJson(e as Map<String, dynamic>))
          .toList();
    }
    return [];
  }

  Future<List<CustomerOrderSummary>> getOrders({String? status}) async {
    final queryParams = <String, String>{};
    if (status != null && status.isNotEmpty) {
      queryParams['status'] = status;
    }
    final res = await _apiClient.get('/customer/orders/', queryParams: queryParams.isNotEmpty ? queryParams : null);
    if (res is Map && res.containsKey('results') && res['results'] is List) {
      return (res['results'] as List)
          .map((e) => CustomerOrderSummary.fromJson(e as Map<String, dynamic>))
          .toList();
    }
    return [];
  }

  Future<Map<String, dynamic>> submitOrderRequest({
    String? requestedDeliveryDate,
    String? customerReference,
    String? notes,
    required List<CustomerOrderRequestLine> lines,
  }) async {
    final body = <String, dynamic>{
      if (requestedDeliveryDate != null && requestedDeliveryDate.isNotEmpty)
        'requested_delivery_date': requestedDeliveryDate,
      if (customerReference != null && customerReference.isNotEmpty)
        'customer_reference': customerReference,
      if (notes != null && notes.isNotEmpty) 'notes': notes,
      'lines': lines.map((l) => l.toJson()).toList(),
    };
    final res = await _apiClient.post('/customer/order-requests/', body: body);
    return res as Map<String, dynamic>;
  }
}
