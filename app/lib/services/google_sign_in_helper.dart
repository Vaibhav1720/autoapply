import 'google_oauth_types.dart';
import 'google_sign_in_helper_stub.dart'
    if (dart.library.js_interop) 'google_sign_in_helper_web.dart' as impl;

export 'google_oauth_types.dart' show GoogleOAuthRedirectResult;

Future<String> requestGoogleIdToken({required String clientId}) =>
    impl.requestGoogleIdToken(clientId: clientId);

Future<void> signInWithGoogleRedirect({required String clientId}) =>
    impl.signInWithGoogleRedirect(clientId: clientId);

/// Starts Google OAuth. On web, navigates away (returns incomplete future).
Future<String?> signInWithGoogle({required String clientId}) =>
    impl.signInWithGoogle(clientId: clientId);
Future<GoogleOAuthRedirectResult?> consumeRedirectOAuthResult() =>
    impl.consumeRedirectOAuthResult();

Future<String> exchangeCodeForIdToken({
  required String code,
  required String redirectUri,
  required String codeVerifier,
  required String clientId,
}) =>
    impl.exchangeCodeForIdToken(
      code: code,
      redirectUri: redirectUri,
      codeVerifier: codeVerifier,
      clientId: clientId,
    );

bool isEmbeddedOAuthBrowser() => impl.isEmbeddedOAuthBrowser();

String? embeddedBrowserWarning() => impl.embeddedBrowserWarning();

bool tryEscapeEmbeddedBrowser() => impl.tryEscapeEmbeddedBrowser();

bool hasPendingOAuthResult() => impl.hasPendingOAuthResult();

String oauthErrorMessage(String error) => impl.oauthErrorMessage(error);

Future<bool> copyCurrentUrlToClipboard() => impl.copyCurrentUrlToClipboard();

String embeddedBrowserInstructions() => impl.embeddedBrowserInstructions();

Future<bool> openInSystemBrowser() => impl.openInSystemBrowser();
