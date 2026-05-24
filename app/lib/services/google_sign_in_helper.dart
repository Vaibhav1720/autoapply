// Stub used on non-web platforms. The real web implementation lives in
// `google_sign_in_helper_web.dart` and is selected via conditional import in
// the consumers below.
import 'google_sign_in_helper_stub.dart'
    if (dart.library.js_interop) 'google_sign_in_helper_web.dart' as impl;

/// Triggers the Google Identity Services sign-in flow and returns the Google
/// ID token (a JWT) on success. Returns null if the user cancels.
/// Throws [UnsupportedError] on non-web platforms.
Future<String?> signInWithGoogle({required String clientId}) =>
    impl.signInWithGoogle(clientId: clientId);

/// On web, completes sign-in after a full-page Google OAuth redirect (mobile).
Future<String?> consumeRedirectOAuthResult() =>
    impl.consumeRedirectOAuthResult();
