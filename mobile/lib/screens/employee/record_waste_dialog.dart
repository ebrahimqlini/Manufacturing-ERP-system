import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';
import '../../models/employee_models.dart';
import '../../services/api_client.dart';
import '../../services/employee_service.dart';

class RecordWasteDialog extends StatefulWidget {
  final int resultId;
  final List<EmployeeRequirement> materials;
  final EmployeeService employeeService;

  const RecordWasteDialog({
    super.key,
    required this.resultId,
    required this.materials,
    required this.employeeService,
  });

  @override
  State<RecordWasteDialog> createState() => _RecordWasteDialogState();
}

class _RecordWasteDialogState extends State<RecordWasteDialog> {
  final _formKey = GlobalKey<FormState>();
  int? _selectedMaterialId;
  final _qtyBaseController = TextEditingController();
  final _rollRefController = TextEditingController();
  bool _isLoading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    if (widget.materials.isNotEmpty) {
      _selectedMaterialId = widget.materials.first.materialId;
    }
  }

  @override
  void dispose() {
    _qtyBaseController.dispose();
    _rollRefController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate() || _selectedMaterialId == null) return;

    setState(() {
      _isLoading = true;
      _error = null;
    });

    try {
      await widget.employeeService.recordWaste(
        resultId: widget.resultId,
        materialId: _selectedMaterialId!,
        quantityBase: _qtyBaseController.text.trim(),
        rollReference: _rollRefController.text.trim(),
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
      title: Text(context.tr('recordWaste')),
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
              DropdownButtonFormField<int>(
                initialValue: _selectedMaterialId,
                decoration: InputDecoration(
                  labelText: context.tr('damagedMaterial'),
                ),
                items: widget.materials.map((m) {
                  return DropdownMenuItem<int>(
                    value: m.materialId,
                    child: Text('${m.material} (${m.unit})'),
                  );
                }).toList(),
                onChanged: (val) {
                  setState(() {
                    _selectedMaterialId = val;
                  });
                },
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _qtyBaseController,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                decoration: InputDecoration(
                  labelText: context.tr('measuredBaseQuantity'),
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
                controller: _rollRefController,
                decoration: InputDecoration(
                  labelText: context.tr('rollReference'),
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
