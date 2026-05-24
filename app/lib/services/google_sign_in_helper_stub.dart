// Non-web stub. Kept here so the conditional import in
// `google_sign_in_helper.dart` always resolves on mobile/desktop builds.
Future<String?> signInWithGoogle({required String clientId}) {
  throw UnsupportedError('Google Sign-In is currently only wired for web.');
}

Future<String?> consumeRedirectOAuthResult() async => null;
