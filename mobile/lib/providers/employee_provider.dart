import 'package:flutter/material.dart';
import '../models/employee_models.dart';
import '../services/api_client.dart';
import '../services/employee_service.dart';

class EmployeeProvider extends ChangeNotifier {
  final EmployeeService _service;

  List<EmployeeProductionOrder> _orders = [];
  bool _isLoading = false;
  String? _errorMessage;

  EmployeeProvider(this._service);

  List<EmployeeProductionOrder> get orders => _orders;
  bool get isLoading => _isLoading;
  String? get errorMessage => _errorMessage;

  Future<void> fetchOrders() async {
    _isLoading = true;
    _errorMessage = null;
    notifyListeners();

    try {
      _orders = await _service.getProductionOrders();
      _errorMessage = null;
    } on ApiException catch (e) {
      _errorMessage = e.message;
    } catch (e) {
      _errorMessage = 'خطأ أثناء تحميل أوامر الإنتاج: $e';
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  Future<EmployeeProductionOrder?> getOrderDetail(int id) async {
    try {
      return await _service.getProductionOrderDetail(id);
    } catch (_) {
      return null;
    }
  }
}
