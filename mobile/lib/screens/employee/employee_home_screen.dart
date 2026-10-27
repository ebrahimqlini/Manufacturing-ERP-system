import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../l10n/app_localizations.dart';
import '../../models/employee_models.dart';
import '../../providers/auth_provider.dart';
import '../../providers/employee_provider.dart';
import '../../providers/locale_provider.dart';
import '../../services/employee_service.dart';
import '../settings_screen.dart';
import 'counter_management_screen.dart';
import 'finished_receipt_screen.dart';
import 'material_issue_screen.dart';
import 'order_detail_screen.dart';

class EmployeeHomeScreen extends StatefulWidget {
  const EmployeeHomeScreen({super.key});

  @override
  State<EmployeeHomeScreen> createState() => _EmployeeHomeScreenState();
}

class _EmployeeHomeScreenState extends State<EmployeeHomeScreen> with SingleTickerProviderStateMixin {
  late TabController _tabController;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 3, vsync: this);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      Provider.of<EmployeeProvider>(context, listen: false).fetchOrders();
    });
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final auth = Provider.of<AuthProvider>(context);
    final locale = Provider.of<LocaleProvider>(context);
    final employeeProvider = Provider.of<EmployeeProvider>(context);

    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(context.tr('appName'), style: const TextStyle(fontSize: 16)),
            Text(
              '${context.tr('roleEmployee')} - ${auth.currentUser?.displayName ?? ''}',
              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.normal, color: Colors.white70),
            ),
          ],
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: context.tr('refresh'),
            onPressed: () => employeeProvider.fetchOrders(),
          ),
          IconButton(
            icon: const Icon(Icons.language),
            tooltip: context.tr('language'),
            onPressed: () => locale.toggleLocale(),
          ),
          IconButton(
            icon: const Icon(Icons.settings),
            tooltip: context.tr('settings'),
            onPressed: () {
              Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const SettingsScreen()),
              );
            },
          ),
        ],
        bottom: TabBar(
          controller: _tabController,
          indicatorColor: Colors.white,
          labelColor: Colors.white,
          unselectedLabelColor: Colors.white60,
          tabs: [
            Tab(icon: const Icon(Icons.assignment_outlined), text: context.tr('productionOrders')),
            Tab(icon: const Icon(Icons.inventory_2_outlined), text: context.tr('materialStock')),
            Tab(icon: const Icon(Icons.flash_on_outlined), text: context.tr('operations')),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabController,
        children: [
          _buildOrdersTab(employeeProvider),
          _buildStockTab(),
          _buildQuickActionsTab(),
        ],
      ),
    );
  }

  Widget _buildOrdersTab(EmployeeProvider provider) {
    if (provider.isLoading) {
      return const Center(child: CircularProgressIndicator());
    }

    if (provider.errorMessage != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.error_outline, size: 48, color: Colors.red),
              const SizedBox(height: 12),
              Text(provider.errorMessage!, textAlign: TextAlign.center),
              const SizedBox(height: 16),
              ElevatedButton.icon(
                icon: const Icon(Icons.refresh),
                label: Text(context.tr('refresh')),
                onPressed: () => provider.fetchOrders(),
              ),
            ],
          ),
        ),
      );
    }

    if (provider.orders.isEmpty) {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.done_all, size: 48, color: Colors.grey),
            const SizedBox(height: 12),
            Text(context.tr('noData'), style: const TextStyle(color: Colors.grey)),
          ],
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: () => provider.fetchOrders(),
      child: ListView.builder(
        padding: const EdgeInsets.symmetric(vertical: 8),
        itemCount: provider.orders.length,
        itemBuilder: (context, index) {
          final order = provider.orders[index];
          return Card(
            child: ListTile(
              leading: CircleAvatar(
                backgroundColor: order.isInProgress ? Colors.orange.shade100 : Colors.blue.shade100,
                child: Icon(
                  order.isInProgress ? Icons.precision_manufacturing : Icons.pending_actions,
                  color: order.isInProgress ? Colors.orange.shade900 : Colors.blue.shade900,
                ),
              ),
              title: Text(
                '${order.orderNumber} - ${order.product}',
                style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14),
              ),
              subtitle: Text(
                '${context.tr('productionLine')}: ${order.productionLine} | '
                '${context.tr('quantity')}: ${order.plannedQuantity} ${order.unit} | '
                '${context.tr('status')}: ${order.status}',
                style: const TextStyle(fontSize: 12),
              ),
              trailing: const Icon(Icons.arrow_forward_ios, size: 14),
              onTap: () async {
                await Navigator.of(context).push(
                  MaterialPageRoute(
                    builder: (_) => OrderDetailScreen(orderId: order.id),
                  ),
                );
                if (mounted) provider.fetchOrders();
              },
            ),
          );
        },
      ),
    );
  }

  Widget _buildStockTab() {
    final employeeService = Provider.of<EmployeeService>(context, listen: false);

    return FutureBuilder<EmployeeMaterialsData>(
      future: employeeService.getMaterialsData(),
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting) {
          return const Center(child: CircularProgressIndicator());
        }
        if (snapshot.hasError) {
          return Center(child: Text('${context.tr('error')}: ${snapshot.error}'));
        }
        final data = snapshot.data;
        if (data == null || data.stock.isEmpty) {
          return Center(child: Text(context.tr('noData')));
        }

        return ListView.builder(
          padding: const EdgeInsets.all(12),
          itemCount: data.stock.length,
          itemBuilder: (context, index) {
            final item = data.stock[index];
            final material = data.materials.firstWhere(
              (m) => m.id == item.materialId,
              orElse: () => RawMaterialItem(
                id: item.materialId,
                sku: '?',
                name: '?',
                baseUnitId: 0,
                baseUnit: item.baseUnit,
              ),
            );
            final location = data.locations.firstWhere(
              (l) => l.id == item.locationId,
              orElse: () => StorageLocationItem(
                id: item.locationId,
                warehouse: '?',
                code: '?',
                name: '?',
              ),
            );

            return Card(
              child: ListTile(
                leading: const Icon(Icons.inventory, color: Color(0xFF1E3A8A)),
                title: Text('${material.sku} - ${material.name}'),
                subtitle: Text('${context.tr('location')}: ${location.displayName}'),
                trailing: Text(
                  '${item.quantityBase} ${item.baseUnit}',
                  style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13),
                ),
              ),
            );
          },
        );
      },
    );
  }

  Widget _buildQuickActionsTab() {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        _buildActionCard(
          icon: Icons.output,
          title: context.tr('materialIssues'),
          subtitle: context.tr('issueMaterialsSubtitle'),
          color: Colors.blue.shade700,
          onTap: () {
            Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => const MaterialIssueScreen()),
            );
          },
        ),
        const SizedBox(height: 12),
        _buildActionCard(
          icon: Icons.input,
          title: context.tr('finishedReceipts'),
          subtitle: context.tr('finishedReceiptsSubtitle'),
          color: Colors.green.shade700,
          onTap: () {
            Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => const FinishedReceiptScreen()),
            );
          },
        ),
        const SizedBox(height: 12),
        _buildActionCard(
          icon: Icons.speed,
          title: context.tr('counters'),
          subtitle: context.tr('countersSubtitle'),
          color: Colors.amber.shade800,
          onTap: () {
            Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => const CounterManagementScreen()),
            );
          },
        ),
      ],
    );
  }

  Widget _buildActionCard({
    required IconData icon,
    required String title,
    required String subtitle,
    required Color color,
    required VoidCallback onTap,
  }) {
    return Card(
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(10),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              CircleAvatar(
                backgroundColor: color.withValues(alpha: 0.12),
                radius: 26,
                child: Icon(icon, color: color, size: 28),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.bold)),
                    const SizedBox(height: 4),
                    Text(subtitle, style: const TextStyle(fontSize: 12, color: Colors.grey)),
                  ],
                ),
              ),
              const Icon(Icons.arrow_forward_ios, size: 14, color: Colors.grey),
            ],
          ),
        ),
      ),
    );
  }
}
