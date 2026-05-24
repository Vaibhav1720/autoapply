// Web implementation of the Google Sign-In helper.
//
// Desktop: OAuth implicit flow in a popup (response_type=id_token).
// Mobile / in-app browsers: full-page redirect — Google blocks OAuth inside
// popups and embedded WebViews with Error 403 disallowed_useragent.

import 'dart:async';
import 'dart:convert';
import 'dart:html' as html;
import 'dart:math' as math;

const _authEndpoint = 'https://accounts.google.com/o/oauth2/v2/auth';
const _pendingKey = 'autoapply_google_oauth_pending';
const _resultKey = 'autoapply_google_oauth_result';

String _randomNonce([int len = 24]) {
  const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  final r = math.Random.secure();
  return List.generate(len, (_) => chars[r.nextInt(chars.length)]).join();
}

bool _useRedirectFlow() {
  final ua = html.window.navigator.userAgent.toLowerCase();
  // iOS (all browsers use WebKit — popups are treated as insecure WebViews).
  if (RegExp(r'iphone|ipad|ipod').hasMatch(ua)) return true;
  if (ua.contains('crios') || ua.contains('fxios')) return true;
  // Android phone browsers.
  if (ua.contains('android') && ua.contains('mobile')) return true;
  // Social / in-app browsers (Instagram, Facebook, LinkedIn, etc.).
  if (RegExp(r'fbav|fban|instagram|linkedinapp|twitter|line/').hasMatch(ua)) {
    return true;
  }
  // Installed PWA (Add to Home Screen).
  try {
    if (html.window.matchMedia('(display-mode: standalone)').matches) {
      return true;
    }
  } catch (_) {}
  return false;
}

String _authUrl({
  required String clientId,
  required String redirectUri,
  required String nonce,
  required String state,
}) {
  final params = <String, String>{
    'client_id': clientId,
    'response_type': 'id_token',
    'scope': 'openid email profile',
    'redirect_uri': redirectUri,
    'nonce': nonce,
    'state': state,
    'prompt': 'select_account',
  };
  final qs = params.entries
      .map((e) => '${Uri.encodeComponent(e.key)}=${Uri.encodeComponent(e.value)}')
      .join('&');
  return '$_authEndpoint?$qs';
}

void _startRedirectFlow(String authUrl, String state, String nonce) {
  html.window.sessionStorage[_pendingKey] = jsonEncode({
    'state': state,
    'nonce': nonce,
  });
  html.window.location.assign(authUrl);
}

/// After a full-page OAuth redirect, read and clear the stored id_token.
/// Returns null if there is no pending result or the user cancelled / errored.
Future<String?> consumeRedirectOAuthResult() async {
  final raw = html.window.sessionStorage[_resultKey];
  if (raw == null || raw.isEmpty) return null;
  html.window.sessionStorage.remove(_resultKey);
  html.window.sessionStorage.remove(_pendingKey);

  Map<String, dynamic> data;
  try {
    data = jsonDecode(raw) as Map<String, dynamic>;
  } catch (_) {
    return null;
  }
  final err = (data['error'] as String?)?.trim();
  if (err != null && err.isNotEmpty) return null;
  final token = (data['id_token'] as String?)?.trim();
  if (token == null || token.isEmpty) return null;
  return token;
}

Future<String?> signInWithGoogle({required String clientId}) async {
  final origin = html.window.location.origin;
  final redirectUri = '$origin/oauth2-redirect.html';
  final nonce = _randomNonce();
  final state = _randomNonce();
  final url = _authUrl(
    clientId: clientId,
    redirectUri: redirectUri,
    nonce: nonce,
    state: state,
  );

  if (_useRedirectFlow()) {
    _startRedirectFlow(url, state, nonce);
    // Page navigates away — this future never completes.
    return Completer<String?>().future;
  }

  final completer = Completer<String?>();

  // Popup must be opened during the user gesture (button onPressed).
  final popup = html.window.open(url, 'autoapply_google_oauth',
      'width=480,height=640,menubar=no,toolbar=no,location=no');

  // Popup blocked — fall back to full-page redirect.
  var popupBlocked = false;
  try {
    popupBlocked = popup.closed!;
  } catch (_) {
    popupBlocked = true;
  }
  if (popupBlocked) {
    _startRedirectFlow(url, state, nonce);
    return Completer<String?>().future;
  }

  late StreamSubscription sub;
  Timer? poll;
  Timer? hardTimeout;

  void finish(String? token) {
    if (completer.isCompleted) return;
    sub.cancel();
    poll?.cancel();
    hardTimeout?.cancel();
    completer.complete(token);
  }

  sub = html.window.onMessage.listen((evt) {
    if (evt.origin != origin) return;
    final data = evt.data;
    if (data is! Map) return;
    if (data['source'] != 'autoapply-google-oauth') return;
    final hash = (data['hash'] as String?) ?? '';
    final cleaned = hash.startsWith('#') ? hash.substring(1) : hash;
    final fragParams = Uri.splitQueryString(cleaned);
    if (fragParams['state'] != state) {
      finish(null);
      return;
    }
    finish(fragParams['id_token']);
  });

  poll = Timer.periodic(const Duration(milliseconds: 500), (t) {
    if (completer.isCompleted) {
      t.cancel();
      return;
    }
    try {
      if (popup.closed ?? true) {
        finish(null);
      }
    } catch (_) {
      finish(null);
    }
  });

  hardTimeout = Timer(const Duration(seconds: 30), () => finish(null));

  return completer.future;
}
