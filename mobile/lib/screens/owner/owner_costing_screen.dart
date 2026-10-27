import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../l10n/app_localizations.dart';
import '../../models/owner_models.dart';
import '../../services/owner_service.dart';

class OwnerCostingScreen extends StatefulWidget {
  const OwnerCostingScreen({super.key});

  @override
  State<OwnerCostingScreen> createState() => _OwnerCostingScreenState();
}

class _OwnerCostingScreenState extends State<OwnerCostingScreen> {
  List<OwnerCostingPeriodItem> _periods = [];
  bool _isLoading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _fetchCosting();
  }

  Future<void> _fetchCosting() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });

    final service = Provider.of<OwnerService>(context, listen: false);

    try {
      final list = await service.getCostingPeriods();
      setState(() {
        _periods = list;
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
        title: Text(context.tr('costingOverview')),
        actions: [
          IconButton(icon: const Icon(Icons.refresh), onPressed: _fetchCosting),
        ],
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : _periods.isEmpty
                  ? Center(child: Text(context.tr('noData')))
                  : ListView.builder(
                      padding: const EdgeInsets.all(12),
                      itemCount: _periods.length,
                      itemBuilder: (context, index) {
                        final p = _periods[index];
                        return Card(
                          child: Padding(
                            padding: const EdgeInsets.all(14),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Row(
                                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                                  children: [
                                    Text(
                                      '${p.month} / ${p.year}',
                                      style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                                    ),
                                    Chip(
                                      label: Text(p.status, style: const TextStyle(fontSize: 11)),
                                      backgroundColor: p.status == 'FINALIZED' ? Colors.green.shade100 : Colors.amber.shade100,
                                    ),
                                  ],
                                ),
                                const Divider(height: 16),
                                Text('${context.tr('totalProductionCost')}: ${p.totalProductionCost}'),
                                Text('${context.tr('materialsTotal')}: ${p.materialsTotal}'),
                                Text('${context.tr('laborTotal')}: ${p.laborTotal}'),
                                Text('${context.tr('electricityTotal')}: ${p.electricityTotal}'),
                                Text('${context.tr('waterTotal')}: ${p.waterTotal}'),
                                Text('${context.tr('otherIndustrialTotal')}: ${p.otherIndustrialTotal}'),
                                Text('${context.tr('entryCount')}: ${p.entryCount}'),
                                if (p.reviewRequired)
                                  Padding(
                                    padding: const EdgeInsets.only(top: 8),
                                    child: Text(
                                      context.tr('reviewRequired'),
                                      style: TextStyle(color: Colors.orange.shade800, fontSize: 12),
                                    ),
                                  ),
                                if (!p.isComplete && p.missingDataNotes.isNotEmpty)
                                  Padding(
                                    padding: const EdgeInsets.only(top: 8),
                                    child: Text(
                                      '${context.tr('missingDataNotes')}: ${p.missingDataNotes.join(", ")}',
                                      style: TextStyle(color: Colors.red.shade700, fontSize: 11),
                                    ),
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
