import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../l10n/app_localizations.dart';
import '../../models/owner_models.dart';
import '../../services/owner_service.dart';

class OwnerInventoryScreen extends StatefulWidget {
  const OwnerInventoryScreen({super.key});

  @override
  State<OwnerInventoryScreen> createState() => _OwnerInventoryScreenState();
}

class _OwnerInventoryScreenState extends State<OwnerInventoryScreen> {
  List<OwnerStockRowItem> _rows = [];
  bool _isLoading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _fetchStock();
  }

  Future<void> _fetchStock() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });

    final service = Provider.of<OwnerService>(context, listen: false);

    try {
      final list = await service.getStockRows();
      setState(() {
        _rows = list;
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
        title: Text(context.tr('inventoryOverview')),
        actions: [
          IconButton(icon: const Icon(Icons.refresh), onPressed: _fetchStock),
        ],
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : _rows.isEmpty
                  ? Center(child: Text(context.tr('noData')))
                  : ListView.builder(
                      padding: const EdgeInsets.all(12),
                      itemCount: _rows.length,
                      itemBuilder: (context, index) {
                        final item = _rows[index];
                        return Card(
                          child: ListTile(
                            leading: Icon(
                              Icons.inventory,
                              color: item.belowThreshold
                                  ? Colors.red.shade700
                                  : const Color(0xFF1E3A8A),
                            ),
                            title: Text('${item.productSku} - ${item.productName}'),
                            subtitle: Text('${context.tr('category')}: ${item.category}'),
                            trailing: Column(
                              mainAxisAlignment: MainAxisAlignment.center,
                              crossAxisAlignment: CrossAxisAlignment.end,
                              children: [
                                Text(
                                  '${item.balance} ${item.baseUnit}',
                                  style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13),
                                ),
                                if (item.belowThreshold)
                                  Text(
                                    context.tr('lowStockAlerts'),
                                    style: TextStyle(fontSize: 10, color: Colors.red.shade700),
                                  ),
                              ],
                            ),
                          ),
                        );
                      },
                    ),
    );
  }
}
