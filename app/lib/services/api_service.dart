import 'dart:convert';
import 'dart:typed_data';
// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;
import 'package:dio/dio.dart';
import 'package:auto_apply/config/azure_config.dart';

/// Base HTTP client for API communication.
class ApiService {
  late final Dio _dio;
  String? _token;

  ApiService() {
    // Restore token from localStorage on startup
    _token = html.window.localStorage['auth_token'];

    _dio = Dio(
      BaseOptions(
        baseUrl: AzureConfig.apiBaseUrl,
        connectTimeout: const Duration(seconds: 30),
        // No receive timeout: discover results stream in per-company, and
        // the user explicitly asked that slow companies should still show
        // their results whenever they finish, not be killed by a deadline.
        // Setting Duration.zero disables Dio's receive timeout entirely.
        // Per-request CancelTokens still allow user-initiated stop or F5.
        receiveTimeout: Duration.zero,
        sendTimeout: const Duration(seconds: 30),
        headers: {'Content-Type': 'application/json'},
      ),
    );

    _dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) {
          if (_token != null) {
            options.headers['Authorization'] = 'Bearer $_token';
          }
          return handler.next(options);
        },
        onError: (error, handler) {
          return handler.next(error);
        },
      ),
    );
  }

  void setToken(String? token) {
    _token = token;
    if (token != null) {
      html.window.localStorage['auth_token'] = token;
    } else {
      html.window.localStorage.remove('auth_token');
    }
  }

  bool get hasToken => _token != null;

  Future<Response> get(String path,
          {Map<String, dynamic>? queryParameters,
          CancelToken? cancelToken}) =>
      _dio.get(path,
          queryParameters: queryParameters, cancelToken: cancelToken);

  Future<Response> post(String path,
          {dynamic data, CancelToken? cancelToken, Options? options}) =>
      _dio.post(path, data: data, cancelToken: cancelToken, options: options);

  Future<Response> put(String path, {dynamic data}) =>
      _dio.put(path, data: data);

  Future<Response> delete(String path) => _dio.delete(path);

  Future<Response> deleteWithData(String path, {dynamic data}) =>
      _dio.delete(path, data: data);

  Future<Response> postRaw(String path, List<int> bytes, String contentType) =>
      _dio.post(path,
          data: Stream.fromIterable([bytes]),
          options: Options(
            contentType: contentType,
            headers: {'Content-Length': bytes.length},
          ));

  Future<Response> postBytes(String path, List<int> bytes) {
    // Send as base64-encoded JSON for Flutter web compatibility
    final b64 = base64Encode(Uint8List.fromList(bytes));
    return _dio.post(path, data: {'fileBase64': b64, 'fileName': 'resume.pdf'});
  }

  Future<Response> postForm(String path, FormData formData) =>
      _dio.post(path, data: formData);
}
