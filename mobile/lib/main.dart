import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:provider/provider.dart';

import 'config/app_theme.dart';
import 'l10n/app_localizations.dart';
import 'providers/auth_provider.dart';
import 'providers/employee_provider.dart';
import 'providers/locale_provider.dart';
import 'screens/splash_screen.dart';
import 'services/api_client.dart';
import 'services/auth_service.dart';
import 'services/customer_service.dart';
import 'services/employee_service.dart';
import 'services/owner_service.dart';
import 'services/storage_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final storageService = await StorageService.init();
  final apiClient = ApiClient(storageService: storageService);
  final authService = AuthService(apiClient: apiClient, storageService: storageService);
  final employeeService = EmployeeService(apiClient);
  final ownerService = OwnerService(apiClient);
  final customerService = CustomerService(apiClient);

  runApp(
    MultiProvider(
      providers: [
        Provider<StorageService>.value(value: storageService),
        Provider<ApiClient>.value(value: apiClient),
        Provider<AuthService>.value(value: authService),
        Provider<EmployeeService>.value(value: employeeService),
        Provider<OwnerService>.value(value: ownerService),
        Provider<CustomerService>.value(value: customerService),
        ChangeNotifierProvider<LocaleProvider>(
          create: (_) => LocaleProvider(storageService),
        ),
        ChangeNotifierProvider<AuthProvider>(
          create: (_) => AuthProvider(authService),
        ),
        ChangeNotifierProvider<EmployeeProvider>(
          create: (_) => EmployeeProvider(employeeService),
        ),
      ],
      child: const PaperFactoryApp(),
    ),
  );
}

class PaperFactoryApp extends StatelessWidget {
  const PaperFactoryApp({super.key});

  @override
  Widget build(BuildContext context) {
    final localeProvider = Provider.of<LocaleProvider>(context);

    return MaterialApp(
      title: 'Manufacturing ERP',
      debugShowCheckedModeBanner: false,
      locale: localeProvider.locale,
      supportedLocales: AppLocalizations.supportedLocales,
      localizationsDelegates: const [
        AppLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      theme: AppTheme.lightTheme(localeProvider.locale.languageCode),
      home: const SplashScreen(),
    );
  }
}
