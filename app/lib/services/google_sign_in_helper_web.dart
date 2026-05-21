// Web implementation of the Google Sign-In helper.
//
// Uses the OAuth2 implicit flow in a popup window (response_type=id_token).
// Works reliably regardless of 3rd-party-cookie state, unlike
// google.accounts.id.prompt() (One Tap), which silently no-ops on many
// modern browsers and leaves the UI stuck in "Signing in…".

import 'dart:async';
import 'dart:html' as html;
import 'dart:math' as math;

const _authEndpoint = 'https://accounts.google.com/o/oauth2/v2/auth';

String _randomNonce([int len = 24]) {
  const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  final r = math.Random.secure();
  return List.generate(len, (_) => chars[r.nextInt(chars.length)]).join();
}

Future<String?> signInWithGoogle({required String clientId}) async {
  final completer = Completer<String?>();
  final origin = html.window.location.origin;
  final redirectUri = '$origin/oauth2-redirect.html';
  final nonce = _randomNonce();
  final state = _randomNonce();

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
  final authUrl = '$_authEndpoint?$qs';

  // Popup must be opened during the user gesture (button onPressed).
  final popup = html.window.open(authUrl, 'autoapply_google_oauth',
      'width=480,height=640,menubar=no,toolbar=no,location=no');

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

  // Detect popup closed without callback so the UI doesn't sit forever.
  poll = Timer.periodic(const Duration(milliseconds: 500), (t) {
    if (completer.isCompleted) {
      t.cancel();
      return;
    }
    if (popup.closed ?? true) {
      finish(null);
    }
  });

  // Hard safety timeout — surfaces failures fast (was 120 s).
  hardTimeout = Timer(const Duration(seconds: 30), () => finish(null));

  return completer.future;
}
