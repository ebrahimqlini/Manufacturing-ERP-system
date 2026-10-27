class CustomerProfile {
  final String code;
  final String nameAr;
  final String nameEn;
  final String contactPerson;
  final String phone;
  final String email;
  final String address;
  final bool active;

  const CustomerProfile({
    required this.code,
    required this.nameAr,
    required this.nameEn,
    required this.contactPerson,
    required this.phone,
    required this.email,
    required this.address,
    required this.active,
  });

  factory CustomerProfile.fromJson(Map<String, dynamic> json) {
    return CustomerProfile(
      code: json['code'] as String? ?? '',
      nameAr: json['name_ar'] as String? ?? '',
      nameEn: json['name_en'] as String? ?? '',
      contactPerson: json['contact_person'] as String? ?? '',
      phone: json['phone'] as String? ?? '',
      email: json['email'] as String? ?? '',
      address: json['address'] as String? ?? '',
      active: json['active'] as bool? ?? false,
    );
  }
}

class CustomerUnit {
  final int id;
  final String code;
  final String nameAr;
  final bool isBase;
  final String? factor;

  const CustomerUnit({
    required this.id,
    required this.code,
    required this.nameAr,
    this.isBase = false,
    this.factor,
  });

  factory CustomerUnit.fromJson(Map<String, dynamic> json) {
    return CustomerUnit(
      id: json['id'] as int? ?? 0,
      code: json['code'] as String? ?? '',
      nameAr: json['name_ar'] as String? ?? '',
      isBase: json['is_base'] as bool? ?? false,
      factor: json['factor']?.toString(),
    );
  }
}

class CustomerProduct {
  final int id;
  final String sku;
  final String nameAr;
  final String nameEn;
  final String? referencePrice;
  final String? referencePriceUnit;
  final int baseUnitId;
  final String baseUnit;
  final List<CustomerUnit> units;

  const CustomerProduct({
    required this.id,
    required this.sku,
    required this.nameAr,
    required this.nameEn,
    this.referencePrice,
    this.referencePriceUnit,
    required this.baseUnitId,
    required this.baseUnit,
    required this.units,
  });

  factory CustomerProduct.fromJson(Map<String, dynamic> json) {
    final refPriceObj = json['reference_price'] as Map<String, dynamic>?;
    final unitsList = json['units'] as List? ?? [];
    final baseUomObj = json['base_uom'] as Map<String, dynamic>?;

    return CustomerProduct(
      id: json['id'] as int? ?? 0,
      sku: json['sku'] as String? ?? '',
      nameAr: json['name_ar'] as String? ?? '',
      nameEn: json['name_en'] as String? ?? '',
      referencePrice: refPriceObj?['amount']?.toString(),
      referencePriceUnit: refPriceObj?['unit']?['name_ar'] as String?,
      baseUnitId: baseUomObj?['id'] as int? ?? 0,
      baseUnit: baseUomObj?['code'] as String? ?? '',
      units: unitsList.map((e) => CustomerUnit.fromJson(e as Map<String, dynamic>)).toList(),
    );
  }
}

class CustomerOrderSummary {
  final String orderNumber;
  final String orderDate;
  final String? requestedDeliveryDate;
  final String status;
  final String statusDisplay;
  final String shipmentState;
  final String shipmentStateDisplay;
  final String pricingStatus;
  final String? customerReference;
  final String orderedValue;
  final String shippedValue;
  final int lineCount;
  final String? totalAmount;
  final String currency;

  const CustomerOrderSummary({
    required this.orderNumber,
    required this.orderDate,
    this.requestedDeliveryDate,
    required this.status,
    required this.statusDisplay,
    required this.shipmentState,
    required this.shipmentStateDisplay,
    required this.pricingStatus,
    this.customerReference,
    required this.orderedValue,
    required this.shippedValue,
    required this.lineCount,
    this.totalAmount,
    required this.currency,
  });

  factory CustomerOrderSummary.fromJson(Map<String, dynamic> json) {
    final status = json['status'] as String? ?? '';
    return CustomerOrderSummary(
      orderNumber: json['order_number'] as String? ?? '',
      orderDate: json['order_date'] as String? ?? '',
      requestedDeliveryDate: json['requested_delivery_date'] as String?,
      status: status,
      statusDisplay: json['status_display'] as String? ?? status,
      shipmentState: json['shipment_state'] as String? ?? '',
      shipmentStateDisplay: json['shipment_state_display'] as String? ?? '',
      pricingStatus: json['pricing_status'] as String? ?? '',
      customerReference: json['customer_reference'] as String?,
      orderedValue: json['ordered_value']?.toString() ??
          json['total_amount']?.toString() ??
          '0.00',
      shippedValue: json['shipped_value']?.toString() ?? '0.00',
      lineCount: json['line_count'] as int? ?? 0,
      totalAmount: json['total_amount']?.toString(),
      currency: json['currency'] as String? ?? '',
    );
  }
}

class CustomerOrderRequestLine {
  final int product;
  final String quantity;
  final int unit;
  final String? notes;

  const CustomerOrderRequestLine({
    required this.product,
    required this.quantity,
    required this.unit,
    this.notes,
  });

  Map<String, dynamic> toJson() {
    return {
      'product': product,
      'quantity': quantity,
      'unit': unit,
      if (notes != null && notes!.isNotEmpty) 'notes': notes,
    };
  }
}
