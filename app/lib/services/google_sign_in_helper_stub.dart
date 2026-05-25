import 'package:auto_apply/services/google_oauth_types.dart';

export 'google_oauth_types.dart' show GoogleOAuthRedirectResult;

Future<String> requestGoogleIdToken({required String clientId}) {
  throw UnsupportedError('Google Sign-In is only wired for web.');
}

Future<void> signInWithGoogleRedirect({required String clientId}) async {
  throw UnsupportedError('Google Sign-In is only wired for web.');
}

Future<String?> signInWithGoogle({required String clientId}) async {
  throw UnsupportedError('Google Sign-In is only wired for web.');
}

Future<GoogleOAuthRedirectResult?> consumeRedirectOAuthResult() async => null;

bool isEmbeddedOAuthBrowser() => false;

String? embeddedBrowserWarning() => null;

bool hasPendingOAuthResult() => false;

String oauthErrorMessage(String error) => 'Google sign-in failed: $error';

bool tryEscapeEmbeddedBrowser() => false;

Future<bool> copyCurrentUrlToClipboard() async => false;

String embeddedBrowserInstructions() =>
    'Open autoapplynow.in in Safari or Chrome, then sign in.';

Future<String> exchangeCodeForIdToken({
  required String code,
  required String redirectUri,
  required String codeVerifier,
  required String clientId,
}) {
  throw UnsupportedError('Google Sign-In is only wired for web.');
}

Future<bool> openInSystemBrowser() async => false;
