import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../l10n/app_localizations.dart';
import '../../models/employee_models.dart';
import '../../services/api_client.dart';
import '../../services/employee_service.dart';
import 'record_progress_dialog.dart';
import 'record_result_dialog.dart';
import 'record_waste_dialog.dart';

class OrderDetailScreen extends StatefulWidget {
  final int orderId;

  const OrderDetailScreen({super.key, required this.orderId});

  @override
  State<OrderDetailScreen> createState() => _OrderDetailScreenState();
}

class _OrderDetailScreenState extends State<OrderDetailScreen> {
  EmployeeProductionOrder? _order;
  bool _isLoading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _fetchDetail();
  }

  Future<void> _fetchDetail() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });

    final service = Provider.of<EmployeeService>(context, listen: false);
    try {
      final order = await service.getProductionOrderDetail(widget.orderId);
      setState(() {
        _order = order;
        _isLoading = false;
      });
    } on ApiException catch (e) {
      setState(() {
        _error = e.message;
        _isLoading = false;
      });
    } catch (e) {
      setState(() {
        _error = '${context.tr('error')}: $e';
        _isLoading = false;
      });
    }
  }

  Future<void> _promptReasonAndReverseResult(int resultId) async {
    final reasonController = TextEditingController();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(context.tr('reverseResult')),
        content: TextField(
          controller: reasonController,
          decoration: InputDecoration(labelText: context.tr('reason')),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(context.tr('cancel'))),
          ElevatedButton(
            style: ElevatedButton.styleFrom(backgroundColor: Colors.red),
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(context.tr('reverse')),
          ),
        ],
      ),
    );

    if (!mounted) return;

    if (confirmed == true && reasonController.text.trim().isNotEmpty) {
      final service = Provider.of<EmployeeService>(context, listen: false);
      try {
        await service.reverseResult(resultId: resultId, reason: reasonController.text.trim());
        _fetchDetail();
      } on ApiException catch (e) {
        _showError(e.message);
      }
    }
  }

  Future<void> _confirmZeroWaste(int resultId) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(context.tr('confirmZeroWaste')),
        content: Text(context.tr('confirmZeroWasteMessage')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(context.tr('cancel'))),
          ElevatedButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(context.tr('confirm')),
          ),
        ],
      ),
    );

    if (!mounted) return;

    if (confirmed == true) {
      final service = Provider.of<EmployeeService>(context, listen: false);
      try {
        await service.confirmZeroWaste(resultId);
        _fetchDetail();
      } on ApiException catch (e) {
        _showError(e.message);
      }
    }
  }

  void _showError(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(msg), backgroundColor: Colors.red.shade700),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (_isLoading) {
      return Scaffold(
        appBar: AppBar(title: Text(context.tr('productionOrders'))),
        body: const Center(child: CircularProgressIndicator()),
      );
    }

    if (_error != null || _order == null) {
      return Scaffold(
        appBar: AppBar(title: Text(context.tr('productionOrders'))),
        body: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text(_error ?? context.tr('noData')),
              const SizedBox(height: 12),
              ElevatedButton(onPressed: _fetchDetail, child: Text(context.tr('refresh'))),
            ],
          ),
        ),
      );
    }

    final order = _order!;
    final employeeService = Provider.of<EmployeeService>(context, listen: false);

    return Scaffold(
      appBar: AppBar(
        title: Text(order.orderNumber),
        actions: [
          IconButton(icon: const Icon(Icons.refresh), onPressed: _fetchDetail),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(12),
        children: [
          // Order Header
          Card(
            color: Colors.blue.shade50,
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text(
                        order.product,
                        style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Color(0xFF1E3A8A)),
                      ),
                      Chip(
                        label: Text(order.status, style: const TextStyle(fontSize: 12, color: Colors.white)),
                        backgroundColor: const Color(0xFF1E3A8A),
                      ),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Text('${context.tr('productionLine')}: ${order.productionLine}'),
                  Text(
                    '${context.tr('plannedQuantity')}: '
                    '${order.plannedQuantity} ${order.unit} '
                    '(${order.plannedQuantityBase} ${context.tr('base')})',
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),

          // Operations Section
          Text(context.tr('operations'), style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
          const SizedBox(height: 6),
          ...order.operations.map((op) {
            return Card(
              child: ListTile(
                title: Text('${op.sequence}. ${op.name} ${op.isOptional ? "(${context.tr('skipOperation')})" : ""}'),
                subtitle: Text('${context.tr('status')}: ${op.status}'),
                trailing: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    IconButton(
                      icon: const Icon(Icons.add_task, color: Colors.blue),
                      tooltip: context.tr('recordProgress'),
                      onPressed: () async {
                        final res = await showDialog<bool>(
                          context: context,
                          builder: (_) => RecordProgressDialog(
                            order: order,
                            operation: op,
                            employeeService: employeeService,
                          ),
                        );
                        if (res == true) _fetchDetail();
                      },
                    ),
                    if (!op.isCompleted)
                      IconButton(
                        icon: const Icon(Icons.check_circle_outline, color: Colors.green),
                        tooltip: context.tr('completeOperation'),
                        onPressed: () async {
                          try {
                            await employeeService.completeOperation(
                              orderId: order.id,
                              operationId: op.id,
                            );
                            _fetchDetail();
                          } on ApiException catch (e) {
                            _showError(e.message);
                          }
                        },
                      ),
                  ],
                ),
              ),
            );
          }),
          const SizedBox(height: 16),

          // Material Requirements Section
          Text(context.tr('requirements'), style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
          const SizedBox(height: 6),
          ...order.materials.map((mat) {
            return Card(
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${mat.material} (${mat.unit})',
                      style: const TextStyle(fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      '${context.tr('planned')}: ${mat.plannedBase} | '
                      '${context.tr('issued')}: ${mat.issuedBase} | '
                      '${context.tr('remaining')}: ${mat.remainingBase}',
                    ),
                    if (double.tryParse(mat.shortageBase) != null && double.parse(mat.shortageBase) > 0)
                      Text(
                        '${context.tr('availableShortage')}: ${mat.shortageBase}',
                        style: const TextStyle(color: Colors.red, fontWeight: FontWeight.bold, fontSize: 12),
                      ),
                  ],
                ),
              ),
            );
          }),
          const SizedBox(height: 16),

          // Production Results Section
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(context.tr('recordResult'), style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
              ElevatedButton.icon(
                icon: const Icon(Icons.add, size: 16),
                label: Text(context.tr('recordResult')),
                onPressed: () async {
                  final res = await showDialog<bool>(
                    context: context,
                    builder: (_) => RecordResultDialog(
                      order: order,
                      employeeService: employeeService,
                    ),
                  );
                  if (res == true) _fetchDetail();
                },
              ),
            ],
          ),
          const SizedBox(height: 6),
          if (order.results.isEmpty)
            Card(child: Padding(padding: const EdgeInsets.all(12), child: Text(context.tr('noData'))))
          else
            ...order.results.map((res) {
              return Card(
                color: res.reversed ? Colors.grey.shade100 : Colors.white,
                child: ListTile(
                  title: Text(
                    '${context.tr('good')}: ${res.goodQuantity} ${res.unit} | '
                    '${context.tr('scrap')}: ${res.scrapQuantity}',
                    style: TextStyle(
                      fontWeight: FontWeight.bold,
                      decoration: res.reversed ? TextDecoration.lineThrough : null,
                    ),
                  ),
                  subtitle: Text(
                    '${context.tr('postedAt')}: ${res.postedAt ?? "-"} '
                    '${res.reversed ? "(${context.tr('reversed')})" : ""}',
                  ),
                  trailing: res.reversed
                      ? Chip(
                          label: Text(
                            context.tr('reversed'),
                            style: const TextStyle(fontSize: 10),
                          ),
                        )
                      : PopupMenuButton<String>(
                          onSelected: (val) {
                            if (val == 'reverse') {
                              _promptReasonAndReverseResult(res.id);
                            } else if (val == 'waste') {
                              showDialog<bool>(
                                context: context,
                                builder: (_) => RecordWasteDialog(
                                  resultId: res.id,
                                  materials: order.materials,
                                  employeeService: employeeService,
                                ),
                              ).then((v) {
                                if (v == true) _fetchDetail();
                              });
                            } else if (val == 'zero_waste') {
                              _confirmZeroWaste(res.id);
                            }
                          },
                          itemBuilder: (_) => [
                            PopupMenuItem(
                              value: 'waste',
                              child: Text(context.tr('recordWaste')),
                            ),
                            PopupMenuItem(
                              value: 'zero_waste',
                              child: Text(context.tr('confirmZeroWaste')),
                            ),
                            PopupMenuItem(
                              value: 'reverse',
                              child: Text(context.tr('reverseResult'), style: const TextStyle(color: Colors.red)),
                            ),
                          ],
                        ),
                ),
              );
            }),
          const SizedBox(height: 16),

          // Measured Waste Section
          Text(context.tr('materialWaste'), style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
          const SizedBox(height: 6),
          if (order.materialWaste.isEmpty)
            Card(child: Padding(padding: const EdgeInsets.all(12), child: Text(context.tr('noData'))))
          else
            ...order.materialWaste.map((w) {
              return Card(
                child: ListTile(
                  leading: const Icon(Icons.delete_outline, color: Colors.brown),
                  title: Text(
                    '${context.tr('measuredDamagedWeight')}: '
                    '${w.quantityBase} ${context.tr('baseUnit')}',
                  ),
                  subtitle: Text(
                    '${context.tr('resultNumber')} #${w.resultId ?? "-"} | '
                    '${context.tr('postedAt')}: ${w.postedAt ?? "-"}',
                  ),
                  trailing: w.reversed
                      ? Chip(
                          label: Text(
                            context.tr('reversed'),
                            style: const TextStyle(fontSize: 10),
                          ),
                        )
                      : IconButton(
                          icon: const Icon(Icons.undo, color: Colors.red),
                          tooltip: context.tr('reverseWaste'),
                          onPressed: () async {
                            final reasonController = TextEditingController();
                            final ok = await showDialog<bool>(
                              context: context,
                              builder: (ctx) => AlertDialog(
                                title: Text(context.tr('reverseWaste')),
                                content: TextField(
                                  controller: reasonController,
                                  decoration: InputDecoration(
                                    labelText: context.tr('reason'),
                                  ),
                                ),
                                actions: [
                                  TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(context.tr('cancel'))),
                                  ElevatedButton(onPressed: () => Navigator.pop(ctx, true), child: Text(context.tr('reverse'))),
                                ],
                              ),
                            );
                            if (ok == true && reasonController.text.trim().isNotEmpty) {
                              try {
                                await employeeService.reverseWaste(wasteId: w.id, reason: reasonController.text.trim());
                                _fetchDetail();
                              } on ApiException catch (e) {
                                _showError(e.message);
                              }
                            }
                          },
                        ),
                ),
              );
            }),
        ],
      ),
    );
  }
}
