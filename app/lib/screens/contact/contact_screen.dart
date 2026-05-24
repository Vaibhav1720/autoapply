/// Contact / Support screen.
///
/// - Accessible without login (public route /contact).
/// - Logged-in users submit via POST /api/v1/feedback (rich category tracking).
/// - Guest users submit via POST /api/v1/contact (public endpoint).
/// - Direct email link always visible as fallback.
library;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:go_router/go_router.dart';
// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;

import 'package:auto_apply/config/theme.dart';
import 'package:auto_apply/services/api_service.dart';

const _kSupportEmail = 'techvibeapps.ai@gmail.com';

class ContactScreen extends StatefulWidget {
  const ContactScreen({super.key});
  @override
  State<ContactScreen> createState() => _ContactScreenState();
}

class _ContactScreenState extends State<ContactScreen> {
  final _formKey = GlobalKey<FormState>();
  final _nameCtrl = TextEditingController();
  final _emailCtrl = TextEditingController();
  final _subjectCtrl = TextEditingController();
  final _msgCtrl = TextEditingController();
  String _category = 'general';
  bool _busy = false;
  bool _sent = false;
  String? _error;

  static const _categories = [
    ('general', 'General enquiry'),
    ('billing', 'Billing & payments'),
    ('bug', 'Bug report'),
    ('feature', 'Feature request'),
    ('refund', 'Refund request'),
    ('other', 'Other'),
  ];

  @override
  void dispose() {
    _nameCtrl.dispose();
    _emailCtrl.dispose();
    _subjectCtrl.dispose();
    _msgCtrl.dispose();
    super.dispose();
  }

  bool get _isLoggedIn {
    final token = html.window.localStorage['auth_token'];
    return token != null && token.isNotEmpty;
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final api = context.read<ApiService>();
      if (_isLoggedIn) {
        // Logged-in: use the authenticated feedback endpoint (includes user_id)
        await api.post('/api/v1/feedback', data: {
          'text': _msgCtrl.text.trim(),
          'category': _category,
          'page': 'contact',
        });
      } else {
        // Guest: use the public contact endpoint
        await api.post('/api/v1/contact', data: {
          'name': _nameCtrl.text.trim(),
          'email': _emailCtrl.text.trim(),
          'subject': _subjectCtrl.text.trim().isNotEmpty
              ? _subjectCtrl.text.trim()
              : _categories.firstWhere((c) => c.$1 == _category).$2,
          'message': _msgCtrl.text.trim(),
        });
      }
      if (mounted) setState(() => _sent = true);
    } catch (e) {
      if (mounted) {
        setState(() => _error =
            'Could not send your message. Please email us directly at $_kSupportEmail.');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Contact & Support'),
        leading: BackButton(onPressed: () {
          if (context.canPop()) {
            context.pop();
          } else {
            context.go(_isLoggedIn ? '/profile' : '/login');
          }
        }),
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 28),
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 700),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _ContactHeader(),
                const SizedBox(height: 28),
                _QuickContactRow(),
                const SizedBox(height: 32),
                _sent ? _SuccessBanner() : _buildForm(),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildForm() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Send us a message',
          style: Theme.of(context).textTheme.titleMedium?.copyWith(
                fontWeight: FontWeight.w700,
                color: AppTheme.primary,
              ),
        ),
        const SizedBox(height: 4),
        Text(
          _isLoggedIn
              ? 'We\'ll respond to your registered email within 2 business days.'
              : 'Fill in the form below and we\'ll get back to you within 2 business days.',
          style: TextStyle(fontSize: 13, color: Colors.grey.shade600),
        ),
        const SizedBox(height: 16),
        Form(
          key: _formKey,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Category selector
              const Text('Category', style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
              const SizedBox(height: 6),
              Wrap(
                spacing: 8,
                runSpacing: 6,
                children: [
                  for (final (id, label) in _categories)
                    ChoiceChip(
                      label: Text(label, style: const TextStyle(fontSize: 12)),
                      selected: _category == id,
                      onSelected: (_) => setState(() => _category = id),
                      selectedColor: AppTheme.primarySoft,
                      checkmarkColor: AppTheme.primary,
                    ),
                ],
              ),
              const SizedBox(height: 16),

              // Name + email — guests only
              if (!_isLoggedIn) ...[
                Row(children: [
                  Expanded(
                    child: TextFormField(
                      controller: _nameCtrl,
                      decoration: const InputDecoration(
                        labelText: 'Your name',
                        prefixIcon: Icon(Icons.person_outlined),
                        border: OutlineInputBorder(),
                        isDense: true,
                      ),
                      validator: (v) =>
                          (v ?? '').trim().isEmpty ? 'Enter your name' : null,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: TextFormField(
                      controller: _emailCtrl,
                      keyboardType: TextInputType.emailAddress,
                      decoration: const InputDecoration(
                        labelText: 'Your email',
                        prefixIcon: Icon(Icons.email_outlined),
                        border: OutlineInputBorder(),
                        isDense: true,
                      ),
                      validator: (v) {
                        final s = (v ?? '').trim();
                        if (s.isEmpty) return 'Enter your email';
                        if (!s.contains('@')) return 'Invalid email';
                        return null;
                      },
                    ),
                  ),
                ]),
                const SizedBox(height: 12),
                TextFormField(
                  controller: _subjectCtrl,
                  decoration: const InputDecoration(
                    labelText: 'Subject (optional)',
                    prefixIcon: Icon(Icons.subject),
                    border: OutlineInputBorder(),
                    isDense: true,
                  ),
                ),
                const SizedBox(height: 12),
              ],

              // Message
              TextFormField(
                controller: _msgCtrl,
                maxLines: 6,
                decoration: const InputDecoration(
                  labelText: 'Your message',
                  alignLabelWithHint: true,
                  border: OutlineInputBorder(),
                  hintText: 'Describe your issue or question in detail…',
                ),
                validator: (v) {
                  final s = (v ?? '').trim();
                  if (s.isEmpty) return 'Please write a message';
                  if (s.length < 10) return 'Message is too short';
                  return null;
                },
              ),
              const SizedBox(height: 6),
              Text(
                'For billing issues, include your registered email and payment reference.',
                style: TextStyle(fontSize: 11, color: Colors.grey.shade500),
              ),

              if (_error != null) ...[
                const SizedBox(height: 12),
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: AppTheme.error.withValues(alpha: 0.08),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: AppTheme.error.withValues(alpha: 0.3)),
                  ),
                  child: Row(children: [
                    const Icon(Icons.error_outline, color: AppTheme.error, size: 16),
                    const SizedBox(width: 8),
                    Expanded(
                        child: Text(_error!,
                            style: const TextStyle(
                                color: AppTheme.error, fontSize: 13))),
                  ]),
                ),
              ],

              const SizedBox(height: 20),
              SizedBox(
                width: double.infinity,
                height: 48,
                child: FilledButton.icon(
                  onPressed: _busy ? null : _submit,
                  icon: _busy
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(
                              strokeWidth: 2, color: Colors.white))
                      : const Icon(Icons.send_rounded, size: 18),
                  label: Text(
                      _busy ? 'Sending\u2026' : 'Send message',
                      style: const TextStyle(fontWeight: FontWeight.w600)),
                  style: FilledButton.styleFrom(backgroundColor: AppTheme.primary),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

// ── Sub-widgets ───────────────────────────────────────────────────────────

class _ContactHeader extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(
              gradient: const LinearGradient(
                  colors: [Color(0xFF6366f1), Color(0xFF8b5cf6)]),
              borderRadius: BorderRadius.circular(12),
            ),
            child: const Icon(Icons.headset_mic_rounded,
                color: Colors.white, size: 22),
          ),
          const SizedBox(width: 12),
          Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(
              'Contact & Support',
              style: Theme.of(context)
                  .textTheme
                  .headlineSmall
                  ?.copyWith(fontWeight: FontWeight.w800),
            ),
            const Text(
              'We\u2019re here to help — typical response within 2 business days.',
              style: TextStyle(fontSize: 13, color: AppTheme.textSecondary),
            ),
          ]),
        ]),
      ],
    );
  }
}

