import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../l10n/app_localizations.dart';
import '../../models/customer_models.dart';
import '../../services/api_client.dart';
import '../../services/customer_service.dart';

class CreateOrderRequestScreen extends StatefulWidget {
  const CreateOrderRequestScreen({super.key});

  @override
  State<CreateOrderRequestScreen> createState() => _CreateOrderRequestScreenState();
}

class _CreateOrderRequestScreenState extends State<CreateOrderRequestScreen> {
  final _formKey = GlobalKey<FormState>();
  final _refController = TextEditingController();
  final _deliveryDateController = TextEditingController();
  final _notesController = TextEditingController();
  final _qtyController = TextEditingController();

  List<CustomerProduct> _products = [];
  CustomerProduct? _selectedProduct;
  CustomerUnit? _selectedUnit;

  bool _isLoadingProducts = true;
  bool _isLoading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadProducts();
  }

  @override
  void dispose() {
    _refController.dispose();
    _deliveryDateController.dispose();
    _notesController.dispose();
    _qtyController.dispose();
    super.dispose();
  }

  Future<void> _loadProducts() async {
    setState(() {
      _isLoadingProducts = true;
      _error = null;
    });
    final service = Provider.of<CustomerService>(context, listen: false);
    try {
      final products = await service.getProducts();
      if (!mounted) return;
      final selected = products.isEmpty ? null : products.first;
      setState(() {
        _products = products;
        _selectedProduct = selected;
        _selectedUnit = _baseUnitFor(selected);
        _isLoadingProducts = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.message;
        _isLoadingProducts = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '${context.tr('error')}: $e';
        _isLoadingProducts = false;
      });
    }
  }

  CustomerUnit? _baseUnitFor(CustomerProduct? product) {
    if (product == null || product.units.isEmpty) return null;
    for (final unit in product.units) {
      if (unit.isBase) return unit;
    }
    return product.units.first;
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    final product = _selectedProduct;
    final unit = _selectedUnit;
    if (product == null || unit == null) {
      setState(() => _error = context.tr('selectProduct'));
      return;
    }

    setState(() {
      _isLoading = true;
      _error = null;
    });

    final service = Provider.of<CustomerService>(context, listen: false);

    try {
      final line = CustomerOrderRequestLine(
        product: product.id,
        quantity: _qtyController.text.trim(),
        unit: unit.id,
      );

      await service.submitOrderRequest(
        customerReference: _refController.text.trim(),
        requestedDeliveryDate: _deliveryDateController.text.trim(),
        notes: _notesController.text.trim(),
        lines: [line],
      );

      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(context.tr('success')),
          backgroundColor: Colors.green.shade700,
        ),
      );
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
    return Scaffold(
      appBar: AppBar(
        title: Text(context.tr('createOrderRequest')),
      ),
      body: _isLoadingProducts
          ? const Center(child: CircularProgressIndicator())
          : SingleChildScrollView(
              padding: const EdgeInsets.all(16),
              child: Form(
                key: _formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    if (_error != null)
                      Container(
                        margin: const EdgeInsets.only(bottom: 16),
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: Colors.red.shade50,
                          border: Border.all(color: Colors.red.shade300),
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: Text(
                          _error!,
                          style: TextStyle(color: Colors.red.shade800),
                        ),
                      ),
                    if (_products.isEmpty)
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
                          child: Column(
                            children: [
                              DropdownButtonFormField<int>(
                                initialValue: _selectedProduct?.id,
                                isExpanded: true,
                                decoration: InputDecoration(
                                  labelText: context.tr('selectProduct'),
                                ),
                                items: _products
                                    .map(
                                      (product) => DropdownMenuItem<int>(
                                        value: product.id,
                                        child: Text(
                                          '${product.sku} - ${product.nameAr}',
                                          overflow: TextOverflow.ellipsis,
                                        ),
                                      ),
                                    )
                                    .toList(),
                                onChanged: (value) {
                                  final product = _products.firstWhere(
                                    (p) => p.id == value,
                                    orElse: () => _products.first,
                                  );
                                  setState(() {
                                    _selectedProduct = product;
                                    _selectedUnit = _baseUnitFor(product);
                                  });
                                },
                                validator: (value) => value == null
                                    ? context.tr('requiredField')
                                    : null,
                              ),
                              const SizedBox(height: 12),
                              if (_selectedProduct != null &&
                                  _selectedProduct!.units.isNotEmpty)
                                DropdownButtonFormField<int>(
                                  initialValue: _selectedUnit?.id,
                                  isExpanded: true,
                                  decoration: InputDecoration(
                                    labelText: context.tr('selectUnit'),
                                  ),
                                  items: _selectedProduct!.units
                                      .map(
                                        (unit) => DropdownMenuItem<int>(
                                          value: unit.id,
                                          child: Text(unit.nameAr),
                                        ),
                                      )
                                      .toList(),
                                  onChanged: (value) {
                                    setState(() {
                                      _selectedUnit = _selectedProduct!.units
                                          .firstWhere((u) => u.id == value);
                                    });
                                  },
                                  validator: (value) => value == null
                                      ? context.tr('requiredField')
                                      : null,
                                ),
                              const SizedBox(height: 12),
                              TextFormField(
                                controller: _qtyController,
                                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                                decoration: InputDecoration(
                                  labelText: context.tr('quantity'),
                                  suffixText: _selectedUnit?.nameAr,
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
                                controller: _deliveryDateController,
                                decoration: InputDecoration(
                                  labelText: context.tr('requestedDeliveryDate'),
                                  hintText: 'YYYY-MM-DD',
                                ),
                                validator: (v) {
                                  if (v == null || v.trim().isEmpty) return null;
                                  return DateTime.tryParse(v.trim()) == null
                                      ? context.tr('invalidDate')
                                      : null;
                                },
                              ),
                              const SizedBox(height: 12),
                              TextFormField(
                                controller: _refController,
                                decoration: InputDecoration(
                                  labelText: context.tr('customerReference'),
                                ),
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
                    const SizedBox(height: 20),
                    ElevatedButton.icon(
                      icon: const Icon(Icons.send),
                      label: Text(context.tr('submit')),
                      onPressed: (_isLoading ||
                              _selectedProduct == null ||
                              _selectedUnit == null)
                          ? null
                          : _submit,
                    ),
                  ],
                ),
              ),
            ),
    );
  }
}
