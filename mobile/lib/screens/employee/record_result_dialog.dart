import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';
import '../../models/employee_models.dart';
import '../../services/api_client.dart';
import '../../services/employee_service.dart';

class RecordResultDialog extends StatefulWidget {
  final EmployeeProductionOrder order;
  final EmployeeService employeeService;

  const RecordResultDialog({
    super.key,
    required this.order,
    required this.employeeService,
  });

  @override
  State<RecordResultDialog> createState() => _RecordResultDialogState();
}

class _RecordResultDialogState extends State<RecordResultDialog> {
  final _formKey = GlobalKey<FormState>();
  final _goodQtyController = TextEditingController();
  final _scrapQtyController = TextEditingController(text: '0');
  final _notesController = TextEditingController();
  bool _isLoading = false;
  String? _error;

  @override
  void dispose() {
    _goodQtyController.dispose();
    _scrapQtyController.dispose();
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
      await widget.employeeService.recordResult(
        orderId: widget.order.id,
        goodQuantity: _goodQtyController.text.trim(),
        scrapQuantity: _scrapQtyController.text.trim(),
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
      title: Text(context.tr('recordResult')),
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
                controller: _goodQtyController,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                decoration: InputDecoration(
                  labelText: context.tr('goodQuantity'),
                ),
                validator: (v) {
                  if (v == null || v.trim().isEmpty) {
                    return context.tr('requiredField');
                  }
                  final val = double.tryParse(v);
                  if (val == null || val < 0) {
                    return context.tr('invalidValue');
                  }
                  return null;
                },
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _scrapQtyController,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                decoration: InputDecoration(
                  labelText: context.tr('scrapQuantity'),
                ),
                validator: (v) {
                  if (v == null || v.trim().isEmpty) {
                    return context.tr('requiredField');
                  }
                  final val = double.tryParse(v);
                  if (val == null || val < 0) {
                    return context.tr('invalidValue');
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
