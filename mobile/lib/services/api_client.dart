import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import '../config/api_config.dart';
import 'storage_service.dart';

class ApiException implements Exception {
  final int statusCode;
  final String message;
  final dynamic details;

  ApiException({
    required this.statusCode,
    required this.message,
    this.details,
  });

  @override
  String toString() => 'ApiException($statusCode): $message';
}

class ApiClient {
  final StorageService storageService;
  final http.Client _httpClient;

  ApiClient({
    required this.storageService,
    http.Client? httpClient,
  })  : _httpClient = httpClient ?? http.Client();

  String get _baseUrl {
    String base = storageService.getBaseUrl().trim();
    while (base.endsWith('/')) {
      base = base.substring(0, base.length - 1);
    }
    if (!base.endsWith(ApiConfig.apiVersion)) {
      base = '$base${ApiConfig.apiVersion}';
    }
    if (kReleaseMode && Uri.tryParse(base)?.scheme != 'https') {
      throw ApiException(
        statusCode: 0,
        message: 'A secure HTTPS API address is required in release builds.',
      );
    }
    return base;
  }

  Map<String, String> _buildHeaders({bool authenticated = true}) {
    final headers = <String, String>{
      'Content-Type': 'application/json; charset=UTF-8',
      'Accept': 'application/json',
    };
    if (authenticated) {
      final token = storageService.getToken();
      if (token != null && token.isNotEmpty) {
        headers['Authorization'] = 'Token $token';
      }
    }
    return headers;
  }

  Future<dynamic> get(String endpoint, {Map<String, String>? queryParams, bool authenticated = true}) async {
    final uri = Uri.parse('$_baseUrl$endpoint').replace(queryParameters: queryParams);
    try {
      final response = await _httpClient
          .get(uri, headers: _buildHeaders(authenticated: authenticated))
          .timeout(ApiConfig.connectTimeout);
      return _processResponse(response);
    } on SocketException catch (e) {
      throw ApiException(statusCode: 0, message: 'تعذر الاتصال بالخادم: $e');
    } on TimeoutException {
      throw ApiException(statusCode: 408, message: 'انتهت مهلة انتظار استجابة الخادم.');
    }
  }

  Future<dynamic> post(String endpoint, {Map<String, dynamic>? body, bool authenticated = true}) async {
    final uri = Uri.parse('$_baseUrl$endpoint');
    try {
      final response = await _httpClient
          .post(
            uri,
            headers: _buildHeaders(authenticated: authenticated),
            body: body != null ? jsonEncode(body) : null,
          )
          .timeout(ApiConfig.connectTimeout);
      return _processResponse(response);
    } on SocketException catch (e) {
      throw ApiException(statusCode: 0, message: 'تعذر الاتصال بالخادم: $e');
    } on TimeoutException {
      throw ApiException(statusCode: 408, message: 'انتهت مهلة انتظار استجابة الخادم.');
    }
  }

  dynamic _processResponse(http.Response response) {
    if (response.statusCode == 204) {
      return null;
    }

    dynamic decoded;
    if (response.body.isNotEmpty) {
      try {
        decoded = jsonDecode(utf8.decode(response.bodyBytes));
      } catch (_) {
        decoded = response.body;
      }
    }

    if (response.statusCode >= 200 && response.statusCode < 300) {
      return decoded;
    }

    // Extract error message from DRF format or generic envelope
    String errorMessage = 'حدث خطأ في معالجة الطلب.';
    dynamic details = decoded;

    if (decoded is Map<String, dynamic>) {
      if (decoded.containsKey('error') && decoded['error'] is Map) {
        final errObj = decoded['error'] as Map<String, dynamic>;
        details = errObj['detail'] ?? errObj;
        errorMessage = _extractDetailMessage(details);
      } else if (decoded.containsKey('detail')) {
        errorMessage = decoded['detail'].toString();
      } else {
        errorMessage = _extractDetailMessage(decoded);
      }
    }

    throw ApiException(
      statusCode: response.statusCode,
      message: errorMessage,
      details: details,
    );
  }

  String _extractDetailMessage(dynamic detail) {
    if (detail is String) return detail;
    if (detail is List && detail.isNotEmpty) return detail.first.toString();
    if (detail is Map) {
      final entries = detail.entries.map((e) {
        final val = e.value is List ? (e.value as List).join(', ') : e.value.toString();
        return '${e.key}: $val';
      });
      return entries.join('\n');
    }
    return detail.toString();
  }
}
