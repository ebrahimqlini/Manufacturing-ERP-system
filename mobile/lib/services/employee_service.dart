import '../models/employee_models.dart';
import 'api_client.dart';

class EmployeeService {
  final ApiClient _apiClient;

  EmployeeService(this._apiClient);

  Future<List<EmployeeProductionOrder>> getProductionOrders() async {
    final res = await _apiClient.get('/employee/production/orders/');
    if (res is List) {
      return res
          .map((e) => EmployeeProductionOrder.fromJson(e as Map<String, dynamic>))
          .toList();
    }
    return [];
  }

  Future<EmployeeProductionOrder> getProductionOrderDetail(int id) async {
    final res = await _apiClient.get('/employee/production/orders/$id/');
    return EmployeeProductionOrder.fromJson(res as Map<String, dynamic>);
  }

  Future<Map<String, dynamic>> recordProgress({
    required int orderId,
    required int operationId,
    required String quantity,
    required int unitId,
    String? notes,
    String? submissionKey,
  }) async {
    final body = <String, dynamic>{
      'order_id': orderId,
      'operation_id': operationId,
      'quantity': quantity,
      'unit_id': unitId,
      if (notes != null && notes.isNotEmpty) 'notes': notes,
      if (submissionKey != null && submissionKey.isNotEmpty) 'submission_key': submissionKey,
    };
    final res = await _apiClient.post('/employee/production/progress/', body: body);
    return res as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> completeOperation({
    required int orderId,
    required int operationId,
    bool skip = false,
  }) async {
    final body = <String, dynamic>{
      'order_id': orderId,
      'operation_id': operationId,
      'skip': skip,
    };
    final res = await _apiClient.post('/employee/production/operations/complete/', body: body);
    return res as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> recordResult({
    required int orderId,
    required String goodQuantity,
    required String scrapQuantity,
    required int unitId,
    String? notes,
    String? submissionKey,
  }) async {
    final body = <String, dynamic>{
      'order_id': orderId,
      'good_quantity': goodQuantity,
      'scrap_quantity': scrapQuantity,
      'unit_id': unitId,
      if (notes != null && notes.isNotEmpty) 'notes': notes,
      if (submissionKey != null && submissionKey.isNotEmpty) 'submission_key': submissionKey,
    };
    final res = await _apiClient.post('/employee/production/results/', body: body);
    return res as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> reverseResult({
    required int resultId,
    required String reason,
  }) async {
    final res = await _apiClient.post(
      '/employee/production/results/$resultId/reverse/',
      body: {'reason': reason.trim()},
    );
    return res as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> recordWaste({
    required int resultId,
    required int materialId,
    required String quantityBase,
    int? materialIssueId,
    String? rollReference,
  }) async {
    final body = <String, dynamic>{
      'material_id': materialId,
      'quantity_base': quantityBase,
      if (materialIssueId != null) 'material_issue_id': materialIssueId,
      if (rollReference != null && rollReference.isNotEmpty) 'roll_reference': rollReference,
    };
    final res = await _apiClient.post(
      '/employee/production/results/$resultId/waste/',
      body: body,
    );
    return res as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> confirmZeroWaste(int resultId) async {
    final res = await _apiClient.post(
      '/employee/production/results/$resultId/waste-review/',
      body: {}, // Rejects extra fields
    );
    return res as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> reverseWaste({
    required int wasteId,
    required String reason,
  }) async {
    final res = await _apiClient.post(
      '/employee/production/waste/$wasteId/reverse/',
      body: {'reason': reason.trim()},
    );
    return res as Map<String, dynamic>;
  }

  Future<EmployeeMaterialsData> getMaterialsData() async {
    final res = await _apiClient.get('/employee/materials/');
    return EmployeeMaterialsData.fromJson(res as Map<String, dynamic>);
  }

  Future<Map<String, dynamic>> issueMaterial({
    required int orderId,
    required int requirementId,
    required String quantity,
    required int unitId,
    required String reference,
  }) async {
    final body = <String, dynamic>{
      'order_id': orderId,
      'requirement_id': requirementId,
      'quantity': quantity,
      'unit_id': unitId,
      'reference': reference.trim(),
    };
    final res = await _apiClient.post('/employee/material-issues/', body: body);
    return res as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> startCounter({
    required int orderId,
    required int unitId,
    required String startReading,
    int? operationId,
    int? materialIssueId,
    String? rollReference,
    String? notes,
    int? continuationId,
    String? resetReason,
  }) async {
    final body = <String, dynamic>{
      'order_id': orderId,
      'unit_id': unitId,
      'start_reading': startReading,
      if (operationId != null) 'operation_id': operationId,
      if (materialIssueId != null) 'material_issue_id': materialIssueId,
      if (rollReference != null && rollReference.isNotEmpty) 'roll_reference': rollReference,
      if (notes != null && notes.isNotEmpty) 'notes': notes,
      if (continuationId != null) 'continuation_id': continuationId,
      if (resetReason != null && resetReason.isNotEmpty) 'reset_reason': resetReason,
    };
    final res = await _apiClient.post('/employee/counters/', body: body);
    return res as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> closeCounter({
    required int counterId,
    required String endReading,
    String? rejectedQuantity,
  }) async {
    final body = <String, dynamic>{
      'end_reading': endReading,
      if (rejectedQuantity != null && rejectedQuantity.isNotEmpty)
        'rejected_quantity': rejectedQuantity,
    };
    final res = await _apiClient.post(
      '/employee/counters/$counterId/close/',
      body: body,
    );
    return res as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> approveCounter(int counterId) async {
    final res = await _apiClient.post(
      '/employee/counters/$counterId/approve/',
      body: {}, // Rejects extra fields
    );
    return res as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> correctCounter({
    required int counterId,
    required String reason,
    String? startReading,
    String? endReading,
    String? rejectedQuantity,
    String? rollReference,
    String? notes,
  }) async {
    final body = <String, dynamic>{
      'reason': reason.trim(),
      if (startReading != null && startReading.isNotEmpty) 'start_reading': startReading,
      if (endReading != null && endReading.isNotEmpty) 'end_reading': endReading,
      if (rejectedQuantity != null && rejectedQuantity.isNotEmpty)
        'rejected_quantity': rejectedQuantity,
      if (rollReference != null && rollReference.isNotEmpty) 'roll_reference': rollReference,
      if (notes != null && notes.isNotEmpty) 'notes': notes,
    };
    final res = await _apiClient.post(
      '/employee/counters/$counterId/correct/',
      body: body,
    );
    return res as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> receiveFinishedGoods({
    required int orderId,
    required String quantity,
    required int unitId,
    required int locationId,
    required String reference,
  }) async {
    final body = <String, dynamic>{
      'order_id': orderId,
      'quantity': quantity,
      'unit_id': unitId,
      'location_id': locationId,
      'reference': reference.trim(),
    };
    final res = await _apiClient.post('/employee/finished-receipts/', body: body);
    return res as Map<String, dynamic>;
  }
}
