import 'package:flutter/foundation.dart';
import 'package:dio/dio.dart';
// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;
import 'package:auto_apply/services/auth_service.dart';
import 'package:auto_apply/services/api_service.dart';
import 'package:auto_apply/services/google_sign_in_errors.dart';

/// Auth state — signup, login, logout with JWT.
class AuthProvider extends ChangeNotifier {
  final AuthService _authService;
  final ApiService _apiService;

  String? _userId;
  String? _token;
  String? _email;
  String? _name;
  bool _isLoggedIn = false;
  bool _loading = false;
  String? _error;

  AuthProvider(this._authService, this._apiService) {
    // Restore session from stored token
    if (_apiService.hasToken) {
      _isLoggedIn = true;
      _token = 'restored';
      _syncTokenToChromeExtension();
      // Load user info from profile API in background
      _restoreSession();
    }
  }

  /// Push JWT to the Chrome extension (content script on autoapplynow.in).
  void _syncTokenToChromeExtension() {
    if (!kIsWeb) return;
    try {
      final t = html.window.localStorage['auth_token'];
      if (t != null && t.isNotEmpty) {
        html.window.postMessage({'type': 'AUTOAPPLY_SYNC_TOKEN', 'token': t}, '*');
      }
    } catch (_) {}
  }

  Future<void> _restoreSession() async {
    try {
      final resp = await _apiService.get('/api/v1/profile');
      final data = resp.data;
      final personal = data['personal'] as Map<String, dynamic>? ?? {};
      _email = data['email'] as String?;
      _name = '${personal['firstName'] ?? ''} ${personal['lastName'] ?? ''}'.trim();
      _userId = data['userId'] as String?;
      notifyListeners();
    } catch (e) {
      // Token expired or invalid — force logout
      logout();
    }
  }

  String? get userId => _userId;
  String? get token => _token;
  String? get email => _email;
  String? get name => _name;
  bool get isLoggedIn => _isLoggedIn;
  bool get loading => _loading;
  String? get error => _error;

  Future<bool> signup(String email, String password, String name) async {
    _loading = true;
    _error = null;
    notifyListeners();
    try {
      final result = await _authService.signup(email, password, name);
      _userId = result['userId'];
      _token = result['token'];
      _email = result['email'];
      _name = result['name'];
      _isLoggedIn = true;
      _apiService.setToken(_token);
      _loading = false;
      notifyListeners();
      return true;
    } catch (e) {
      final msg = e.toString();
      if (msg.contains('409') || msg.contains('already exists')) {
        _error = 'An account with this email already exists';
      } else if (msg.contains('400') || msg.contains('Validation')) {
        _error = 'Please fill in all fields correctly';
      } else if (msg.contains('connection') || msg.contains('network')) {
        _error = 'Network error. Please check your connection.';
      } else {
        _error = 'Signup failed. Please try again.';
      }
      _loading = false;
      notifyListeners();
      return false;
    }
  }

  Future<bool> login(String email, String password) async {
    _loading = true;
    _error = null;
    notifyListeners();
    try {
      final result = await _authService.login(email, password);
      _userId = result['userId'];
      _token = result['token'];
      _email = result['email'];
      _name = result['name'];
      _isLoggedIn = true;
      _apiService.setToken(_token);
      _loading = false;
      notifyListeners();
      return true;
    } catch (e) {
      final msg = e.toString();
      if (msg.contains('401') || msg.contains('Invalid') || msg.contains('password')) {
        _error = 'Invalid email or password';
      } else if (msg.contains('connection') || msg.contains('network') || msg.contains('XMLHttp')) {
        _error = 'Network error. Please check your connection.';
      } else {
        _error = 'Login failed. Please try again.';
      }
      _loading = false;
      notifyListeners();
      return false;
    }
  }

  Future<bool> loginWithGoogle(String idToken) async {
    _loading = true;
    _error = null;
    notifyListeners();
    try {
      final result = await _authService.loginWithGoogle(idToken);
      _userId = result['userId'];
      _token = result['token'];
      _email = result['email'];
      _name = result['name'];
      _isLoggedIn = true;
      _apiService.setToken(_token);
      _syncTokenToChromeExtension();
      _loading = false;
      notifyListeners();
      return true;
    } catch (e) {
      _error = _googleAuthErrorMessage(e);
      _loading = false;
      notifyListeners();
      return false;
    }
  }

  Future<bool> loginWithGoogleCode({
    required String code,
    required String redirectUri,
    required String codeVerifier,
  }) async {
    _loading = true;
    _error = null;
    notifyListeners();
    try {
      final result = await _authService.loginWithGoogleCode(
        code: code,
        redirectUri: redirectUri,
        codeVerifier: codeVerifier,
      );
      _userId = result['userId'];
      _token = result['token'];
      _email = result['email'];
      _name = result['name'];
      _isLoggedIn = true;
      _apiService.setToken(_token);
      _syncTokenToChromeExtension();
      _loading = false;
      notifyListeners();
      return true;
    } catch (e) {
      _error = _googleAuthErrorMessage(e);
      _loading = false;
      notifyListeners();
      return false;
    }
  }

  String _googleAuthErrorMessage(Object e) {
    if (e is DioException) {
      final data = e.response?.data;
      if (data is Map) {
        final err = data['error'];
        if (err is Map && err['message'] is String) {
          return err['message'] as String;
        }
      }
      if (e.type == DioExceptionType.connectionError ||
          e.type == DioExceptionType.connectionTimeout) {
        return 'Network error. Please check your connection.';
      }
    }
    final msg = formatSignInError(e);
    if (msg.contains('connection') || msg.contains('network') || msg.contains('XMLHttp')) {
      return 'Network error. Please check your connection.';
    }
    return msg.contains('Google sign-in failed')
        ? msg
        : 'Google sign-in failed. Please try again.';
  }

  void logout() {
    _userId = null;
    _token = null;
    _email = null;
    _name = null;
    _isLoggedIn = false;
    _error = null;
    _apiService.setToken(null);
    notifyListeners();
  }

  void clearError() {
    _error = null;
    notifyListeners();
  }
}
