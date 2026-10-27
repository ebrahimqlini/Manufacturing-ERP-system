class EmployeeOperation {
  final int id;
  final int sequence;
  final String name;
  final String status;
  final bool isOptional;

  const EmployeeOperation({
    required this.id,
    required this.sequence,
    required this.name,
    required this.status,
    required this.isOptional,
  });

  factory EmployeeOperation.fromJson(Map<String, dynamic> json) {
    return EmployeeOperation(
      id: json['id'] as int? ?? 0,
      sequence: json['sequence'] as int? ?? 0,
      name: json['name'] as String? ?? '',
      status: json['status'] as String? ?? '',
      isOptional: json['is_optional'] as bool? ?? false,
    );
  }

  bool get isCompleted => status == 'COMPLETED';
  bool get isSkipped => status == 'SKIPPED';
  bool get isPending => status == 'PENDING';
}

class EmployeeRequirement {
  final int requirementId;
  final int materialId;
  final String material;
  final int unitId;
  final String unit;
  final String plannedBase;
  final String issuedBase;
  final String remainingBase;
  final String availableAtSourceBase;
  final String shortageBase;

  const EmployeeRequirement({
    required this.requirementId,
    required this.materialId,
    required this.material,
    required this.unitId,
    required this.unit,
    required this.plannedBase,
    required this.issuedBase,
    required this.remainingBase,
    required this.availableAtSourceBase,
    required this.shortageBase,
  });

  factory EmployeeRequirement.fromJson(Map<String, dynamic> json) {
    return EmployeeRequirement(
      requirementId: json['requirement_id'] as int? ?? 0,
      materialId: json['material_id'] as int? ?? 0,
      material: json['material'] as String? ?? '',
      unitId: json['unit_id'] as int? ?? 0,
      unit: json['unit'] as String? ?? '',
      plannedBase: json['planned_base']?.toString() ?? '0',
      issuedBase: json['issued_base']?.toString() ?? '0',
      remainingBase: json['remaining_base']?.toString() ?? '0',
      availableAtSourceBase: json['available_at_source_base']?.toString() ?? '0',
      shortageBase: json['shortage_base']?.toString() ?? '0',
    );
  }
}

class EmployeeResult {
  final int id;
  final String goodQuantity;
  final String scrapQuantity;
  final String unit;
  final String? postedAt;
  final bool reversed;

  const EmployeeResult({
    required this.id,
    required this.goodQuantity,
    required this.scrapQuantity,
    required this.unit,
    this.postedAt,
    required this.reversed,
  });

  factory EmployeeResult.fromJson(Map<String, dynamic> json) {
    return EmployeeResult(
      id: json['id'] as int? ?? 0,
      goodQuantity: json['good_quantity']?.toString() ?? '0',
      scrapQuantity: json['scrap_quantity']?.toString() ?? '0',
      unit: json['unit'] as String? ?? '',
      postedAt: json['posted_at'] as String?,
      reversed: json['reversed'] as bool? ?? false,
    );
  }
}

class EmployeeCounter {
  final int id;
  final String status;
  final String unit;
  final String startReading;
  final String? endReading;
  final String? counterOutput;
  final String? rejectedQuantity;
  final String? startedAt;
  final String? endedAt;

  const EmployeeCounter({
    required this.id,
    required this.status,
    required this.unit,
    required this.startReading,
    this.endReading,
    this.counterOutput,
    this.rejectedQuantity,
    this.startedAt,
    this.endedAt,
  });

  factory EmployeeCounter.fromJson(Map<String, dynamic> json) {
    return EmployeeCounter(
      id: json['id'] as int? ?? 0,
      status: json['status'] as String? ?? '',
      unit: json['unit'] as String? ?? '',
      startReading: json['start_reading']?.toString() ?? '0',
      endReading: json['end_reading']?.toString(),
      counterOutput: json['counter_output']?.toString(),
      rejectedQuantity: json['rejected_quantity']?.toString(),
      startedAt: json['started_at'] as String?,
      endedAt: json['ended_at'] as String?,
    );
  }

  bool get isProvisional => status == 'PROVISIONAL';
  bool get isComplete => status == 'COMPLETE';
  bool get isApproved => status == 'APPROVED';
  bool get isCorrected => status == 'CORRECTED';
}

class EmployeeWaste {
  final int id;
  final int? resultId;
  final int materialId;
  final String quantityBase;
  final String? postedAt;
  final bool reversed;

  const EmployeeWaste({
    required this.id,
    this.resultId,
    required this.materialId,
    required this.quantityBase,
    this.postedAt,
    required this.reversed,
  });

  factory EmployeeWaste.fromJson(Map<String, dynamic> json) {
    return EmployeeWaste(
      id: json['id'] as int? ?? 0,
      resultId: json['result_id'] as int?,
      materialId: json['material_id'] as int? ?? 0,
      quantityBase: json['quantity_base']?.toString() ?? '0',
      postedAt: json['posted_at'] as String?,
      reversed: json['reversed'] as bool? ?? false,
    );
  }
}

