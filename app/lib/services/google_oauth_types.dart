/// Result of a full-page Google OAuth redirect (PKCE code flow).
class GoogleOAuthRedirectResult {
  final String? code;
  final String? redirectUri;
  final String? codeVerifier;
  final String? error;

  const GoogleOAuthRedirectResult({
    this.code,
    this.redirectUri,
    this.codeVerifier,
    this.error,
  });

  bool get isSuccess => code != null && code!.isNotEmpty && error == null;
}
