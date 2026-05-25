/// Azure configuration constants for the AutoApply app.
class AzureConfig {
  AzureConfig._();

  // Azure AD B2C
  static const String b2cTenant = ''; // e.g., 'autoapplyb2c'
  static const String b2cClientId = '';
  static const String b2cPolicySignUpSignIn = 'B2C_1_signupsignin';
  static const String b2cPolicyPasswordReset = 'B2C_1_passwordreset';

  // API — set via --dart-define=API_BASE_URL at build time, or replace the default below.
  static const String apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'https://autoapplynow.in',
  );

  // Blob Storage
  static const String blobBaseUrl = '';

  /// Google OAuth Web client ID. Used by Google Identity Services on Flutter
  /// web to render the Sign-In button. Replace with the Client ID created in
  /// Google Cloud Console (must list this app's origin under Authorized
  /// JavaScript origins). Set via --dart-define=GOOGLE_CLIENT_ID.
  static const String googleClientId = String.fromEnvironment(
    'GOOGLE_CLIENT_ID',
    defaultValue: '8017795829-np8cfekibbnfr960rfo6fqj6g8kl8dm1.apps.googleusercontent.com',
  );
}
