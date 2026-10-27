import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../l10n/app_localizations.dart';
import '../../models/employee_models.dart';
import '../../services/api_client.dart';
import '../../services/employee_service.dart';

class MaterialIssueScreen extends StatefulWidget {
  const MaterialIssueScreen({super.key});

  @override
  State<MaterialIssueScreen> createState() => _MaterialIssueScreenState();
}

class _MaterialIssueScreenState extends State<MaterialIssueScreen> {
  final _formKey = GlobalKey<FormState>();
  final _quantityController = TextEditingController();
  final _refController = TextEditingController();

  List<EmployeeProductionOrder> _orders = [];
  EmployeeProductionOrder? _selectedOrder;
  EmployeeRequirement? _selectedRequirement;

  bool _isLoadingOrders = true;
  bool _isLoading = false;
  String? _message;
  bool _isError = false;

  @override
  void initState() {
    super.initState();
    _refController.text =
        'ISSUE-${DateTime.now().millisecondsSinceEpoch.toString().substring(6)}';
    _loadOrders();
  }

  @override
  void dispose() {
    _quantityController.dispose();
    _refController.dispose();
    super.dispose();
  }

  Future<void> _loadOrders() async {
    setState(() {
      _isLoadingOrders = true;
      _message = null;
    });
    final service = Provider.of<EmployeeService>(context, listen: false);
    try {
      final orders = await service.getProductionOrders();
      if (!mounted) return;
      setState(() {
        _orders = orders;
        _selectedOrder = orders.isEmpty ? null : orders.first;
        _selectedRequirement = _selectedOrder?.materials.isNotEmpty == true
            ? _selectedOrder!.materials.first
            : null;
        _isLoadingOrders = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _isError = true;
        _message = e.message;
        _isLoadingOrders = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isError = true;
        _message = '${context.tr('error')}: $e';
        _isLoadingOrders = false;
      });
    }
  }

  Future<void> _submitIssue() async {
    if (!_formKey.currentState!.validate()) return;
    final order = _selectedOrder;
    final requirement = _selectedRequirement;
    if (order == null || requirement == null) return;

    setState(() {
      _isLoading = true;
      _message = null;
    });

    final service = Provider.of<EmployeeService>(context, listen: false);

    try {
      final res = await service.issueMaterial(
        orderId: order.id,
        requirementId: requirement.requirementId,
        quantity: _quantityController.text.trim(),
        unitId: requirement.unitId,
        reference: _refController.text.trim(),
      );
      if (!mounted) return;
      setState(() {
        _message = '${context.tr('success')}: ${res['id']}';
        _isError = false;
      });
      _quantityController.clear();
      _refController.text =
          'ISSUE-${DateTime.now().millisecondsSinceEpoch.toString().substring(6)}';
      await _loadOrders();
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
        title: Text(context.tr('issueMaterial')),
        actions: [
          IconButton(icon: const Icon(Icons.refresh), onPressed: _loadOrders),
        ],
      ),
      body: _isLoadingOrders
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
                if (_orders.isEmpty)
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
                                final order = _orders.firstWhere(
                                  (o) => o.id == value,
                                  orElse: () => _orders.first,
                                );
                                setState(() {
                                  _selectedOrder = order;
                                  _selectedRequirement = order.materials.isNotEmpty
                                      ? order.materials.first
                                      : null;
                                });
                              },
                              validator: (value) => value == null
                                  ? context.tr('requiredField')
                                  : null,
                            ),
                            const SizedBox(height: 12),
                            if (_selectedOrder != null &&
                                _selectedOrder!.materials.isNotEmpty)
                              DropdownButtonFormField<int>(
                                initialValue: _selectedRequirement?.requirementId,
                                isExpanded: true,
                                decoration: InputDecoration(
                                  labelText: context.tr('selectRequirement'),
                                ),
                                items: _selectedOrder!.materials
                                    .map(
                                      (req) => DropdownMenuItem<int>(
                                        value: req.requirementId,
                                        child: Text(
                                          '${req.material} (${req.unit})',
                                          overflow: TextOverflow.ellipsis,
                                        ),
                                      ),
                                    )
                                    .toList(),
                                onChanged: (value) {
                                  setState(() {
                                    _selectedRequirement = _selectedOrder!.materials
                                        .firstWhere((r) => r.requirementId == value);
                                  });
                                },
                                validator: (value) => value == null
                                    ? context.tr('requiredField')
                                    : null,
                              )
                            else
                              Text(
                                context.tr('noData'),
                                style: const TextStyle(color: Colors.orange),
                              ),
                            if (_selectedRequirement != null) ...[
                              const SizedBox(height: 8),
                              Text(
                                '${context.tr('remainingBase')}: '
                                '${_selectedRequirement!.remainingBase} '
                                '${_selectedRequirement!.unit}',
                                style: const TextStyle(fontSize: 12, color: Colors.grey),
                              ),
                            ],
                            const SizedBox(height: 12),
                            TextFormField(
                              controller: _quantityController,
                              keyboardType: const TextInputType.numberWithOptions(decimal: true),
                              decoration: InputDecoration(
                                labelText: context.tr('quantity'),
                                suffixText: _selectedRequirement?.unit,
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
                              label: Text(context.tr('submit')),
                              onPressed: (_isLoading ||
                                      _selectedOrder == null ||
                                      _selectedRequirement == null)
                                  ? null
                                  : _submitIssue,
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
