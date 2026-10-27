import 'package:flutter_test/flutter_test.dart';
import 'package:paper_factory_mobile/models/user_model.dart';
import 'package:paper_factory_mobile/models/employee_models.dart';
import 'package:paper_factory_mobile/models/owner_models.dart';
import 'package:paper_factory_mobile/models/customer_models.dart';

void main() {
  group('Model Parsing Tests from Real Backend Schema', () {
    test('CurrentUser parses Owner, Employee, and Customer roles', () {
      final ownerJson = {
        'username': 'owner_ibrahim',
        'first_name': 'إبراهيم',
        'last_name': 'المالك',
        'email': 'owner@factory.local',
        'is_superuser': true,
        'is_staff': true,
        'is_owner': true,
        'is_employee': false,
        'is_customer': false,
        'role': 'OWNER',
        'customer_id': null,
        'customer_code': null,
        'customer_name_ar': null,
        'permissions': ['manufacturing.view_productionorder'],
      };

      final owner = CurrentUser.fromJson(ownerJson);
      expect(owner.username, 'owner_ibrahim');
      expect(owner.role, UserRole.owner);
      expect(owner.isOwner, isTrue);
      expect(owner.isEmployee, isFalse);
      expect(owner.displayName, 'إبراهيم المالك');

      final employeeJson = {
        'username': 'operator_1',
        'first_name': 'عامل',
        'last_name': 'تشغيل',
        'email': 'op@factory.local',
        'is_superuser': false,
        'is_staff': true,
        'is_owner': false,
        'is_employee': true,
        'is_customer': false,
        'role': 'EMPLOYEE',
        'customer_id': null,
        'permissions': ['manufacturing.record_counter_reading'],
      };
      final employee = CurrentUser.fromJson(employeeJson);
      expect(employee.role, UserRole.employee);
      expect(employee.isEmployee, isTrue);

      final customerJson = {
        'username': 'customer_c1',
        'first_name': '',
        'last_name': '',
        'email': 'cust@company.com',
        'is_superuser': false,
        'is_staff': false,
        'is_owner': false,
        'is_employee': false,
        'is_customer': true,
        'role': 'CUSTOMER',
        'customer_id': 5,
        'customer_code': 'CUST-005',
        'customer_name_ar': 'شركة الأهرام للتوزيع',
        'permissions': [],
      };
      final customer = CurrentUser.fromJson(customerJson);
      expect(customer.role, UserRole.customer);
      expect(customer.isCustomer, isTrue);
      expect(customer.customerCode, 'CUST-005');
      expect(customer.customerNameAr, 'شركة الأهرام للتوزيع');
    });

    test('EmployeeProductionOrder parses operations, requirements, results, counters, and waste', () {
      final orderJson = {
        'id': 101,
        'order_number': 'ORD-2026-101',
        'status': 'IN_PROGRESS',
        'product_id': 12,
        'product': 'كوب ورقي 9 أونصة',
        'production_line': 'paper-cups',
        'planned_quantity': '1000.000000000000',
        'planned_unit_id': 4,
        'unit': 'كرتونة',
        'planned_quantity_base': '1000.000000000000',
        'source_location_id': 3,
        'operations': [
          {'id': 1, 'sequence': 10, 'name': 'طباعة الورق', 'status': 'COMPLETED', 'is_optional': false},
          {'id': 2, 'sequence': 20, 'name': 'تقطيع وتجعيد', 'status': 'IN_PROGRESS', 'is_optional': false},
          {'id': 3, 'sequence': 30, 'name': 'تركيب القاع', 'status': 'PENDING', 'is_optional': true},
        ],
        'materials': [
          {
            'requirement_id': 51,
            'material_id': 7,
            'material': 'رول ورق خام 100جم',
            'unit_id': 5,
            'unit': 'كجم',
            'planned_base': '500.000000000000',
            'issued_base': '350.000000000000',
            'remaining_base': '150.000000000000',
            'available_at_source_base': '2000.000000000000',
            'shortage_base': '0.000000000000',
          }
        ],
        'results': [
          {
            'id': 201,
            'good_quantity': '300.000000000000',
            'scrap_quantity': '15.000000000000',
            'unit': 'كرتونة',
            'posted_at': '2026-10-24T10:00:00Z',
            'reversed': false,
          }
        ],
        'counters': [
          {
            'id': 301,
            'status': 'COMPLETE',
            'unit': 'قطعة',
            'start_reading': '1000.000000000000',
            'end_reading': '1500.000000000000',
            'counter_output': '500.000000000000',
            'rejected_quantity': '10.000000000000',
            'started_at': '2026-10-24T08:00:00Z',
            'ended_at': '2026-10-24T12:00:00Z',
          }
        ],
        'material_waste': [
          {
            'id': 401,
            'result_id': 201,
            'material_id': 7,
            'quantity_base': '12.500000000000',
            'posted_at': '2026-10-24T10:05:00Z',
            'reversed': false,
          }
        ],
      };

      final order = EmployeeProductionOrder.fromJson(orderJson);
      expect(order.id, 101);
      expect(order.orderNumber, 'ORD-2026-101');
      expect(order.plannedUnitId, 4);
      expect(order.isInProgress, isTrue);
      expect(order.operations.length, 3);
      expect(order.operations.first.isCompleted, isTrue);
      expect(order.operations.last.isOptional, isTrue);

      expect(order.materials.length, 1);
      expect(order.materials.first.unitId, 5);
      expect(order.materials.first.remainingBase, '150.000000000000');

      expect(order.results.length, 1);
      expect(order.results.first.goodQuantity, '300.000000000000');

      expect(order.counters.length, 1);
      expect(order.counters.first.counterOutput, '500.000000000000');

      expect(order.materialWaste.length, 1);
      expect(order.materialWaste.first.quantityBase, '12.500000000000');
    });

    test('OwnerDashboardSummary parses KPI cards and non-accounting status', () {
      final dashJson = {
        'period': {'start_date': '2026-10-01', 'end_date': '2026-10-25'},
        'sales': {
          'net_sales_value': '150000.00',
          'orders_total': 18,
        },
        'purchases': {
          'net_purchases_value': '80000.00',
          'receipts_count': 10,
        },
        'inventory': {
          'low_stock_items': [{'sku': 'RAW-01', 'name': 'ورق'}],
        },
        'production': {},
        'costing': {},
        'profit_loss': {
          'gross_profit': '45000.00',
          'operating_expenses_total': '20000.00',
          'monthly_result': '25000.00',
          'closing_accumulated_loss': '0.00',
        },
        'data_quality': ['ACCOUNTING_UNAVAILABLE'],
        'accounting_available': false,
        'accounting_note': 'Shared accounting is not implemented.',
      };

      final summary = OwnerDashboardSummary.fromJson(dashJson);
      expect(summary.netSalesValue, '150000.00');
      expect(summary.totalSalesOrders, '18');
      expect(summary.netPurchasesValue, '80000.00');
      expect(summary.lowStockCount, 1);
      expect(summary.grossProfit, '45000.00');
      expect(summary.monthlyResult, '25000.00');
      expect(summary.accountingAvailable, isFalse);
    });

    test('Customer models parse products, prices, and orders', () {
      final prodJson = {
        'id': 15,
        'sku': 'CUP-9OZ',
        'name_ar': 'كوب ورقي 9 أونصة',
        'name_en': 'Paper Cup 9oz',
        'base_uom': {'id': 8, 'code': 'كرتونة', 'name_ar': 'كرتونة'},
        'reference_price': {
          'amount': '250.00',
          'unit': {'id': 8, 'name_ar': 'كرتونة'},
        },
        'units': [
          {'id': 8, 'code': 'كرتونة', 'name_ar': 'كرتونة', 'is_base': true, 'factor': '1.0'},
          {'id': 9, 'code': 'قطعة', 'name_ar': 'قطعة', 'factor': '12.0'},
        ],
      };

      final prod = CustomerProduct.fromJson(prodJson);
      expect(prod.id, 15);
      expect(prod.sku, 'CUP-9OZ');
      expect(prod.referencePrice, '250.00');
      expect(prod.baseUnitId, 8);
      expect(prod.units.map((unit) => unit.id), [8, 9]);
      expect(prod.units.first.isBase, isTrue);

      final orderJson = {
        'order_number': 'SO-2026-001',
        'order_date': '2026-10-20',
        'requested_delivery_date': '2026-10-30',
        'status': 'DRAFT',
        'pricing_status': 'PENDING_REVIEW',
        'customer_reference': 'PO-991',
        'line_count': 2,
        'total_amount': null,
        'currency': 'EGP',
      };

      final order = CustomerOrderSummary.fromJson(orderJson);
      expect(order.orderNumber, 'SO-2026-001');
      expect(order.pricingStatus, 'PENDING_REVIEW');
      expect(order.status, 'DRAFT');
    });

    test('Owner models parse the actual dashboard, costing, and stock payload keys', () {
      final dashJson = {
        'period': {'start_date': '2026-10-01', 'end_date': '2026-10-31'},
        'sales': {'net_shipped_value': '150000.00', 'orders_total': 18},
        'purchases': {'net_received_value': '80000.00'},
        'inventory': {},
        'production': {},
        'costing': {},
        'low_stock_alerts': [
          {'product_id': 7, 'sku': 'RAW-01'},
        ],
        'profit_loss': {
          'range_totals': {
            'gross_profit': '45000.00',
            'operating_expenses_total': '20000.00',
            'monthly_result': '25000.00',
            'latest_closing_accumulated_loss': '5000.00',
          },
        },
        'financial_integrity': {
          'accounting_available': false,
          'note': 'Management reporting only.',
        },
        'data_quality': [],
      };

      final summary = OwnerDashboardSummary.fromJson(dashJson);
      expect(summary.netSalesValue, '150000.00');
      expect(summary.netPurchasesValue, '80000.00');
      expect(summary.lowStockCount, 1);
      expect(summary.grossProfit, '45000.00');
      expect(summary.operatingExpensesTotal, '20000.00');
      expect(summary.monthlyResult, '25000.00');
      expect(summary.closingAccumulatedLoss, '5000.00');
      expect(summary.accountingAvailable, isFalse);
      expect(summary.accountingNote, 'Management reporting only.');

      final period = OwnerCostingPeriodItem.fromJson({
        'id': 3,
        'year': 2026,
        'month': 10,
        'status': 'DRAFT',
        'materials_total': '80.00',
        'labor_total': '10.00',
        'electricity_total': '5.00',
        'water_total': '2.00',
        'other_industrial_total': '3.00',
        'total_production_cost': '100.00',
        'is_complete': true,
        'review_required': false,
        'entry_count': 4,
        'missing_data_notes': [],
      });
      expect(period.materialsTotal, '80.00');
      expect(period.laborTotal, '10.00');
      expect(period.totalProductionCost, '100.00');
      expect(period.entryCount, 4);

      final stock = OwnerStockRowItem.fromJson({
        'product_id': 7,
        'sku': 'RAW-01',
        'name_ar': 'رول ورق',
        'category_code': 'RAW',
        'category_name': 'مواد خام',
        'base_uom': 'كجم',
        'base_uom_name_ar': 'كيلوجرام',
        'quantity_base': '125.500000000000',
        'low_stock_threshold': '100.000000000000',
        'below_threshold': false,
        'active': true,
      });
      expect(stock.productSku, 'RAW-01');
      expect(stock.category, 'مواد خام');
      expect(stock.balance, '125.500000000000');
      expect(stock.baseUnit, 'كجم');
    });
  });
}
