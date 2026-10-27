enum UserRole {
  owner,
  employee,
  customer,
  none,
}

class CurrentUser {
  final String username;
  final String firstName;
  final String lastName;
  final String email;
  final bool isSuperuser;
  final bool isStaff;
  final bool isOwner;
  final bool isEmployee;
  final bool isCustomer;
  final UserRole role;
  final int? customerId;
  final String? customerCode;
  final String? customerNameAr;
  final List<String> permissions;

  const CurrentUser({
    required this.username,
    required this.firstName,
    required this.lastName,
    required this.email,
    required this.isSuperuser,
    required this.isStaff,
    required this.isOwner,
    required this.isEmployee,
    required this.isCustomer,
    required this.role,
    this.customerId,
    this.customerCode,
    this.customerNameAr,
    required this.permissions,
  });

  factory CurrentUser.fromJson(Map<String, dynamic> json) {
    final String roleStr = (json['role'] as String? ?? 'NONE').toUpperCase();
    UserRole parsedRole;
    switch (roleStr) {
      case 'OWNER':
        parsedRole = UserRole.owner;
        break;
      case 'EMPLOYEE':
        parsedRole = UserRole.employee;
        break;
      case 'CUSTOMER':
        parsedRole = UserRole.customer;
        break;
      default:
        parsedRole = UserRole.none;
    }

    final rawPerms = json['permissions'];
    final List<String> permsList = rawPerms is List
        ? rawPerms.map((e) => e.toString()).toList()
        : <String>[];

    return CurrentUser(
      username: json['username'] as String? ?? '',
      firstName: json['first_name'] as String? ?? '',
      lastName: json['last_name'] as String? ?? '',
      email: json['email'] as String? ?? '',
      isSuperuser: json['is_superuser'] as bool? ?? false,
      isStaff: json['is_staff'] as bool? ?? false,
      isOwner: json['is_owner'] as bool? ?? false,
      isEmployee: json['is_employee'] as bool? ?? false,
      isCustomer: json['is_customer'] as bool? ?? false,
      role: parsedRole,
      customerId: json['customer_id'] as int?,
      customerCode: json['customer_code'] as String?,
      customerNameAr: json['customer_name_ar'] as String?,
      permissions: permsList,
    );
  }

  String get displayName {
    if (firstName.isNotEmpty || lastName.isNotEmpty) {
      return '$firstName $lastName'.trim();
    }
    return username;
  }
}
