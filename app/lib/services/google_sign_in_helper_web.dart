// Web Google Sign-In — authorization code + PKCE, full-page redirect only.
//
// Google blocks OAuth in embedded WebViews / popups (403 disallowed_useragent).
// Implicit id_token flows are also unreliable on mobile. We always redirect the
// full browser tab to Google, then exchange the code on our API.

import 'dart:async';
import 'dart:convert';
import 'dart:html' as html;
import 'dart:js_util' as js_util;
import 'dart:math' as math;

import 'package:auto_apply/services/google_oauth_types.dart';
import 'package:auto_apply/services/google_sign_in_errors.dart';
import 'package:crypto/crypto.dart';

export 'google_oauth_types.dart' show GoogleOAuthRedirectResult;

const _authEndpoint = 'https://accounts.google.com/o/oauth2/v2/auth';
const _pendingKey = 'autoapply_google_oauth_pending';
const _resultKey = 'autoapply_google_oauth_result';

String _randomString([int len = 48]) {
  const chars =
      'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~';
  final r = math.Random.secure();
  return List.generate(len, (_) => chars[r.nextInt(chars.length)]).join();
}

String _pkceChallenge(String verifier) {
  final digest = sha256.convert(utf8.encode(verifier));
  return base64Url.encode(digest.bytes).replaceAll('=', '');
}

/// True when Google OAuth will fail (in-app browser, Android WebView, iOS PWA).
bool isEmbeddedOAuthBrowser() {
  final ua = html.window.navigator.userAgent;
  if (RegExp(
    r'FBAN|FBAV|Instagram|Line/|LinkedInApp|\[LinkedInApp\]|LinkedIn/|Twitter|GSA/|TikTok|BytedanceWebview|Snapchat|MicroMessenger|WeChat|Slack|Discord|Blind/|TeamBlind|teamblind',
    caseSensitive: false,
  ).hasMatch(ua)) {
    return true;
  }
  if (ua.contains('Android') && ua.toLowerCase().contains('wv')) {
    return true;
  }
  // iOS in-app browsers often lack "Safari/" in the UA unlike Mobile Safari.
  if (RegExp(r'iPhone|iPad|iPod', caseSensitive: false).hasMatch(ua) &&
      !RegExp(r'Safari/', caseSensitive: false).hasMatch(ua) &&
      RegExp(r'AppleWebKit/', caseSensitive: false).hasMatch(ua)) {
    return true;
  }
  try {
    if (html.window.matchMedia('(display-mode: standalone)').matches) {
      return true;
    }
  } catch (_) {}
  return false;
}

bool _isIos() {
  final ua = html.window.navigator.userAgent;
  return RegExp(r'iPhone|iPad|iPod', caseSensitive: false).hasMatch(ua);
}

/// Auto-redirect from known embedded browsers (e.g. LinkedIn iOS) to system Safari/Chrome.
/// Returns true when a redirect was triggered (page is unloading).
bool tryEscapeEmbeddedBrowser() {
  if (hasPendingOAuthResult()) return false;
  if (!isEmbeddedOAuthBrowser()) return false;
  final href = html.window.location.href;
  if (_isIos()) {
    // iOS 17+ opens the URL in Safari (works for LinkedIn in-app browser).
    html.window.location.assign('x-safari-$href');
    return true;
  }
  if (html.window.navigator.userAgent.contains('Android')) {
    final uri = Uri.parse(href);
    final path = '${uri.path}${uri.hasQuery ? '?${uri.query}' : ''}';
    html.window.location.assign(
      'intent://${uri.host}$path#Intent;scheme=${uri.scheme};package=com.android.chrome;end',
    );
    return true;
  }
  return false;
}

String embeddedBrowserInstructions() {
  if (_isIos()) {
    return 'Tap ⋯ (top right) → Open in Safari, or use "Copy link" below and paste in Safari.';
  }
  if (html.window.navigator.userAgent.contains('Android')) {
    return 'Tap ⋮ (top right) → Open in Chrome, or use "Copy link" below and paste in Chrome.';
  }
  return 'Open autoapplynow.in in Safari or Chrome, then sign in.';
}

Future<bool> copyCurrentUrlToClipboard() async {
  try {
    await html.window.navigator.clipboard?.writeText(html.window.location.href);
    return true;
  } catch (_) {
    return false;
  }
}

String? embeddedBrowserWarning() {
  if (!isEmbeddedOAuthBrowser()) return null;
  return 'Google sign-in is blocked in this in-app browser. ${embeddedBrowserInstructions()}';
}

String _canonicalOrigin() {
  var host = html.window.location.hostname ?? 'autoapplynow.in';
  if (host.startsWith('www.')) {
    host = host.substring(4);
  }
  return 'https://$host';
}

String _redirectUri() => '${_canonicalOrigin()}/oauth2-redirect.html';

void _storePending(Map<String, String> pending) {
  final raw = jsonEncode(pending);
  html.window.sessionStorage[_pendingKey] = raw;
  html.window.localStorage[_pendingKey] = raw;
}

