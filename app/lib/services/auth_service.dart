import 'package:auto_apply/services/api_service.dart';

/// Auth service — signup and login.
class AuthService {
  final ApiService _api;
  AuthService(this._api);

  Future<Map<String, dynamic>> signup(String email, String password, String name) async {
    final resp = await _api.post('/api/v1/auth/signup', data: {
      'email': email,
      'password': password,
      'name': name,
    });
    return Map<String, dynamic>.from(resp.data);
  }

  Future<Map<String, dynamic>> login(String email, String password) async {
    final resp = await _api.post('/api/v1/auth/login', data: {
      'email': email,
      'password': password,
    });
    return Map<String, dynamic>.from(resp.data);
  }

  /// Exchange a Google ID token for our app JWT.
  Future<Map<String, dynamic>> loginWithGoogle(String idToken) async {
    final resp = await _api.post('/api/v1/auth/google', data: {
      'idToken': idToken,
    });
    return Map<String, dynamic>.from(resp.data);
  }
}
