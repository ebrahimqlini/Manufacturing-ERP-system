import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../l10n/app_localizations.dart';
import '../../models/employee_models.dart';
import '../../services/api_client.dart';
import '../../services/employee_service.dart';

class FinishedReceiptScreen extends StatefulWidget {
  const FinishedReceiptScreen({super.key});

  @override
  State<FinishedReceiptScreen> createState() => _FinishedReceiptScreenState();
}

class _FinishedReceiptScreenState extends State<FinishedReceiptScreen> {
  final _formKey = GlobalKey<FormState>();
  final _quantityController = TextEditingController();
  final _refController = TextEditingController();

  List<EmployeeProductionOrder> _orders = [];
  List<StorageLocationItem> _locations = [];
  EmployeeProductionOrder? _selectedOrder;
  StorageLocationItem? _selectedLocation;

  bool _isLoadingData = true;
  bool _isLoading = false;
  String? _message;
  bool _isError = false;

  @override
  void initState() {
    super.initState();
    _refController.text =
        'FG-REC-${DateTime.now().millisecondsSinceEpoch.toString().substring(6)}';
    _loadData();
  }

  @override
  void dispose() {
    _quantityController.dispose();
    _refController.dispose();
    super.dispose();
  }

  Future<void> _loadData() async {
    setState(() {
      _isLoadingData = true;
      _message = null;
    });
    final service = Provider.of<EmployeeService>(context, listen: false);
    try {
      final results = await Future.wait([
        service.getProductionOrders(),
        service.getMaterialsData(),
      ]);
      final orders = results[0] as List<EmployeeProductionOrder>;
      final materials = results[1] as EmployeeMaterialsData;
      if (!mounted) return;
      setState(() {
        _orders = orders;
        _locations = materials.locations;
        _selectedOrder = orders.isEmpty ? null : orders.first;
        _selectedLocation = _locations.isEmpty ? null : _locations.first;
        _isLoadingData = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _isError = true;
        _message = e.message;
        _isLoadingData = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isError = true;
        _message = '${context.tr('error')}: $e';
        _isLoadingData = false;
      });
    }
  }

  Future<void> _submitReceipt() async {
    if (!_formKey.currentState!.validate()) return;
    final order = _selectedOrder;
    final location = _selectedLocation;
    if (order == null || location == null) return;

    setState(() {
      _isLoading = true;
      _message = null;
    });

    final service = Provider.of<EmployeeService>(context, listen: false);

    try {
      final res = await service.receiveFinishedGoods(
        orderId: order.id,
        quantity: _quantityController.text.trim(),
        unitId: order.plannedUnitId,
        locationId: location.id,
        reference: _refController.text.trim(),
      );
      if (!mounted) return;
      setState(() {
        _message = '${context.tr('success')}: ${res['reference']}';
        _isError = false;
      });
      _quantityController.clear();
      _refController.text =
          'FG-REC-${DateTime.now().millisecondsSinceEpoch.toString().substring(6)}';
      await _loadData();
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _message = e.message;
        _isError = true;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _message = '${context.tr('error')}: $e';
        _isError = true;
      });
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(context.tr('receiveFinished')),
        actions: [
          IconButton(icon: const Icon(Icons.refresh), onPressed: _loadData),
        ],
      ),
      body: _isLoadingData
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                if (_message != null)
                  Container(
                    margin: const EdgeInsets.only(bottom: 16),
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: _isError ? Colors.red.shade50 : Colors.green.shade50,
                      border: Border.all(
                        color: _isError ? Colors.red.shade300 : Colors.green.shade300,
                      ),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Text(
                      _message!,
                      style: TextStyle(
                        color: _isError ? Colors.red.shade800 : Colors.green.shade800,
                      ),
                    ),
                  ),
                if (_orders.isEmpty || _locations.isEmpty)
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(16),
                      child: Text(context.tr('noData')),
                    ),
                  )
                else
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(16),
                      child: Form(
                        key: _formKey,
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            DropdownButtonFormField<int>(
                              initialValue: _selectedOrder?.id,
                              isExpanded: true,
                              decoration: InputDecoration(
                                labelText: context.tr('selectOrder'),
                              ),
                              items: _orders
                                  .map(
                                    (order) => DropdownMenuItem<int>(
                                      value: order.id,
                                      child: Text(
                                        '${order.orderNumber} - ${order.product}',
                                        overflow: TextOverflow.ellipsis,
                                      ),
                                    ),
                                  )
                                  .toList(),
                              onChanged: (value) {
                                setState(() {
                                  _selectedOrder = _orders.firstWhere(
                                    (o) => o.id == value,
                                    orElse: () => _orders.first,
                                  );
                                });
                              },
                              validator: (value) => value == null
                                  ? context.tr('requiredField')
                                  : null,
                            ),
                            const SizedBox(height: 12),
                            TextFormField(
                              controller: _quantityController,
                              keyboardType: const TextInputType.numberWithOptions(decimal: true),
                              decoration: InputDecoration(
                                labelText: context.tr('goodQuantity'),
                                suffixText: _selectedOrder?.unit,
                              ),
                              validator: (v) {
                                if (v == null || v.trim().isEmpty) {
                                  return context.tr('requiredField');
                                }
                                final val = double.tryParse(v);
                                if (val == null || val <= 0) {
                                  return context.tr('positiveNumberRequired');
                                }
                                return null;
                              },
                            ),
                            const SizedBox(height: 12),
                            DropdownButtonFormField<int>(
                              initialValue: _selectedLocation?.id,
                              isExpanded: true,
                              decoration: InputDecoration(
                                labelText: context.tr('selectLocation'),
                              ),
                              items: _locations
                                  .map(
                                    (location) => DropdownMenuItem<int>(
                                      value: location.id,
                                      child: Text(
                                        location.displayName,
                                        overflow: TextOverflow.ellipsis,
                                      ),
                                    ),
                                  )
                                  .toList(),
                              onChanged: (value) {
                                setState(() {
                                  _selectedLocation = _locations.firstWhere(
                                    (l) => l.id == value,
                                    orElse: () => _locations.first,
                                  );
                                });
                              },
                              validator: (value) => value == null
                                  ? context.tr('requiredField')
                                  : null,
                            ),
                            const SizedBox(height: 12),
                            TextFormField(
                              controller: _refController,
                              decoration: InputDecoration(labelText: context.tr('reference')),
                              validator: (v) => (v == null || v.trim().isEmpty)
                                  ? context.tr('requiredField')
                                  : null,
                            ),
                            const SizedBox(height: 20),
                            ElevatedButton.icon(
                              icon: const Icon(Icons.check),
                              label: Text(context.tr('receiveFinished')),
                              onPressed: (_isLoading ||
                                      _selectedOrder == null ||
                                      _selectedLocation == null)
                                  ? null
                                  : _submitReceipt,
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
              ],
            ),
    );
  }
}
