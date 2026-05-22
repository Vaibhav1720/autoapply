import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:go_router/go_router.dart';
import 'package:auto_apply/config/constants.dart';
import 'package:auto_apply/config/theme.dart';
import 'package:auto_apply/providers/auth_provider.dart';

class SignupScreen extends StatefulWidget {
  const SignupScreen({super.key});

  @override
  State<SignupScreen> createState() => _SignupScreenState();
}

class _SignupScreenState extends State<SignupScreen> {
  final _nameCtrl = TextEditingController();
  final _emailCtrl = TextEditingController();
  final _passCtrl = TextEditingController();
  final _formKey = GlobalKey<FormState>();
  bool _obscure = true;

  @override
  void dispose() {
    _nameCtrl.dispose();
    _emailCtrl.dispose();
    _passCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(32),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 400),
              child: Form(
                key: _formKey,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(Icons.person_add_rounded, size: 64, color: AppTheme.primary),
                    const SizedBox(height: 16),
                    const Text('Create Account',
                        style: TextStyle(fontSize: 28, fontWeight: FontWeight.bold)),
                    const SizedBox(height: 4),
                    Text('Join ${AppConstants.appName} to discover matching jobs',
                        style: TextStyle(color: AppTheme.textSecondary)),
                    const SizedBox(height: 40),

                    TextFormField(
                      controller: _nameCtrl,
                      textCapitalization: TextCapitalization.words,
                      decoration: const InputDecoration(
                        labelText: 'Full Name',
                        prefixIcon: Icon(Icons.person_outlined),
                        border: OutlineInputBorder(),
                      ),
                      validator: (v) =>
                          v == null || v.trim().isEmpty ? 'Enter your name' : null,
                    ),
                    const SizedBox(height: 16),

                    TextFormField(
                      controller: _emailCtrl,
                      keyboardType: TextInputType.emailAddress,
                      decoration: const InputDecoration(
                        labelText: 'Email',
                        prefixIcon: Icon(Icons.email_outlined),
                        border: OutlineInputBorder(),
                      ),
                      validator: (v) =>
                          v == null || !v.contains('@') ? 'Enter a valid email' : null,
                    ),
                    const SizedBox(height: 16),

                    TextFormField(
                      controller: _passCtrl,
                      obscureText: _obscure,
                      decoration: InputDecoration(
                        labelText: 'Password',
                        prefixIcon: const Icon(Icons.lock_outlined),
                        border: const OutlineInputBorder(),
                        suffixIcon: IconButton(
                          icon: Icon(_obscure ? Icons.visibility : Icons.visibility_off),
                          onPressed: () => setState(() => _obscure = !_obscure),
                        ),
                      ),
                      validator: (v) =>
                          v == null || v.length < 6 ? 'Min 6 characters' : null,
                    ),
                    const SizedBox(height: 8),

                    Consumer<AuthProvider>(builder: (_, auth, __) {
                      if (auth.error != null) {
                        return Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: Text(auth.error!,
                              style: const TextStyle(color: AppTheme.error, fontSize: 13)),
                        );
                      }
                      return const SizedBox.shrink();
                    }),

                    const SizedBox(height: 16),

                    Consumer<AuthProvider>(builder: (_, auth, __) {
                      return SizedBox(
                        width: double.infinity,
                        height: 48,
                        child: ElevatedButton(
                          onPressed: auth.loading ? null : _signup,
                          child: auth.loading
                              ? const SizedBox(
                                  width: 20, height: 20,
                                  child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                              : const Text('Sign Up', style: TextStyle(fontSize: 16)),
                        ),
                      );
                    }),

                    const SizedBox(height: 16),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        const Text('Already have an account? '),
                        TextButton(
                          onPressed: () => context.go('/login'),
                          child: const Text('Login'),
                        ),
                      ],
                    ),
                    const SizedBox(height: 16),
                    _LegalFooter(),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Future<void> _signup() async {
    if (!_formKey.currentState!.validate()) return;
    final auth = context.read<AuthProvider>();
    auth.clearError();
    final ok = await auth.signup(
        _emailCtrl.text.trim(), _passCtrl.text, _nameCtrl.text.trim());
    if (ok && mounted) {
      context.go('/');
    }
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
