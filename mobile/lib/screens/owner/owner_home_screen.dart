import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../l10n/app_localizations.dart';
import '../../models/owner_models.dart';
import '../../providers/auth_provider.dart';
import '../../providers/locale_provider.dart';
import '../../services/owner_service.dart';
import '../settings_screen.dart';
import 'owner_costing_screen.dart';
import 'owner_inventory_screen.dart';

class OwnerHomeScreen extends StatefulWidget {
  const OwnerHomeScreen({super.key});

  @override
  State<OwnerHomeScreen> createState() => _OwnerHomeScreenState();
}

class _OwnerHomeScreenState extends State<OwnerHomeScreen> {
  OwnerDashboardSummary? _summary;
  bool _isLoading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _fetchDashboard();
  }

  Future<void> _fetchDashboard() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });

    final service = Provider.of<OwnerService>(context, listen: false);

    try {
      final summary = await service.getDashboardSummary();
      setState(() {
        _summary = summary;
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
    final auth = Provider.of<AuthProvider>(context);
    final locale = Provider.of<LocaleProvider>(context);

    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(context.tr('appName'), style: const TextStyle(fontSize: 16)),
            Text(
              '${context.tr('roleOwner')} - ${auth.currentUser?.displayName ?? ''}',
              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.normal, color: Colors.white70),
            ),
          ],
        ),
        actions: [
          IconButton(icon: const Icon(Icons.refresh), onPressed: _fetchDashboard),
          IconButton(icon: const Icon(Icons.language), onPressed: () => locale.toggleLocale()),
          IconButton(
            icon: const Icon(Icons.settings),
            onPressed: () {
              Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const SettingsScreen()),
              );
            },
          ),
        ],
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        const Icon(Icons.error_outline, size: 48, color: Colors.red),
                        const SizedBox(height: 12),
                        Text(_error!, textAlign: TextAlign.center),
                        const SizedBox(height: 16),
                        ElevatedButton(onPressed: _fetchDashboard, child: Text(context.tr('refresh'))),
                      ],
                    ),
                  ),
                )
              : _buildDashboardContent(),
    );
  }

  Widget _buildDashboardContent() {
    final s = _summary!;

    return RefreshIndicator(
      onRefresh: _fetchDashboard,
      child: ListView(
        padding: const EdgeInsets.all(14),
        children: [
          // Commercial KPI Cards
          Row(
            children: [
              Expanded(
                child: _buildMetricCard(
                  title: context.tr('netSalesValue'),
                  value: s.netSalesValue,
                  subtitle: '${context.tr('ordersTotal')}: ${s.totalSalesOrders}',
                  icon: Icons.trending_up,
                  color: Colors.green.shade700,
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: _buildMetricCard(
                  title: context.tr('netPurchasesValue'),
                  value: s.netPurchasesValue,
                  subtitle: context.tr('purchasesOverview'),
                  icon: Icons.shopping_bag_outlined,
                  color: Colors.blue.shade700,
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),

          // Inventory & Low Stock
          Card(
            child: ListTile(
              leading: const Icon(Icons.inventory_2_outlined, color: Color(0xFF1E3A8A)),
              title: Text(context.tr('inventoryOverview')),
              subtitle: Text('${context.tr('lowStockAlerts')}: ${s.lowStockCount}'),
              trailing: const Icon(Icons.arrow_forward_ios, size: 14),
              onTap: () {
                Navigator.of(context).push(
                  MaterialPageRoute(builder: (_) => const OwnerInventoryScreen()),
                );
              },
            ),
          ),
          const SizedBox(height: 10),

          // Monthly Profit & Management Result
          Card(
            color: Colors.blueGrey.shade50,
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text(
                        context.tr('profitOverview'),
                        style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Color(0xFF1E3A8A)),
                      ),
                      const Icon(Icons.account_balance_wallet_outlined, color: Color(0xFF1E3A8A)),
                    ],
                  ),
                  const Divider(height: 20),
                  _buildProfitRow(context.tr('grossProfit'), s.grossProfit ?? '0.00'),
                  _buildProfitRow(
                    context.tr('operatingExpenses'),
                    s.operatingExpensesTotal ?? '0.00',
                  ),
                  _buildProfitRow(
                    context.tr('monthlyResult'),
                    s.monthlyResult ?? '0.00',
                    isHighlight: true,
                  ),
                  _buildProfitRow(
                    context.tr('accumulatedLoss'),
                    s.closingAccumulatedLoss ?? '0.00',
                    isWarning: true,
                  ),
                  const SizedBox(height: 10),
                  Text(
                    context.tr('nonAccountingNote'),
                    style: const TextStyle(fontSize: 11, color: Colors.blueGrey, fontStyle: FontStyle.italic),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 10),

          // Monthly Costing Navigation Card
          Card(
            child: ListTile(
              leading: const Icon(Icons.calculate_outlined, color: Color(0xFF1E3A8A)),
              title: Text(context.tr('costingOverview')),
              subtitle: Text(context.tr('costingPeriods')),
              trailing: const Icon(Icons.arrow_forward_ios, size: 14),
              onTap: () {
                Navigator.of(context).push(
                  MaterialPageRoute(builder: (_) => const OwnerCostingScreen()),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildMetricCard({
    required String title,
    required String value,
    required String subtitle,
    required IconData icon,
    required Color color,
  }) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Expanded(child: Text(title, style: const TextStyle(fontSize: 12, color: Colors.grey))),
                Icon(icon, color: color, size: 20),
              ],
            ),
            const SizedBox(height: 8),
            Text(value, style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: color)),
            const SizedBox(height: 4),
            Text(subtitle, style: const TextStyle(fontSize: 11, color: Colors.grey)),
          ],
        ),
      ),
    );
  }

  Widget _buildProfitRow(String label, String value, {bool isHighlight = false, bool isWarning = false}) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(
            label,
            style: TextStyle(
              fontSize: isHighlight ? 14 : 13,
              fontWeight: isHighlight ? FontWeight.bold : FontWeight.normal,
            ),
          ),
          Text(
            value,
            style: TextStyle(
              fontSize: isHighlight ? 14 : 13,
              fontWeight: FontWeight.bold,
              color: isWarning ? Colors.red.shade700 : (isHighlight ? const Color(0xFF1E3A8A) : Colors.black87),
            ),
          ),
        ],
      ),
    );
  }
}
