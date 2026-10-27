import '../models/owner_models.dart';
import 'api_client.dart';

class OwnerService {
  final ApiClient _apiClient;

  OwnerService(this._apiClient);

  Future<OwnerDashboardSummary> getDashboardSummary({
    String? startDate,
    String? endDate,
  }) async {
    final queryParams = <String, String>{};
    if (startDate != null && endDate != null) {
      queryParams['start_date'] = startDate;
      queryParams['end_date'] = endDate;
    }
    final res = await _apiClient.get('/owner/dashboard/', queryParams: queryParams.isNotEmpty ? queryParams : null);
    return OwnerDashboardSummary.fromJson(res as Map<String, dynamic>);
  }

  Future<List<OwnerCostingPeriodItem>> getCostingPeriods() async {
    final res = await _apiClient.get('/owner/costing/periods/');
    if (res is Map && res.containsKey('results') && res['results'] is List) {
      return (res['results'] as List)
          .map((e) => OwnerCostingPeriodItem.fromJson(e as Map<String, dynamic>))
          .toList();
    }
    return [];
  }

  Future<List<OwnerStockRowItem>> getStockRows() async {
    final res = await _apiClient.get('/owner/inventory/stock/');
    if (res is Map && res.containsKey('results') && res['results'] is List) {
      return (res['results'] as List)
          .map((e) => OwnerStockRowItem.fromJson(e as Map<String, dynamic>))
          .toList();
    }
    return [];
  }
}
