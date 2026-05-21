import 'package:flutter/foundation.dart';
import 'package:auto_apply/services/api_service.dart';

/// Profile state — loads and updates the user profile as a raw Map.
class ProfileProvider extends ChangeNotifier {
  final ApiService _api;
  ProfileProvider(this._api);

  Map<String, dynamic>? _profile;
  bool _loading = false;
  String? _error;

  Map<String, dynamic>? get profile => _profile;
  bool get loading => _loading;
  String? get error => _error;

  // Convenience getters
  Map<String, dynamic> get personal => (_profile?['personal'] as Map<String, dynamic>?) ?? {};
  Map<String, dynamic> get skills => (_profile?['skills'] as Map<String, dynamic>?) ?? {};
  List<String> get technicalSkills => (skills['technical'] as List?)?.cast<String>() ?? [];
  List<dynamic> get experience => (_profile?['experience'] as List?) ?? [];
  List<dynamic> get education => (_profile?['education'] as List?) ?? [];
  Map<String, dynamic> get preferences => (_profile?['preferences'] as Map<String, dynamic>?) ?? {};
  Map<String, dynamic> get documents => (_profile?['documents'] as Map<String, dynamic>?) ?? {};
  String? get resumeUrl => documents['resumeUrl'] as String?;
  int get resumeVersion => documents['resumeVersion'] as int? ?? 0;

  Future<void> loadProfile() async {
    _loading = true;
    _error = null;
    notifyListeners();
    try {
      final resp = await _api.get('/api/v1/profile');
      _profile = Map<String, dynamic>.from(resp.data);
    } catch (e) {
      _error = e.toString();
    }
    _loading = false;
    notifyListeners();
  }

  Future<void> updateProfile(Map<String, dynamic> data) async {
    _loading = true;
    notifyListeners();
    try {
      final resp = await _api.put('/api/v1/profile', data: data);
      _profile = Map<String, dynamic>.from(resp.data);
      _error = null;
    } catch (e) {
      _error = e.toString();
    }
    _loading = false;
    notifyListeners();
  }
}