void _clearPending() {
  html.window.sessionStorage.remove(_pendingKey);
  html.window.localStorage.remove(_pendingKey);
}

Map<String, dynamic>? _readPending() {
  for (final storage in [html.window.sessionStorage, html.window.localStorage]) {
    final raw = storage[_pendingKey];
    if (raw == null || raw.isEmpty) continue;
    try {
      return jsonDecode(raw) as Map<String, dynamic>;
    } catch (_) {}
  }
  return null;
}

/// True when returning from Google OAuth callback (complete sign-in first).
bool hasPendingOAuthResult() {
  final raw = html.window.sessionStorage[_resultKey];
  return raw != null && raw.isNotEmpty;
}

String oauthErrorMessage(String error) {
  switch (error) {
    case 'access_denied':
      return 'Sign-in was cancelled.';
    case 'session_lost':
    case 'state_mismatch':
      return 'Sign-in session expired. Please tap Sign in with Google again.';
    case 'missing_code':
      return 'Google did not return a sign-in code. Please try again.';
    default:
      return 'Google sign-in failed: $error';
  }
}

String _authUrl({
  required String clientId,
  required String redirectUri,
  required String state,
  required String codeChallenge,
}) {
  final params = <String, String>{
    'client_id': clientId,
    'response_type': 'code',
    'scope': 'openid email profile',
    'redirect_uri': redirectUri,
    'state': state,
    'code_challenge': codeChallenge,
    'code_challenge_method': 'S256',
    'prompt': 'select_account',
    'access_type': 'online',
  };
  final qs = params.entries
      .map((e) =>
          '${Uri.encodeComponent(e.key)}=${Uri.encodeComponent(e.value)}')
      .join('&');
  return '$_authEndpoint?$qs';
}

void _startRedirectFlow(String authUrl, String state, String codeVerifier) {
  _storePending({
    'state': state,
    'codeVerifier': codeVerifier,
    'redirectUri': _redirectUri(),
  });
  html.window.location.assign(authUrl);
}

Future<GoogleOAuthRedirectResult?> consumeRedirectOAuthResult() async {
  final raw = html.window.sessionStorage[_resultKey];
  if (raw == null || raw.isEmpty) return null;
  html.window.sessionStorage.remove(_resultKey);

  Map<String, dynamic> data;
  try {
    data = jsonDecode(raw) as Map<String, dynamic>;
  } catch (_) {
    _clearPending();
    return null;
  }

  final pending = _readPending();
  _clearPending();

  final err = (data['error'] as String?)?.trim();
  if (err != null && err.isNotEmpty) {
    return GoogleOAuthRedirectResult(error: err);
  }

  final code = (data['code'] as String?)?.trim();
  if (code == null || code.isEmpty) return null;

  final redirectUri = (data['redirectUri'] as String?)?.trim() ??
      (pending?['redirectUri'] as String?) ??
      _redirectUri();
  final codeVerifier = (data['codeVerifier'] as String?)?.trim() ??
      pending?['codeVerifier'] as String?;

  return GoogleOAuthRedirectResult(
    code: code,
    redirectUri: redirectUri,
    codeVerifier: codeVerifier,
  );
}

/// Exchange PKCE auth code for ID token in the browser (no client secret needed).
Future<String> exchangeCodeForIdToken({
  required String code,
  required String redirectUri,
  required String codeVerifier,
  required String clientId,
}) async {
  final params = <String, String>{
    'code': code,
    'client_id': clientId,
    'redirect_uri': redirectUri,
    'grant_type': 'authorization_code',
    'code_verifier': codeVerifier,
  };
  final body = params.entries
      .map((e) =>
          '${Uri.encodeComponent(e.key)}=${Uri.encodeComponent(e.value)}')
      .join('&');

  late html.HttpRequest req;
  try {
    req = await html.HttpRequest.request(
      'https://oauth2.googleapis.com/token',
      method: 'POST',
      sendData: body,
      requestHeaders: {'Content-Type': 'application/x-www-form-urlencoded'},
    );
  } catch (e) {
    throw GoogleSignInException(
      'Could not reach Google from this browser. Open autoapplynow.in in Safari or '
      'Chrome (not LinkedIn/Blind in-app browser) and try again.',
    );
  }

  if (req.status == 0) {
    throw GoogleSignInException(
      'Could not reach Google from this browser. Open autoapplynow.in in Safari or '
      'Chrome and try again.',
    );
  }

  if (req.status != 200) {
    var detail = req.responseText ?? '';
    try {
      final err = jsonDecode(detail) as Map<String, dynamic>;
      detail = (err['error_description'] ?? err['error'] ?? detail).toString();
    } catch (_) {}
    if (detail.contains('client_secret is missing')) {
      throw GoogleSignInException(
        'Google sign-in must complete on the server. Ask the site admin to set '
        'GOOGLE_CLIENT_SECRET on the API.',
      );
    }
    throw GoogleSignInException(
      detail.isNotEmpty ? detail : 'Google token exchange failed (${req.status})',
    );
  }

  final tokens = jsonDecode(req.responseText!) as Map<String, dynamic>;
  final idToken = (tokens['id_token'] as String?)?.trim();
  if (idToken == null || idToken.isEmpty) {
    throw GoogleSignInException('Google did not return an ID token');
  }
  return idToken;
}

