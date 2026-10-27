import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../l10n/app_localizations.dart';
import '../models/user_model.dart';
import '../providers/auth_provider.dart';
import 'customer/customer_home_screen.dart';
import 'employee/employee_home_screen.dart';
import 'login_screen.dart';
import 'owner/owner_home_screen.dart';

class SplashScreen extends StatefulWidget {
  const SplashScreen({super.key});

  @override
  State<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends State<SplashScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _checkAuthAndNavigate();
    });
  }

  Future<void> _checkAuthAndNavigate() async {
    final auth = Provider.of<AuthProvider>(context, listen: false);
    await auth.checkAuthStatus();

    if (!mounted) return;

    if (auth.isAuthenticated && auth.currentUser != null) {
      _navigateForRole(auth.currentUser!.role);
    } else {
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const LoginScreen()),
      );
    }
  }

  void _navigateForRole(UserRole role) {
    Widget destination;
    switch (role) {
      case UserRole.owner:
        destination = const OwnerHomeScreen();
        break;
      case UserRole.employee:
        destination = const EmployeeHomeScreen();
        break;
      case UserRole.customer:
        destination = const CustomerHomeScreen();
        break;
      case UserRole.none:
        destination = LoginScreen(
          unauthorizedMessage: context.tr('unauthorizedRole'),
        );
    }

    Navigator.of(context).pushReplacement(
      MaterialPageRoute(builder: (_) => destination),
    );
  }

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.factory, size: 72, color: Color(0xFF1E3A8A)),
            SizedBox(height: 24),
            Text(
              'Manufacturing ERP',
              style: TextStyle(
                fontSize: 22,
                fontWeight: FontWeight.bold,
                color: Color(0xFF0F172A),
              ),
            ),
            SizedBox(height: 12),
            CircularProgressIndicator(),
          ],
        ),
      ),
    );
  }
}
