import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:paper_factory_mobile/l10n/app_localizations.dart';
import 'package:paper_factory_mobile/providers/auth_provider.dart';
import 'package:paper_factory_mobile/providers/locale_provider.dart';
import 'package:paper_factory_mobile/screens/login_screen.dart';
import 'package:paper_factory_mobile/services/api_client.dart';
import 'package:paper_factory_mobile/services/auth_service.dart';
import 'package:paper_factory_mobile/services/storage_service.dart';
import 'package:paper_factory_mobile/services/token_store.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  testWidgets('LoginScreen renders fields and login button in Arabic RTL', (WidgetTester tester) async {
    SharedPreferences.setMockInitialValues({'app_language': 'ar'});
    final storageService = await StorageService.init(
      tokenStore: InMemoryTokenStore(),
    );
    final apiClient = ApiClient(storageService: storageService);
    final authService = AuthService(apiClient: apiClient, storageService: storageService);

    await tester.pumpWidget(
      MultiProvider(
        providers: [
          Provider<StorageService>.value(value: storageService),
          ChangeNotifierProvider<LocaleProvider>(create: (_) => LocaleProvider(storageService)),
          ChangeNotifierProvider<AuthProvider>(create: (_) => AuthProvider(authService)),
        ],
        child: MaterialApp(
          locale: const Locale('ar'),
          supportedLocales: AppLocalizations.supportedLocales,
          localizationsDelegates: const [
            AppLocalizations.delegate,
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
          ],
          home: const LoginScreen(),
        ),
      ),
    );

    await tester.pumpAndSettle();

    // Verify Arabic text elements are rendered
    expect(find.text('تسجيل الدخول'), findsWidgets);
    expect(find.byType(TextFormField), findsNWidgets(2));
    expect(find.byType(ElevatedButton), findsOneWidget);
  });
}
