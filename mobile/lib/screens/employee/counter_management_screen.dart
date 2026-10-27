import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../l10n/app_localizations.dart';
import '../../models/employee_models.dart';
import '../../services/api_client.dart';
import '../../services/employee_service.dart';

class CounterManagementScreen extends StatefulWidget {
  const CounterManagementScreen({super.key});

  @override
  State<CounterManagementScreen> createState() => _CounterManagementScreenState();
}

class _CounterManagementScreenState extends State<CounterManagementScreen> {
  final _startReadingController = TextEditingController();
  final _rollReferenceController = TextEditingController();
  final _notesController = TextEditingController();

  List<EmployeeProductionOrder> _orders = [];
  EmployeeProductionOrder? _selectedOrder;
  int _selectedOperationId = 0;

  bool _isLoadingOrders = true;
  bool _isLoading = false;
  String? _message;
  bool _isError = false;

  @override
  void initState() {
    super.initState();
    _loadOrders();
  }

  @override
  void dispose() {
    _startReadingController.dispose();
    _rollReferenceController.dispose();
    _notesController.dispose();
    super.dispose();
  }

  Future<void> _loadOrders({bool keepSelection = false}) async {
    final previousId = keepSelection ? _selectedOrder?.id : null;
    setState(() {
      _isLoadingOrders = true;
      if (!keepSelection) _message = null;
    });
    final service = Provider.of<EmployeeService>(context, listen: false);
    try {
      final orders = await service.getProductionOrders();
      if (!mounted) return;
      EmployeeProductionOrder? selected;
      if (orders.isNotEmpty) {
        selected = orders.firstWhere(
          (o) => o.id == previousId,
          orElse: () => orders.first,
        );
      }
      setState(() {
        _orders = orders;
        _selectedOrder = selected;
        _selectedOperationId = 0;
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

  void _showFeedback(String message, {bool isError = false}) {
    if (!mounted) return;
    setState(() {
      _message = message;
      _isError = isError;
    });
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: isError ? Colors.red.shade700 : Colors.green.shade700,
      ),
    );
  }

  Future<void> _startInterval() async {
    final order = _selectedOrder;
    if (order == null) {
      _showFeedback(context.tr('selectOrder'), isError: true);
      return;
    }
    if (_startReadingController.text.trim().isEmpty) {
      _showFeedback(context.tr('requiredField'), isError: true);
      return;
    }

    setState(() => _isLoading = true);
    final service = Provider.of<EmployeeService>(context, listen: false);
    try {
      final res = await service.startCounter(
        orderId: order.id,
        unitId: order.plannedUnitId,
        startReading: _startReadingController.text.trim(),
        operationId: _selectedOperationId == 0 ? null : _selectedOperationId,
        rollReference: _rollReferenceController.text.trim(),
        notes: _notesController.text.trim(),
      );
      if (!mounted) return;
      _showFeedback('${context.tr('success')}: #${res['id']}');
      _startReadingController.clear();
      _rollReferenceController.clear();
      _notesController.clear();
      await _loadOrders(keepSelection: true);
    } on ApiException catch (e) {
      if (!mounted) return;
      _showFeedback(e.message, isError: true);
    } catch (e) {
      if (!mounted) return;
      _showFeedback('${context.tr('error')}: $e', isError: true);
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _closeCounter(EmployeeCounter counter) async {
    final formKey = GlobalKey<FormState>();
    final endController = TextEditingController();
    final rejectedController = TextEditingController();
    final values = await showDialog<Map<String, String>>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('${context.tr('closeCounter')} #${counter.id}'),
        content: Form(
          key: formKey,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextFormField(
                controller: endController,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                decoration: InputDecoration(
                  labelText: context.tr('endReading'),
                  suffixText: counter.unit,
                ),
                validator: (value) {
                  if (value == null || value.trim().isEmpty) {
                    return context.tr('requiredField');
                  }
                  final parsed = double.tryParse(value);
                  if (parsed == null || parsed < 0) {
                    return context.tr('positiveNumberRequired');
                  }
                  return null;
                },
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: rejectedController,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                decoration: InputDecoration(
                  labelText: context.tr('rejectedQuantity'),
                  suffixText: counter.unit,
                ),
                validator: (value) {
                  if (value == null || value.trim().isEmpty) return null;
                  final parsed = double.tryParse(value);
                  if (parsed == null || parsed < 0) {
                    return context.tr('positiveNumberRequired');
                  }
                  return null;
                },
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(),
            child: Text(context.tr('cancel')),
          ),
          ElevatedButton(
            onPressed: () {
              if (!formKey.currentState!.validate()) return;
              Navigator.of(dialogContext).pop({
                'end': endController.text.trim(),
                'rejected': rejectedController.text.trim(),
              });
            },
            child: Text(context.tr('submit')),
          ),
        ],
      ),
    );
    endController.dispose();
    rejectedController.dispose();
    if (values == null) return;
    if (!mounted) return;

    setState(() => _isLoading = true);
    final service = Provider.of<EmployeeService>(context, listen: false);
    try {
      await service.closeCounter(
        counterId: counter.id,
        endReading: values['end']!,
        rejectedQuantity: values['rejected']!.isEmpty ? null : values['rejected'],
      );
      if (!mounted) return;
      _showFeedback(context.tr('success'));
      await _loadOrders(keepSelection: true);
    } on ApiException catch (e) {
      if (!mounted) return;
      _showFeedback(e.message, isError: true);
    } catch (e) {
      if (!mounted) return;
      _showFeedback('${context.tr('error')}: $e', isError: true);
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _approveCounter(EmployeeCounter counter) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(context.tr('approve')),
        content: Text('#${counter.id} • ${counter.status}'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: Text(context.tr('cancel')),
          ),
          ElevatedButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: Text(context.tr('confirm')),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    if (!mounted) return;

    setState(() => _isLoading = true);
    final service = Provider.of<EmployeeService>(context, listen: false);
    try {
      await service.approveCounter(counter.id);
      if (!mounted) return;
      _showFeedback(context.tr('success'));
      await _loadOrders(keepSelection: true);
    } on ApiException catch (e) {
      if (!mounted) return;
      _showFeedback(e.message, isError: true);
    } catch (e) {
      if (!mounted) return;
      _showFeedback('${context.tr('error')}: $e', isError: true);
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _correctCounter(EmployeeCounter counter) async {
    final formKey = GlobalKey<FormState>();
    final reasonController = TextEditingController();
    final startController = TextEditingController(text: counter.startReading);
    final endController = TextEditingController(text: counter.endReading ?? '');
    final rejectedController = TextEditingController(text: counter.rejectedQuantity ?? '');
    final values = await showDialog<Map<String, String>>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('${context.tr('correctCounter')} #${counter.id}'),
        content: SingleChildScrollView(
          child: Form(
            key: formKey,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextFormField(
                  controller: reasonController,
                  decoration: InputDecoration(
                    labelText: context.tr('counterCorrectionReason'),
                  ),
                  validator: (value) => (value == null || value.trim().isEmpty)
                      ? context.tr('requiredField')
                      : null,
                ),
                const SizedBox(height: 12),
                TextFormField(
                  controller: startController,
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  decoration: InputDecoration(labelText: context.tr('startReading')),
                ),
                const SizedBox(height: 12),
                TextFormField(
                  controller: endController,
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  decoration: InputDecoration(labelText: context.tr('endReading')),
                ),
                const SizedBox(height: 12),
                TextFormField(
                  controller: rejectedController,
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  decoration: InputDecoration(labelText: context.tr('rejectedQuantity')),
                ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(),
            child: Text(context.tr('cancel')),
          ),
          ElevatedButton(
            onPressed: () {
              if (!formKey.currentState!.validate()) return;
              Navigator.of(dialogContext).pop({
                'reason': reasonController.text.trim(),
                'start': startController.text.trim(),
                'end': endController.text.trim(),
                'rejected': rejectedController.text.trim(),
              });
            },
            child: Text(context.tr('submit')),
          ),
        ],
      ),
    );
    reasonController.dispose();
    startController.dispose();
    endController.dispose();
    rejectedController.dispose();
    if (values == null) return;
    if (!mounted) return;

    setState(() => _isLoading = true);
    final service = Provider.of<EmployeeService>(context, listen: false);
    try {
      await service.correctCounter(
        counterId: counter.id,
        reason: values['reason']!,
        startReading: values['start']!.isEmpty ? null : values['start'],
        endReading: values['end']!.isEmpty ? null : values['end'],
        rejectedQuantity: values['rejected']!.isEmpty ? null : values['rejected'],
      );
      if (!mounted) return;
      _showFeedback(context.tr('success'));
      await _loadOrders(keepSelection: true);
    } on ApiException catch (e) {
      if (!mounted) return;
      _showFeedback(e.message, isError: true);
    } catch (e) {
      if (!mounted) return;
      _showFeedback('${context.tr('error')}: $e', isError: true);
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  String _counterStatusLabel(String status) {
    switch (status) {
      case 'PROVISIONAL':
        return context.tr('provisional');
      case 'COMPLETE':
        return context.tr('complete');
      case 'APPROVED':
        return context.tr('approved');
      case 'CORRECTED':
        return context.tr('corrected');
      default:
        return status;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(context.tr('counters')),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: () => _loadOrders(keepSelection: true),
          ),
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
                else ...[
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(16),
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
                                _selectedOperationId = 0;
                              });
                            },
                          ),
                          if (_selectedOrder != null) ...[
                            const SizedBox(height: 8),
                            Text(
                              '${context.tr('unit')}: ${_selectedOrder!.unit}',
                              style: const TextStyle(fontSize: 12, color: Colors.grey),
                            ),
                          ],
                          const SizedBox(height: 12),
                          if (_selectedOrder != null &&
                              _selectedOrder!.operations.isNotEmpty)
                            DropdownButtonFormField<int>(
                              initialValue: _selectedOperationId,
                              isExpanded: true,
                              decoration: InputDecoration(
                                labelText: context.tr('selectOperation'),
                              ),
                              items: [
                                DropdownMenuItem<int>(
                                  value: 0,
                                  child: Text(context.tr('none')),
                                ),
                                ..._selectedOrder!.operations.map(
                                  (operation) => DropdownMenuItem<int>(
                                    value: operation.id,
                                    child: Text(
                                      '${operation.sequence}. ${operation.name}',
                                      overflow: TextOverflow.ellipsis,
                                    ),
                                  ),
                                ),
                              ],
                              onChanged: (value) {
                                setState(() => _selectedOperationId = value ?? 0);
                              },
                            ),
                          const SizedBox(height: 12),
                          TextField(
                            controller: _startReadingController,
                            keyboardType: const TextInputType.numberWithOptions(decimal: true),
                            decoration: InputDecoration(
                              labelText: context.tr('startReading'),
                              suffixText: _selectedOrder?.unit,
                            ),
                          ),
                          const SizedBox(height: 12),
                          TextField(
                            controller: _rollReferenceController,
                            decoration: InputDecoration(
                              labelText: context.tr('rollReference'),
                            ),
                          ),
                          const SizedBox(height: 12),
                          TextField(
                            controller: _notesController,
                            decoration: InputDecoration(labelText: context.tr('notes')),
                          ),
                          const SizedBox(height: 16),
                          ElevatedButton.icon(
                            icon: const Icon(Icons.play_arrow),
                            label: Text(context.tr('startCounter')),
                            onPressed: _isLoading ? null : _startInterval,
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  Text(
                    context.tr('counters'),
                    style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 8),
                  if (_selectedOrder == null || _selectedOrder!.counters.isEmpty)
                    Card(
                      child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Text(context.tr('noData')),
                      ),
                    )
                  else
                    ..._selectedOrder!.counters.map(
                      (counter) => Card(
                        child: Padding(
                          padding: const EdgeInsets.all(12),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Row(
                                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                                children: [
                                  Text(
                                    '#${counter.id} • ${counter.unit}',
                                    style: const TextStyle(fontWeight: FontWeight.bold),
                                  ),
                                  Chip(
                                    label: Text(
                                      _counterStatusLabel(counter.status),
                                      style: const TextStyle(fontSize: 11),
                                    ),
                                  ),
                                ],
                              ),
                              const SizedBox(height: 4),
                              Text(
                                '${context.tr('startReading')}: ${counter.startReading}',
                                style: const TextStyle(fontSize: 12),
                              ),
                              if (counter.endReading != null)
                                Text(
                                  '${context.tr('endReading')}: ${counter.endReading}',
                                  style: const TextStyle(fontSize: 12),
                                ),
                              if (counter.counterOutput != null)
                                Text(
                                  '${context.tr('counterOutput')}: ${counter.counterOutput}',
                                  style: const TextStyle(fontSize: 12),
                                ),
                              if (counter.rejectedQuantity != null)
                                Text(
                                  '${context.tr('rejectedQuantity')}: ${counter.rejectedQuantity}',
                                  style: const TextStyle(fontSize: 12),
                                ),
                              const SizedBox(height: 8),
                              Wrap(
                                spacing: 8,
                                children: [
                                  if (counter.isProvisional)
                                    OutlinedButton(
                                      onPressed: _isLoading
                                          ? null
                                          : () => _closeCounter(counter),
                                      child: Text(context.tr('close')),
                                    ),
                                  if (counter.isComplete || counter.isCorrected)
                                    OutlinedButton(
                                      onPressed: _isLoading
                                          ? null
                                          : () => _approveCounter(counter),
                                      child: Text(context.tr('approve')),
                                    ),
                                  OutlinedButton(
                                    onPressed: _isLoading
                                        ? null
                                        : () => _correctCounter(counter),
                                    child: Text(context.tr('correct')),
                                  ),
                                ],
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                ],
              ],
            ),
    );
  }
}
