import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../l10n/app_localizations.dart';
import '../../models/customer_models.dart';
import '../../providers/auth_provider.dart';
import '../../providers/locale_provider.dart';
import '../../services/customer_service.dart';
import '../settings_screen.dart';
import 'create_order_request_screen.dart';

class CustomerHomeScreen extends StatefulWidget {
  const CustomerHomeScreen({super.key});

  @override
  State<CustomerHomeScreen> createState() => _CustomerHomeScreenState();
}

class _CustomerHomeScreenState extends State<CustomerHomeScreen> with SingleTickerProviderStateMixin {
  late TabController _tabController;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 3, vsync: this);
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

    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(context.tr('customerPortal'), style: const TextStyle(fontSize: 16)),
            Text(
              auth.currentUser?.customerNameAr ?? auth.currentUser?.displayName ?? "",
              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.normal, color: Colors.white70),
            ),
          ],
        ),
        actions: [
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
        bottom: TabBar(
          controller: _tabController,
          indicatorColor: Colors.white,
          labelColor: Colors.white,
          unselectedLabelColor: Colors.white60,
          tabs: [
            Tab(icon: const Icon(Icons.storefront_outlined), text: context.tr('productCatalog')),
            Tab(icon: const Icon(Icons.receipt_long_outlined), text: context.tr('myOrders')),
            Tab(icon: const Icon(Icons.person_outline), text: context.tr('accountData')),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabController,
        children: [
          _buildCatalogTab(),
          _buildOrdersTab(),
          _buildProfileTab(),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: const Color(0xFF1E3A8A),
        foregroundColor: Colors.white,
        icon: const Icon(Icons.add_shopping_cart),
        label: Text(context.tr('createOrderRequest')),
        onPressed: () async {
          final res = await Navigator.of(context).push(
            MaterialPageRoute(builder: (_) => const CreateOrderRequestScreen()),
          );
          if (res == true) {
            _tabController.animateTo(1);
          }
        },
      ),
    );
  }

  Widget _buildCatalogTab() {
    final service = Provider.of<CustomerService>(context, listen: false);

    return FutureBuilder<List<CustomerProduct>>(
      future: service.getProducts(),
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting) {
          return const Center(child: CircularProgressIndicator());
        }
        if (snapshot.hasError) {
          return Center(child: Text('${context.tr('error')}: ${snapshot.error}'));
        }
        final products = snapshot.data ?? [];
        if (products.isEmpty) {
          return Center(child: Text(context.tr('noData')));
        }

        return ListView.builder(
          padding: const EdgeInsets.all(12),
          itemCount: products.length,
          itemBuilder: (context, index) {
            final p = products[index];
            return Card(
              child: ListTile(
                leading: const CircleAvatar(
                  backgroundColor: Color(0xFFF1F5F9),
                  child: Icon(Icons.coffee_outlined, color: Color(0xFF1E3A8A)),
                ),
                title: Text('${p.sku} - ${p.nameAr}'),
                subtitle: Text('${context.tr('baseUnit')}: ${p.baseUnit}'),
                trailing: p.referencePrice != null
                    ? Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        crossAxisAlignment: CrossAxisAlignment.end,
                        children: [
                          Text(
                            '${p.referencePrice} ${p.referencePriceUnit ?? ''}'.trim(),
                            style: const TextStyle(fontWeight: FontWeight.bold, color: Colors.green),
                          ),
                          Text(
                            context.tr('referencePrice'),
                            style: const TextStyle(fontSize: 10, color: Colors.grey),
                          ),
                        ],
                      )
                    : null,
              ),
            );
          },
        );
      },
    );
  }

  Widget _buildOrdersTab() {
    final service = Provider.of<CustomerService>(context, listen: false);

    return FutureBuilder<List<CustomerOrderSummary>>(
      future: service.getOrders(),
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting) {
          return const Center(child: CircularProgressIndicator());
        }
        if (snapshot.hasError) {
          return Center(child: Text('${context.tr('error')}: ${snapshot.error}'));
        }
        final orders = snapshot.data ?? [];
        if (orders.isEmpty) {
          return Center(child: Text(context.tr('noData')));
        }

        return ListView.builder(
          padding: const EdgeInsets.all(12),
          itemCount: orders.length,
          itemBuilder: (context, index) {
            final o = orders[index];
            return Card(
              child: ListTile(
                title: Text(o.orderNumber, style: const TextStyle(fontWeight: FontWeight.bold)),
                subtitle: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('${context.tr('orderDate')}: ${o.orderDate}'),
                    if (o.customerReference != null)
                      Text('${context.tr('customerReference')}: ${o.customerReference}'),
                    Text('${context.tr('orderedValue')}: ${o.orderedValue}'),
                    Text('${context.tr('shippedValue')}: ${o.shippedValue}'),
                    if (o.shipmentStateDisplay.isNotEmpty)
                      Text('${context.tr('shipmentState')}: ${o.shipmentStateDisplay}'),
                    const SizedBox(height: 4),
                    Text(
                      '${context.tr('pricingStatus')}: ${_pricingStatusLabel(o.pricingStatus)}',
                      style: TextStyle(
                        fontSize: 12,
                        color: o.pricingStatus == 'PENDING_REVIEW' ? Colors.orange.shade800 : Colors.green.shade800,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ],
                ),
                trailing: Chip(
                  label: Text(o.statusDisplay, style: const TextStyle(fontSize: 11)),
                ),
              ),
            );
          },
        );
      },
    );
  }

  String _pricingStatusLabel(String status) {
    switch (status) {
      case 'PENDING_REVIEW':
        return context.tr('pendingReview');
      case 'RECORDED':
        return context.tr('recordedPrice');
      case 'UNPRICED':
        return context.tr('unpriced');
      default:
        return status;
    }
  }

  Widget _buildProfileTab() {
    final service = Provider.of<CustomerService>(context, listen: false);

    return FutureBuilder<CustomerProfile>(
      future: service.getProfile(),
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting) {
          return const Center(child: CircularProgressIndicator());
        }
        if (snapshot.hasError) {
          return Center(child: Text('${context.tr('error')}: ${snapshot.error}'));
        }
        final p = snapshot.data;
        if (p == null) {
          return Center(child: Text(context.tr('noData')));
        }

        return ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  children: [
                    const CircleAvatar(
                      radius: 36,
                      backgroundColor: Color(0xFF1E3A8A),
                      child: Icon(Icons.business, color: Colors.white, size: 36),
                    ),
                    const SizedBox(height: 12),
                    Text(p.nameAr, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
                    Text(p.code, style: const TextStyle(color: Colors.grey)),
                    const Divider(height: 24),
                    _profileRow(context.tr('contactPerson'), p.contactPerson),
                    _profileRow(context.tr('phone'), p.phone),
                    _profileRow(context.tr('email'), p.email),
                    _profileRow(context.tr('address'), p.address),
                  ],
                ),
              ),
            ),
          ],
        );
      },
    );
  }

  Widget _profileRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: const TextStyle(color: Colors.grey, fontSize: 13)),
          Text(value.isNotEmpty ? value : '-', style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
        ],
      ),
    );
  }
}
