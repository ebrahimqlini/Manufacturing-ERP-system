class OwnerDashboardSummary {
  final Map<String, dynamic> period;
  final Map<String, dynamic> sales;
  final Map<String, dynamic> purchases;
  final Map<String, dynamic> inventory;
  final Map<String, dynamic> production;
  final Map<String, dynamic> costing;
  final Map<String, dynamic>? profitLoss;
  final Map<String, dynamic> financialIntegrity;
  final List<dynamic> lowStockAlerts;
  final List<dynamic> dataQuality;
  final bool accountingAvailable;
  final String accountingNote;

  const OwnerDashboardSummary({
    required this.period,
    required this.sales,
    required this.purchases,
    required this.inventory,
    required this.production,
    required this.costing,
    this.profitLoss,
    required this.financialIntegrity,
    required this.lowStockAlerts,
    required this.dataQuality,
    required this.accountingAvailable,
    required this.accountingNote,
  });

  factory OwnerDashboardSummary.fromJson(Map<String, dynamic> json) {
    Map<String, dynamic> toMap(dynamic value) {
      if (value == null) return {};
      if (value is Map<String, dynamic>) return value;
      return (value as Map).cast<String, dynamic>();
    }

    Map<String, dynamic>? toNullableMap(dynamic value) {
      if (value == null) return null;
      if (value is Map<String, dynamic>) return value;
      return (value as Map).cast<String, dynamic>();
    }

    final integrity = toMap(json['financial_integrity']);
    final lowStockAlerts = json['low_stock_alerts'] as List? ?? [];

    return OwnerDashboardSummary(
      period: toMap(json['period']),
      sales: toMap(json['sales']),
      purchases: toMap(json['purchases']),
      inventory: toMap(json['inventory']),
      production: toMap(json['production']),
      costing: toMap(json['costing']),
      profitLoss: toNullableMap(json['profit_loss']),
      financialIntegrity: integrity,
      lowStockAlerts: lowStockAlerts,
      dataQuality: json['data_quality'] as List? ?? [],
      accountingAvailable: integrity['accounting_available'] as bool? ??
          json['accounting_available'] as bool? ??
          false,
      accountingNote: integrity['note'] as String? ??
          json['accounting_note'] as String? ??
          '',
    );
  }

  String get netSalesValue =>
      sales['net_shipped_value']?.toString() ??
      sales['net_sales_value']?.toString() ??
      '0.00';

  String get totalSalesOrders =>
      sales['orders_total']?.toString() ?? '0';

  String get netPurchasesValue =>
      purchases['net_received_value']?.toString() ??
      purchases['net_purchases_value']?.toString() ??
      '0.00';

  int get lowStockCount {
    if (lowStockAlerts.isNotEmpty) return lowStockAlerts.length;
    final items = inventory['low_stock_items'];
    return items is List ? items.length : 0;
  }

  Map<String, dynamic> get _profitTotals {
    final totals = profitLoss?['range_totals'];
    if (totals is Map) return totals.cast<String, dynamic>();
    return profitLoss ?? {};
  }

  String? get grossProfit => _profitTotals['gross_profit']?.toString();

  String? get operatingExpensesTotal =>
      _profitTotals['operating_expenses_total']?.toString();

  String? get monthlyResult => _profitTotals['monthly_result']?.toString();

  String? get closingAccumulatedLoss =>
      _profitTotals['latest_closing_accumulated_loss']?.toString() ??
      _profitTotals['closing_accumulated_loss']?.toString();
}

class OwnerCostingPeriodItem {
  final int id;
  final int year;
  final int month;
  final String status;
  final String totalProductionCost;
  final String materialsTotal;
  final String laborTotal;
  final String electricityTotal;
  final String waterTotal;
  final String otherIndustrialTotal;
  final bool isComplete;
  final bool reviewRequired;
  final int entryCount;
  final List<String> missingDataNotes;

  const OwnerCostingPeriodItem({
    required this.id,
    required this.year,
    required this.month,
    required this.status,
    required this.totalProductionCost,
    required this.materialsTotal,
    required this.laborTotal,
    required this.electricityTotal,
    required this.waterTotal,
    required this.otherIndustrialTotal,
    required this.isComplete,
    required this.reviewRequired,
    required this.entryCount,
    required this.missingDataNotes,
  });

  factory OwnerCostingPeriodItem.fromJson(Map<String, dynamic> json) {
    final notes = json['missing_data_notes'];
    final List<String> notesList = notes is List
        ? notes.map((e) => e.toString()).toList()
        : <String>[];

    return OwnerCostingPeriodItem(
      id: json['id'] as int? ?? 0,
      year: json['year'] as int? ?? 0,
      month: json['month'] as int? ?? 0,
      status: json['status'] as String? ?? '',
      totalProductionCost: json['total_production_cost']?.toString() ?? '0.00',
      materialsTotal: json['materials_total']?.toString() ??
          json['actual_material_cost']?.toString() ??
          '0.00',
      laborTotal: json['labor_total']?.toString() ??
          json['total_labor_expense']?.toString() ??
          '0.00',
      electricityTotal: json['electricity_total']?.toString() ??
          json['total_electricity_expense']?.toString() ??
          '0.00',
      waterTotal: json['water_total']?.toString() ?? '0.00',
      otherIndustrialTotal: json['other_industrial_total']?.toString() ?? '0.00',
      isComplete: json['is_complete'] as bool? ?? false,
      reviewRequired: json['review_required'] as bool? ?? false,
      entryCount: json['entry_count'] as int? ?? 0,
      missingDataNotes: notesList,
    );
  }
}

class OwnerStockRowItem {
  final int productId;
  final String productSku;
  final String productName;
  final String category;
  final String balance;
  final String baseUnit;
  final String lowStockThreshold;
  final bool belowThreshold;

  const OwnerStockRowItem({
    required this.productId,
    required this.productSku,
    required this.productName,
    required this.category,
    required this.balance,
    required this.baseUnit,
    required this.lowStockThreshold,
    required this.belowThreshold,
  });

  factory OwnerStockRowItem.fromJson(Map<String, dynamic> json) {
    return OwnerStockRowItem(
      productId: json['product_id'] as int? ?? 0,
      productSku: json['sku'] as String? ?? json['product_sku'] as String? ?? '',
      productName: json['name_ar'] as String? ??
          json['product_name'] as String? ??
          '',
      category: json['category_name'] as String? ??
          json['category'] as String? ??
          '',
      balance: json['quantity_base']?.toString() ??
          json['balance']?.toString() ??
          '0',
      baseUnit: json['base_uom'] as String? ??
          json['base_unit'] as String? ??
          '',
      lowStockThreshold: json['low_stock_threshold']?.toString() ?? '0',
      belowThreshold: json['below_threshold'] as bool? ?? false,
    );
  }
}
