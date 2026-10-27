import 'package:flutter/material.dart';
import '../services/storage_service.dart';

class LocaleProvider extends ChangeNotifier {
  final StorageService _storageService;
  late Locale _locale;

  LocaleProvider(this._storageService) {
    _locale = Locale(_storageService.getLanguage());
  }

  Locale get locale => _locale;
  bool get isArabic => _locale.languageCode == 'ar';

  Future<void> setLocale(Locale newLocale) async {
    if (!['ar', 'en'].contains(newLocale.languageCode)) return;
    _locale = newLocale;
    await _storageService.saveLanguage(newLocale.languageCode);
    notifyListeners();
  }

  Future<void> toggleLocale() async {
    final nextCode = isArabic ? 'en' : 'ar';
    await setLocale(Locale(nextCode));
  }
}