class _QuickContactRow extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: _InfoCard(
            icon: Icons.email_rounded,
            title: 'Email us directly',
            body: _kSupportEmail,
            onTap: () => html.window.open(
                'mailto:$_kSupportEmail?subject=HirePanda%20Support', '_blank'),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: _InfoCard(
            icon: Icons.feedback_rounded,
            title: 'In-app feedback',
            body: 'Log in and use the feedback form in the Discover tab.',
            onTap: null,
          ),
        ),
      ],
    );
  }
}

class _InfoCard extends StatelessWidget {
  final IconData icon;
  final String title;
  final String body;
  final VoidCallback? onTap;
  const _InfoCard(
      {required this.icon,
      required this.title,
      required this.body,
      required this.onTap});

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(14),
      child: Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: AppTheme.primarySoft,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: AppTheme.primary.withValues(alpha: 0.18)),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(icon, color: AppTheme.primary, size: 22),
          const SizedBox(height: 8),
          Text(title,
              style: const TextStyle(
                  fontWeight: FontWeight.w700,
                  fontSize: 13,
                  color: AppTheme.primary)),
          const SizedBox(height: 4),
          Text(body,
              style: TextStyle(
                  fontSize: 12,
                  color: Colors.grey.shade700,
                  height: 1.4,
                  decoration: onTap != null
                      ? TextDecoration.underline
                      : TextDecoration.none)),
        ]),
      ),
    );
  }
}

class _SuccessBanner extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: AppTheme.success.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppTheme.success.withValues(alpha: 0.3)),
      ),
      child: Column(
        children: [
          const Icon(Icons.check_circle_rounded,
              color: AppTheme.success, size: 48),
          const SizedBox(height: 12),
          const Text(
            'Message sent!',
            style: TextStyle(
                fontSize: 20,
                fontWeight: FontWeight.w800,
                color: AppTheme.success),
          ),
          const SizedBox(height: 8),
          const Text(
            'Thanks for reaching out. We\'ll respond to your email within '
            '2 business days. For urgent billing issues, email us directly at:',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 13, height: 1.5),
          ),
          const SizedBox(height: 8),
          GestureDetector(
            onTap: () => html.window
                .open('mailto:$_kSupportEmail', '_blank'),
            child: const Text(
              _kSupportEmail,
              style: TextStyle(
                  fontWeight: FontWeight.w700,
                  color: AppTheme.primary,
                  decoration: TextDecoration.underline),
            ),
          ),
        ],
      ),
    );
  }
}
