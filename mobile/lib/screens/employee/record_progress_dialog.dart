import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';
import '../../models/employee_models.dart';
import '../../services/api_client.dart';
import '../../services/employee_service.dart';

class RecordProgressDialog extends StatefulWidget {
  final EmployeeProductionOrder order;
  final EmployeeOperation operation;
  final EmployeeService employeeService;

  const RecordProgressDialog({
    super.key,
    required this.order,
    required this.operation,
    required this.employeeService,
  });

  @override
  State<RecordProgressDialog> createState() => _RecordProgressDialogState();
}

class _RecordProgressDialogState extends State<RecordProgressDialog> {
  final _formKey = GlobalKey<FormState>();
  final _qtyController = TextEditingController();
  final _notesController = TextEditingController();
  bool _isLoading = false;
  String? _error;

  @override
  void dispose() {
    _qtyController.dispose();
    _notesController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;

    setState(() {
      _isLoading = true;
      _error = null;
    });

    try {
      await widget.employeeService.recordProgress(
        orderId: widget.order.id,
        operationId: widget.operation.id,
        quantity: _qtyController.text.trim(),
        unitId: widget.order.plannedUnitId,
        notes: _notesController.text.trim(),
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
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

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text('${context.tr('recordProgress')} - ${widget.operation.name}'),
      content: SingleChildScrollView(
        child: Form(
          key: _formKey,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (_error != null)
                Container(
                  margin: const EdgeInsets.only(bottom: 12),
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: Colors.red.shade50,
                    border: Border.all(color: Colors.red.shade300),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: Text(_error!, style: TextStyle(color: Colors.red.shade800, fontSize: 12)),
                ),
              TextFormField(
                controller: _qtyController,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                decoration: InputDecoration(
                  labelText: context.tr('quantity'),
                  hintText: '50.0',
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
                controller: _notesController,
                decoration: InputDecoration(
                  labelText: context.tr('notes'),
                ),
              ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: _isLoading ? null : () => Navigator.of(context).pop(false),
          child: Text(context.tr('cancel')),
        ),
        ElevatedButton(
          onPressed: _isLoading ? null : _submit,
          child: _isLoading
              ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
              : Text(context.tr('submit')),
        ),
      ],
    );
  }
}
