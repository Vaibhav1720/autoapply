import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:go_router/go_router.dart';
import 'package:auto_apply/config/azure_config.dart';
import 'package:auto_apply/config/constants.dart';
import 'package:auto_apply/config/theme.dart';
import 'package:auto_apply/providers/auth_provider.dart';
import 'package:auto_apply/screens/main_shell.dart';
import 'package:auto_apply/services/google_sign_in_helper.dart' as google;
import 'package:auto_apply/services/google_sign_in_errors.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  bool _busy = false;

  bool get _embeddedBrowser => kIsWeb && google.isEmbeddedOAuthBrowser();

  @override
  void initState() {
    super.initState();
    if (kIsWeb) {
      WidgetsBinding.instance.addPostFrameCallback((_) async {
        await _completeRedirectSignIn();
        if (!mounted) return;
        google.tryEscapeEmbeddedBrowser();
      });
    }
  }

  /// After full-page Google OAuth, oauth2-redirect.html stores the auth code
  /// in sessionStorage and sends the user back here.
  Future<void> _completeRedirectSignIn() async {
    if (_busy || !kIsWeb) return;
    final result = await google.consumeRedirectOAuthResult();
    if (result == null || !mounted) return;

    if (result.error != null && result.error!.isNotEmpty) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(google.oauthErrorMessage(result.error!))),
        );
      }
      return;
    }

    if (!result.isSuccess ||
        (result.codeVerifier ?? '').isEmpty ||
        (result.redirectUri ?? '').isEmpty) {
      if (mounted && (result.code ?? '').isNotEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text(
              'Sign-in session expired. Please tap Sign in with Google again.'),
        ));
      }
      return;
    }

    setState(() => _busy = true);
    try {
      final auth = context.read<AuthProvider>();
      auth.clearError();
      // Server exchanges the auth code with Google (requires GOOGLE_CLIENT_SECRET on API).
      final ok = await auth.loginWithGoogleCode(
        code: result.code!,
        redirectUri: result.redirectUri!,
        codeVerifier: result.codeVerifier!,
      );
      if (!mounted) return;
      if (ok) {
        context.go('/');
      } else if (auth.error != null) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(auth.error!)),
        );
      }
    } on GoogleSignInException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(e.message)),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(formatSignInError(e))),
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _openInSystemBrowser() async {
    await google.openInSystemBrowser();
  }

  Future<void> _copyLink() async {
    final ok = await google.copyCurrentUrlToClipboard();
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(ok
          ? 'Link copied — paste it in Safari or Chrome.'
          : 'Could not copy. Type autoapplynow.in in Safari or Chrome.'),
    ));
  }

  Future<void> _signInWithGoogle() async {
    if (AzureConfig.googleClientId.startsWith('REPLACE_ME')) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
        content: Text(
            'Google sign-in is not configured. Set googleClientId in azure_config.dart.'),
      ));
      return;
    }
    if (!kIsWeb) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
        content: Text('Google sign-in is currently only wired for the web build.'),
      ));
      return;
    }
    if (_embeddedBrowser) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(
          google.embeddedBrowserWarning() ??
              'Open autoapplynow.in in Safari or Chrome to sign in.',
        ),
        duration: const Duration(seconds: 6),
      ));
      return;
    }
    setState(() => _busy = true);
    try {
      final auth = context.read<AuthProvider>();
      auth.clearError();
      String idToken;
      try {
        idToken = await google.requestGoogleIdToken(
          clientId: AzureConfig.googleClientId,
        );
      } on GoogleSignInException catch (e) {
        if (e.message == 'redirect_started') return;
        if (e.message == 'prompt_not_displayed') {
          await google.signInWithGoogleRedirect(
            clientId: AzureConfig.googleClientId,
          );
          return;
        }
        if (e.message == 'Sign-in was cancelled.') return;
        rethrow;
      }
      final ok = await auth.loginWithGoogle(idToken);
      if (!mounted) return;
      if (ok) {
        context.go('/');
      } else if (auth.error != null) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(auth.error!)),
        );
      }
    } on GoogleSignInException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(e.message)),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(formatSignInError(e))),
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(gradient: AppTheme.backgroundGradient),
        child: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(24),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 420),
                child: Container(
                  padding: const EdgeInsets.all(36),
                  decoration: BoxDecoration(
                    color: AppTheme.surface,
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: AppTheme.border),
                    boxShadow: AppTheme.elevatedShadow,
                  ),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.center,
                    children: [
                      const BrandMark(showWordmark: false),
                      const SizedBox(height: 20),
                      ShaderMask(
                        shaderCallback: (r) =>
                            AppTheme.brandGradient.createShader(r),
                        child: Text(
                          'Welcome to ${AppConstants.appName}',
                          style: TextStyle(
                            fontSize: 24,
                            fontWeight: FontWeight.w800,
                            color: Colors.white,
                            letterSpacing: -0.4,
                          ),
                        ),
                      ),
                      const SizedBox(height: 8),
                      const Text(
                        'AI-matched jobs from 100+ top companies, tailored to your profile.',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          color: AppTheme.textSecondary,
                          fontSize: 14,
                          height: 1.45,
                        ),
                      ),
                      const SizedBox(height: 36),

                      if (_embeddedBrowser) ...[
                        Container(
                          width: double.infinity,
                          padding: const EdgeInsets.all(12),
                          decoration: BoxDecoration(
                            color: AppTheme.error.withValues(alpha: 0.08),
                            borderRadius: BorderRadius.circular(10),
                            border: Border.all(
                                color: AppTheme.error.withValues(alpha: 0.3)),
                          ),
                          child: Text(
                            google.embeddedBrowserWarning() ??
                                google.embeddedBrowserInstructions(),
                            style: const TextStyle(
                              color: AppTheme.error,
                              fontSize: 13,
                              height: 1.4,
                            ),
                          ),
                        ),
                        const SizedBox(height: 12),
                        SizedBox(
                          width: double.infinity,
                          height: 48,
                          child: OutlinedButton.icon(
                            onPressed: _openInSystemBrowser,
                            icon: const Icon(Icons.open_in_browser),
                            label: const Text('Open in Safari / Chrome'),
                          ),
                        ),
                        const SizedBox(height: 8),
                        SizedBox(
                          width: double.infinity,
                          height: 48,
                          child: OutlinedButton.icon(
                            onPressed: _copyLink,
                            icon: const Icon(Icons.link),
                            label: const Text('Copy link'),
                          ),
                        ),
                        const SizedBox(height: 16),
                      ],

                      Consumer<AuthProvider>(builder: (_, auth, __) {
                        if (auth.error != null) {
                          return Padding(
                            padding: const EdgeInsets.only(bottom: 12),
                            child: Text(auth.error!,
                                style: const TextStyle(
                                    color: AppTheme.error, fontSize: 13)),
                          );
                        }
                        return const SizedBox.shrink();
                      }),

                      Consumer<AuthProvider>(builder: (_, auth, __) {
                        final disabled = _busy || auth.loading || _embeddedBrowser;
                        return SizedBox(
                          width: double.infinity,
                          height: 52,
                          child: OutlinedButton.icon(
                            onPressed: disabled ? null : _signInWithGoogle,
                            icon: disabled
                                ? const SizedBox(
                                    width: 20,
                                    height: 20,
                                    child: CircularProgressIndicator(
                                        strokeWidth: 2),
                                  )
                                : const Icon(Icons.login,
                                    color: Colors.redAccent),
                            label: Text(
                              disabled && (_busy || auth.loading)
                                  ? 'Signing in…'
                                  : 'Sign in with Google',
                              style: const TextStyle(fontSize: 15),
                            ),
                            style: OutlinedButton.styleFrom(
                              foregroundColor: Colors.black87,
                              side: const BorderSide(color: Color(0xFFD1D5DB)),
                              backgroundColor: Colors.white,
                            ),
                          ),
                        );
                      }),
                      const SizedBox(height: 18),
                      Text(
                        'We use your Google account just to create your ${AppConstants.appName} profile.',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                            color: AppTheme.textSecondary, fontSize: 12),
                      ),
                      const SizedBox(height: 20),
                      const _LegalFooter(),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _LegalFooter extends StatelessWidget {
  const _LegalFooter();
  @override
  Widget build(BuildContext context) {
    final style = TextStyle(
        fontSize: 11,
        color: Colors.grey.shade500,
        decoration: TextDecoration.underline);
    return Wrap(
      alignment: WrapAlignment.center,
      spacing: 12,
      runSpacing: 4,
      children: [
        GestureDetector(
          onTap: () => context.push('/contact'),
          child: Text('Contact', style: style),
        ),
        GestureDetector(
          onTap: () => context.push('/privacy'),
          child: Text('Privacy Policy', style: style),
        ),
        GestureDetector(
          onTap: () => context.push('/terms'),
          child: Text('Terms & Conditions', style: style),
        ),
        GestureDetector(
          onTap: () => context.push('/refund'),
          child: Text('Refund Policy', style: style),
        ),
      ],
    );
  }
}
