import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../config/api_config.dart';
import '../l10n/app_localizations.dart';
import '../providers/auth_provider.dart';
import '../providers/locale_provider.dart';
import '../services/storage_service.dart';
import 'login_screen.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _urlController = TextEditingController();

  @override
  void initState() {
    super.initState();
    final storage = Provider.of<StorageService>(context, listen: false);
    _urlController.text = storage.getBaseUrl();
  }

  @override
  void dispose() {
    _urlController.dispose();
    super.dispose();
  }

  Future<void> _saveUrl() async {
    final raw = _urlController.text.trim();
    final uri = Uri.tryParse(raw);
    if (uri == null ||
        !uri.hasScheme ||
        uri.host.isEmpty ||
        (uri.scheme != 'http' && uri.scheme != 'https')) {
      _showMessage(context.tr('invalidServerUrl'), isError: true);
      return;
    }
    if (kReleaseMode && uri.scheme != 'https') {
      _showMessage(context.tr('httpsRequired'), isError: true);
      return;
    }

    final storage = Provider.of<StorageService>(context, listen: false);
    await storage.saveBaseUrl(raw);
    if (!mounted) return;
    _showMessage(context.tr('success'));
  }

  void _showMessage(String message, {bool isError = false}) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: isError ? Colors.red.shade700 : Colors.green.shade700,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final localeProvider = Provider.of<LocaleProvider>(context);
    final authProvider = Provider.of<AuthProvider>(context);

    return Scaffold(
      appBar: AppBar(
        title: Text(context.tr('settings')),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // Language Setting Section
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const Icon(Icons.language, color: Color(0xFF1E3A8A)),
                      const SizedBox(width: 8),
                      Text(
                        context.tr('language'),
                        style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Radio<String>(
                      value: 'ar',
                      groupValue: localeProvider.locale.languageCode,
                      onChanged: (val) {
                        if (val != null) localeProvider.setLocale(Locale(val));
                      },
                    ),
                    title: const Text('العربية (RTL)'),
                    onTap: () => localeProvider.setLocale(const Locale('ar')),
                  ),
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Radio<String>(
                      value: 'en',
                      groupValue: localeProvider.locale.languageCode,
                      onChanged: (val) {
                        if (val != null) localeProvider.setLocale(Locale(val));
                      },
                    ),
                    title: const Text('English (LTR)'),
                    onTap: () => localeProvider.setLocale(const Locale('en')),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),

          // API Base URL Setting Section
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const Icon(Icons.dns_outlined, color: Color(0xFF1E3A8A)),
                      const SizedBox(width: 8),
                      Text(
                        context.tr('serverUrl'),
                        style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _urlController,
                    decoration: InputDecoration(
                      hintText: ApiConfig.defaultEmulatorBaseUrl,
                      suffixIcon: IconButton(
                        icon: const Icon(Icons.check),
                        onPressed: _saveUrl,
                      ),
                    ),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    context.tr('presets'),
                    style: const TextStyle(fontSize: 12, color: Colors.grey),
                  ),
                  const SizedBox(height: 6),
                  Wrap(
                    spacing: 8,
                    children: ApiConfig.presets.map((preset) {
                      return ActionChip(
                        label: Text(
                          localeProvider.isArabic ? preset.nameAr : preset.nameEn,
                          style: const TextStyle(fontSize: 11),
                        ),
                        onPressed: () {
                          setState(() {
                            _urlController.text = preset.url;
                          });
                          _saveUrl();
                        },
                      );
                    }).toList(),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 24),

          // Logout Action if authenticated
          if (authProvider.isAuthenticated)
            ElevatedButton.icon(
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.red.shade700,
                foregroundColor: Colors.white,
              ),
              icon: const Icon(Icons.logout),
              label: Text(context.tr('logout')),
              onPressed: () async {
                await authProvider.logout();
                if (!context.mounted) return;
                Navigator.of(context).pushAndRemoveUntil(
                  MaterialPageRoute(builder: (_) => const LoginScreen()),
                  (route) => false,
                );
              },
            ),
        ],
      ),
    );
  }
}
