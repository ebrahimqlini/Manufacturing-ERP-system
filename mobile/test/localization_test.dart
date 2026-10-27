import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:paper_factory_mobile/l10n/app_localizations.dart';

void main() {
  group('Localization and RTL / LTR Tests', () {
    test('Arabic localization provides RTL flag and translated keys', () {
      final l10n = AppLocalizations(const Locale('ar'));

      expect(l10n.isRtl, isTrue);
      expect(l10n.translate('appName'), 'نظام تخطيط موارد مصنع الورق');
      expect(l10n.translate('login'), 'تسجيل الدخول');
      expect(l10n.translate('roleOwner'), 'إدارة المصنع (المالك)');
      expect(l10n.translate('roleEmployee'), 'فريق العمليات التشغيلية');
      expect(l10n.translate('productionOrders'), 'أوامر الإنتاج');
      expect(l10n.translate('materialWaste'), 'هالك المواد الخام المقاس');
      expect(l10n.translate('confirmZeroWaste'), 'تأكيد عدم وجود هالك خامات (صفر)');
    });

    test('English localization provides LTR flag and translated keys', () {
      final l10n = AppLocalizations(const Locale('en'));

      expect(l10n.isRtl, isFalse);
      expect(l10n.translate('appName'), 'Paper Factory ERP');
      expect(l10n.translate('login'), 'Login');
      expect(l10n.translate('roleOwner'), 'Factory Management (Owner)');
      expect(l10n.translate('roleEmployee'), 'Operational Staff');
      expect(l10n.translate('productionOrders'), 'Production Orders');
      expect(l10n.translate('materialWaste'), 'Measured Material Waste');
      expect(l10n.translate('confirmZeroWaste'), 'Confirm Zero Raw Material Waste');
    });

    test('Fallback to key for missing translations', () {
      final l10n = AppLocalizations(const Locale('en'));
      expect(l10n.translate('non_existent_key_xyz'), 'non_existent_key_xyz');
    });
  });
}