bool _isJsFunction(Object? value) {
  if (value == null) return false;
  try {
    return js_util.getProperty(value, 'call') != null;
  } catch (_) {
    return false;
  }
}

Object? _readGoogleSignInCredentialFn() {
  try {
    final fn = js_util.getProperty(
      js_util.globalThis,
      'autoapplyGoogleSignInCredential',
    );
    if (_isJsFunction(fn)) return fn;
  } catch (_) {}
  return null;
}

Future<Object?> _invokeGoogleSignInCredential(
  Object fn,
  String clientId,
) {
  final promise = js_util.callMethod(
    fn,
    'call',
    [js_util.globalThis, clientId],
  );
  return js_util.promiseToFuture<Object?>(promise);
}

Future<Object?> _waitForGoogleSignInBridge({
  Duration timeout = const Duration(seconds: 15),
  Duration interval = const Duration(milliseconds: 100),
}) async {
  final deadline = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(deadline)) {
    final fn = _readGoogleSignInCredentialFn();
    if (fn != null) return fn;
    await Future<void>.delayed(interval);
  }
  return null;
}

GoogleSignInException _parseGsiJsError(Object error) {
  final raw = error.toString();
  try {
    final start = raw.indexOf('{');
    if (start >= 0) {
      final map = jsonDecode(raw.substring(start)) as Map<String, dynamic>;
      final code = map['code']?.toString() ?? '';
      final message = map['message']?.toString() ?? code;
      if (code == 'cancelled') {
        return GoogleSignInException('Sign-in was cancelled.');
      }
      if (code == 'prompt_not_displayed' || code == 'prompt_skipped') {
        return GoogleSignInException('prompt_not_displayed');
      }
      return GoogleSignInException(message.isNotEmpty ? message : 'Google sign-in failed.');
    }
  } catch (_) {}
  return GoogleSignInException(formatSignInError(error));
}

/// Google Identity Services — ID token without client secret (preferred on web).
Future<String> requestGoogleIdToken({required String clientId}) async {
  if (isEmbeddedOAuthBrowser()) {
    throw GoogleSignInException(
      embeddedBrowserWarning() ?? 'Open autoapplynow.in in Safari or Chrome.',
    );
  }
  final fn = await _waitForGoogleSignInBridge();
  if (fn == null) {
    // Stale index.html or GIS script blocked — full-page OAuth still works.
    await signInWithGoogleRedirect(clientId: clientId);
    throw GoogleSignInException('redirect_started');
  }
  try {
    final cred = await _invokeGoogleSignInCredential(fn, clientId);
    if (cred is String && cred.isNotEmpty) return cred;
    throw GoogleSignInException('Google did not return a sign-in token.');
  } catch (e) {
    if (e is GoogleSignInException) rethrow;
    final raw = e.toString();
    if (raw.contains('is not a function') || raw.contains('NoSuchMethodError')) {
      await signInWithGoogleRedirect(clientId: clientId);
      throw GoogleSignInException('redirect_started');
    }
    throw _parseGsiJsError(e);
  }
}

/// Full-page OAuth redirect (fallback when GIS prompt is unavailable).
Future<void> signInWithGoogleRedirect({required String clientId}) async {
  if (isEmbeddedOAuthBrowser()) {
    throw GoogleSignInException(
      embeddedBrowserWarning() ?? 'Use Safari or Chrome',
    );
  }

  final redirectUri = _redirectUri();
  final state = _randomString(32);
  final codeVerifier = _randomString(64);
  final url = _authUrl(
    clientId: clientId,
    redirectUri: redirectUri,
    state: state,
    codeChallenge: _pkceChallenge(codeVerifier),
  );

  _startRedirectFlow(url, state, codeVerifier);
}

@Deprecated('Use requestGoogleIdToken or signInWithGoogleRedirect')
Future<String?> signInWithGoogle({required String clientId}) async {
  signInWithGoogleRedirect(clientId: clientId);
  return Completer<String?>().future;
}

/// Open the current page in the system browser (escape in-app / PWA shell).
Future<bool> openInSystemBrowser() async {
  final href = html.window.location.href;
  if (_isIos()) {
    html.window.location.assign('x-safari-$href');
    return true;
  }
  if (html.window.navigator.userAgent.contains('Android')) {
    final uri = Uri.parse(href);
    final intent =
        'intent://${uri.host}${uri.path}${uri.hasQuery ? '?${uri.query}' : ''}'
        '#Intent;scheme=${uri.scheme};package=com.android.chrome;end';
    html.window.location.assign(intent);
    return true;
  }
  html.window.open(href, '_blank');
  return true;
}