class EmployeeProductionOrder {
  final int id;
  final String orderNumber;
  final String status;
  final int productId;
  final String product;
  final String productionLine;
  final String plannedQuantity;
  final int plannedUnitId;
  final String unit;
  final String plannedQuantityBase;
  final int sourceLocationId;
  final List<EmployeeOperation> operations;
  final List<EmployeeRequirement> materials;
  final List<EmployeeResult> results;
  final List<EmployeeCounter> counters;
  final List<EmployeeWaste> materialWaste;

  const EmployeeProductionOrder({
    required this.id,
    required this.orderNumber,
    required this.status,
    required this.productId,
    required this.product,
    required this.productionLine,
    required this.plannedQuantity,
    required this.plannedUnitId,
    required this.unit,
    required this.plannedQuantityBase,
    required this.sourceLocationId,
    required this.operations,
    required this.materials,
    required this.results,
    required this.counters,
    required this.materialWaste,
  });

  factory EmployeeProductionOrder.fromJson(Map<String, dynamic> json) {
    final ops = json['operations'] as List? ?? [];
    final mats = json['materials'] as List? ?? [];
    final res = json['results'] as List? ?? [];
    final cnt = json['counters'] as List? ?? [];
    final wst = json['material_waste'] as List? ?? [];

    return EmployeeProductionOrder(
      id: json['id'] as int? ?? 0,
      orderNumber: json['order_number'] as String? ?? '',
      status: json['status'] as String? ?? '',
      productId: json['product_id'] as int? ?? 0,
      product: json['product'] as String? ?? '',
      productionLine: json['production_line'] as String? ?? '',
      plannedQuantity: json['planned_quantity']?.toString() ?? '0',
      plannedUnitId: json['planned_unit_id'] as int? ?? 0,
      unit: json['unit'] as String? ?? '',
      plannedQuantityBase: json['planned_quantity_base']?.toString() ?? '0',
      sourceLocationId: json['source_location_id'] as int? ?? 0,
      operations: ops.map((e) => EmployeeOperation.fromJson(e as Map<String, dynamic>)).toList(),
      materials: mats.map((e) => EmployeeRequirement.fromJson(e as Map<String, dynamic>)).toList(),
      results: res.map((e) => EmployeeResult.fromJson(e as Map<String, dynamic>)).toList(),
      counters: cnt.map((e) => EmployeeCounter.fromJson(e as Map<String, dynamic>)).toList(),
      materialWaste: wst.map((e) => EmployeeWaste.fromJson(e as Map<String, dynamic>)).toList(),
    );
  }

  bool get isReleased => status == 'RELEASED';
  bool get isInProgress => status == 'IN_PROGRESS';
}

class RawMaterialItem {
  final int id;
  final String sku;
  final String name;
  final int baseUnitId;
  final String baseUnit;

  const RawMaterialItem({
    required this.id,
    required this.sku,
    required this.name,
    required this.baseUnitId,
    required this.baseUnit,
  });

  factory RawMaterialItem.fromJson(Map<String, dynamic> json) {
    return RawMaterialItem(
      id: json['id'] as int? ?? 0,
      sku: json['sku'] as String? ?? '',
      name: json['name'] as String? ?? '',
      baseUnitId: json['base_unit_id'] as int? ?? 0,
      baseUnit: json['base_unit'] as String? ?? '',
    );
  }
}

class StorageLocationItem {
  final int id;
  final String warehouse;
  final String code;
  final String name;

  const StorageLocationItem({
    required this.id,
    required this.warehouse,
    required this.code,
    required this.name,
  });

  factory StorageLocationItem.fromJson(Map<String, dynamic> json) {
    return StorageLocationItem(
      id: json['id'] as int? ?? 0,
      warehouse: json['warehouse'] as String? ?? '',
      code: json['code'] as String? ?? '',
      name: json['name'] as String? ?? '',
    );
  }

  String get displayName => '$warehouse / $code - $name';
}

class StockBalanceItem {
  final int materialId;
  final int locationId;
  final String quantityBase;
  final String baseUnit;

  const StockBalanceItem({
    required this.materialId,
    required this.locationId,
    required this.quantityBase,
    required this.baseUnit,
  });

  factory StockBalanceItem.fromJson(Map<String, dynamic> json) {
    return StockBalanceItem(
      materialId: json['material_id'] as int? ?? 0,
      locationId: json['location_id'] as int? ?? 0,
      quantityBase: json['quantity_base']?.toString() ?? '0',
      baseUnit: json['base_unit'] as String? ?? '',
    );
  }
}

class EmployeeMaterialsData {
  final List<RawMaterialItem> materials;
  final List<StorageLocationItem> locations;
  final List<StockBalanceItem> stock;

  const EmployeeMaterialsData({
    required this.materials,
    required this.locations,
    required this.stock,
  });

  factory EmployeeMaterialsData.fromJson(Map<String, dynamic> json) {
    final mats = json['materials'] as List? ?? [];
    final locs = json['locations'] as List? ?? [];
    final stk = json['stock'] as List? ?? [];

    return EmployeeMaterialsData(
      materials: mats.map((e) => RawMaterialItem.fromJson(e as Map<String, dynamic>)).toList(),
      locations: locs.map((e) => StorageLocationItem.fromJson(e as Map<String, dynamic>)).toList(),
      stock: stk.map((e) => StockBalanceItem.fromJson(e as Map<String, dynamic>)).toList(),
    );
  }
}
