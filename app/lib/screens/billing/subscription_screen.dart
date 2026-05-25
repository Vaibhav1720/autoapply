// Subscription / account screen. Shows the user's current plan, billing
// status, renewal date, and lets them open the Lemon Squeezy customer
// portal (manage card, view invoices) or cancel.

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:go_router/go_router.dart';
// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;

import 'package:auto_apply/config/theme.dart';
import 'package:auto_apply/providers/profile_provider.dart';
import 'package:auto_apply/services/api_service.dart';
import 'package:auto_apply/utils/subscription_access.dart';

class SubscriptionScreen extends StatefulWidget {
  const SubscriptionScreen({super.key});

  @override
  State<SubscriptionScreen> createState() => _SubscriptionScreenState();
}

class _SubscriptionScreenState extends State<SubscriptionScreen> {
  bool _loading = true;
  bool _busy = false;
  String? _error;
  Map<String, dynamic> _sub = const {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final api = context.read<ApiService>();
      try {
        await context.read<ProfileProvider>().loadProfile();
      } catch (_) {}
      // Cache-bust so browser/CDN never serves a stale subscription snapshot
      final resp = await api.get(
        '/api/v1/billing/subscription',
        queryParameters: {'_': DateTime.now().millisecondsSinceEpoch.toString()},
      );
      if (!mounted) return;
      setState(() {
        _sub = resp.data is Map ? Map<String, dynamic>.from(resp.data) : const {};
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  String get _provider => (_sub['provider'] ?? 'lemonsqueezy').toString();
  String get _paymentType => (_sub['paymentType'] ?? 'recurring').toString();
  bool get _isRazorpay => _provider == 'razorpay';
  bool get _isOneTime => _paymentType == 'one_time';

  String _cancelledHint(Map<String, dynamic> sub) {
    final ends = (sub['endsAt'] ?? '').toString();
    final renews = (sub['renewsAt'] ?? '').toString();
    final raw = ends.isNotEmpty ? ends : renews;
    if (raw.isEmpty) {
      return 'Your subscription is cancelled. Pro stays active until the end of your paid period — subscribe again to keep it after that.';
    }
    try {
      final dt = DateTime.parse(raw).toLocal();
      final label =
          '${dt.year}-${dt.month.toString().padLeft(2, '0')}-${dt.day.toString().padLeft(2, '0')}';
      return 'Your subscription is cancelled. Pro stays active until $label — subscribe again to keep it after that.';
    } catch (_) {
      return 'Your subscription is cancelled. Pro stays active until $raw — subscribe again to keep it after that.';
    }
  }

  Future<void> _openPortal() async {
    if (_busy) return;

    // One-time Razorpay — no portal needed
    if (_isRazorpay && _isOneTime) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text(
            'One-time payment — no auto-renewal. No billing portal is needed.',
          ),
          duration: Duration(seconds: 4),
        ),
      );
      return;
    }

    setState(() => _busy = true);
    try {
      // Use portal URL already returned by GET /subscription (from webhook).
      // Avoids an extra API round-trip and works when LS API is slow.
      var url = (_sub['manageUrl'] ?? '').toString().trim();

      if (url.isEmpty) {
        final api = context.read<ApiService>();
        final resp = await api.get(
          '/api/v1/billing/portal',
          queryParameters: {'_': DateTime.now().millisecondsSinceEpoch.toString()},
        );
        final notApplicable = resp.data?['notApplicable'] == true;
        final message = (resp.data?['message'] ?? '').toString();
        url = (resp.data?['url'] ?? '').toString().trim();

        if (notApplicable) {
          if (!mounted) return;
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: Text(message.isNotEmpty
                  ? message
                  : 'No billing management needed for one-time payments.'),
              duration: const Duration(seconds: 4),
            ),
          );
          return;
        }
      }

      if (url.isEmpty) {
        throw Exception(
          'Portal link not ready yet. Refresh this page in a minute and try again.',
        );
      }
      html.window.open(url, '_blank');
    } catch (e) {
      if (!mounted) return;
      final msg = e.toString();
      final friendly = msg.contains('502') || msg.contains('503')
          ? 'Billing portal is temporarily unavailable. Please try again shortly.'
          : 'Could not open portal: $e';
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(friendly), backgroundColor: AppTheme.error),
      );
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _cancel() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Cancel subscription?'),
        content: const Text(
          'You\'ll keep Pro access until the end of your current billing '
          'period. After that you\'ll move back to the Free plan. You can '
          'resubscribe anytime.',
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Keep my plan')),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              style: FilledButton.styleFrom(backgroundColor: AppTheme.error),
              child: const Text('Cancel anyway')),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _busy = true);
    try {
      final api = context.read<ApiService>();
      final resp = await api.post('/api/v1/billing/cancel');
      if (!mounted) return;
      setState(() {
        _sub = resp.data is Map ? Map<String, dynamic>.from(resp.data) : _sub;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Subscription cancelled')),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
            content: Text('Cancel failed: $e'),
            backgroundColor: AppTheme.error),
      );
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final pp = context.watch<ProfileProvider>();
    final tier = (_sub['tier'] ?? 'free').toString().toLowerCase();
    final status = (_sub['status'] ?? '').toString();
    final isPro = pp.isPremium || isPremiumTier(tier);
    final cancelled = status.toLowerCase() == 'cancelled';

    return Scaffold(
      appBar: AppBar(
        title: const Text('Subscription'),
        leading: BackButton(onPressed: () => context.go('/profile')),
      ),
      body: SafeArea(
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? _ErrorView(message: _error!, onRetry: _load)
                : SingleChildScrollView(
                    padding: const EdgeInsets.all(20),
                    child: Center(
                      child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 720),
                        child: Column(
                          children: [
                            _PlanCard(
                                isPro: isPro, sub: _sub, cancelled: cancelled),
                            const SizedBox(height: 16),
                            if (isPro) ...[
                              if (cancelled) ...[
                                SizedBox(
                                  width: double.infinity,
                                  height: 48,
                                  child: FilledButton.icon(
                                    onPressed: _busy
                                        ? null
                                        : () => context.go('/pricing'),
                                    icon: const Icon(Icons.autorenew_rounded),
                                    label: const Text('Subscribe again'),
                                    style: FilledButton.styleFrom(
                                      backgroundColor: AppTheme.primary,
                                    ),
                                  ),
                                ),
                                const SizedBox(height: 12),
                                Text(
                                  _cancelledHint(_sub),
                                  textAlign: TextAlign.center,
                                  style: TextStyle(
                                    fontSize: 13,
                                    color: Colors.grey.shade700,
                                    height: 1.4,
                                  ),
                                ),
                                const SizedBox(height: 12),
                              ],
                              // Portal tile — show for all except one-time Razorpay
                              if (!(_isRazorpay && _isOneTime))
                                _ActionTile(
                                  icon: Icons.credit_card_rounded,
                                  title: _isRazorpay
                                      ? 'Manage subscription (Razorpay Dashboard)'
                                      : 'Manage payment method & invoices',
                                  subtitle: _isRazorpay
                                      ? 'Opens the Razorpay Dashboard in a new tab'
                                      : 'Opens the Lemon Squeezy self-service portal in a new tab',
                                  onTap: _busy ? null : _openPortal,
                                ),
                              if (_isRazorpay && _isOneTime) ...[
                                const SizedBox(height: 4),
                                Container(
                                  padding: const EdgeInsets.symmetric(
                                      horizontal: 16, vertical: 12),
                                  decoration: BoxDecoration(
                                    color: AppTheme.primarySoft,
                                    borderRadius: BorderRadius.circular(12),
                                    border: Border.all(
                                        color: AppTheme.primary
                                            .withValues(alpha: 0.18)),
                                  ),
                                  child: Row(children: [
                                    const Icon(Icons.info_outline,
                                        color: AppTheme.primary, size: 20),
                                    const SizedBox(width: 10),
                                    Expanded(
                                      child: Text(
                                        'One-time payment — no auto-renewal. Your Pro access is valid until the expiry date above.',
                                        style: TextStyle(
                                            fontSize: 13,
                                            color: Colors.grey.shade700),
                                      ),
                                    ),
                                  ]),
                                ),
                              ],
                              const SizedBox(height: 8),
                              if (!cancelled && !_isOneTime)
                                _ActionTile(
                                  icon: Icons.cancel_outlined,
                                  iconColor: AppTheme.error,
                                  title: 'Cancel subscription',
                                  subtitle:
                                      'Keep Pro until the end of the current period, then move to Free',
                                  onTap: _busy ? null : _cancel,
                                ),
                            ] else ...[
                              SizedBox(
                                width: double.infinity,
                                height: 48,
                                child: FilledButton.icon(
                                  onPressed: () => context.go('/pricing'),
                                  icon: const Icon(Icons.workspace_premium_rounded),
                                  label: const Text('See plans \u2014 Upgrade to Pro'),
                                  style: FilledButton.styleFrom(
                                      backgroundColor: AppTheme.primary),
                                ),
                              ),
                            ],
                          ],
                        ),
                      ),
                    ),
                  ),
      ),
    );
  }
}

class _PlanCard extends StatelessWidget {
  final bool isPro;
  final bool cancelled;
  final Map<String, dynamic> sub;
  const _PlanCard(
      {required this.isPro, required this.sub, required this.cancelled});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: isPro
              ? [AppTheme.primary, AppTheme.secondary]
              : [Colors.grey.shade600, Colors.grey.shade800],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(20),
      ),
      padding: const EdgeInsets.all(24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                isPro
                    ? Icons.workspace_premium_rounded
                    : Icons.lock_outline_rounded,
                color: Colors.white,
                size: 32,
              ),
              const SizedBox(width: 12),
              Text(
                isPro ? 'ApplyRight Pro' : 'Free plan',
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 26,
                  fontWeight: FontWeight.w800,
                ),
              ),
            ],
          ),
          if (isPro) ...[
            const SizedBox(height: 16),
            _row('Status', _statusLabel()),
            if ((sub['interval'] ?? '').toString().isNotEmpty)
              _row('Billing', _billingPeriodLabel(sub)),
            if ((sub['priceInr'] ?? '').toString().isNotEmpty)
              _row('Price', '₹${sub['priceInr']}')
            else if ((sub['priceUsd'] ?? '').toString().isNotEmpty)
              _row('Price', '\$${sub['priceUsd']}'),
            if (cancelled) ...[
              if (_accessUntil(sub).isNotEmpty)
                _row('Access until', _fmt(_accessUntil(sub))),
            ] else if ((sub['renewsAt'] ?? '').toString().isNotEmpty)
              _row((sub['paymentType'] ?? '') == 'one_time' ? 'Expires on' : 'Next renewal',
                  _fmt(sub['renewsAt'])),
          ] else ...[
            const SizedBox(height: 12),
            const Text(
              'Upgrade to remove daily limits and unlock advanced resume tailoring.',
              style: TextStyle(color: Colors.white70, fontSize: 14),
            ),
          ],
        ],
      ),
    );
  }

  String _accessUntil(Map<String, dynamic> sub) {
    final ends = (sub['endsAt'] ?? '').toString();
    if (ends.isNotEmpty) return ends;
    return (sub['renewsAt'] ?? '').toString();
  }

  String _statusLabel() {
    final s = (sub['status'] ?? '').toString();
    if (s.isEmpty) return 'Active';
    return s[0].toUpperCase() + s.substring(1);
  }

  String _billingPeriodLabel(Map<String, dynamic> sub) {
    final interval = (sub['interval'] ?? '').toString().toLowerCase();
    final period = switch (interval) {
      'week' => 'Weekly',
      'year' => 'Yearly',
      'month' => 'Monthly',
      _ => interval.isEmpty ? '' : '${interval[0].toUpperCase()}${interval.substring(1)}',
    };
    if ((sub['paymentType'] ?? '') == 'one_time') {
      return period.isEmpty ? 'One-time' : '$period · one-time';
    }
    return period;
  }

  String _fmt(dynamic v) {
    try {
      final dt = DateTime.parse(v.toString()).toLocal();
      return '${dt.year}-${dt.month.toString().padLeft(2, '0')}-${dt.day.toString().padLeft(2, '0')}';
    } catch (_) {
      return v.toString();
    }
  }

  Widget _row(String k, String v) => Padding(
        padding: const EdgeInsets.only(top: 6),
        child: Row(
          children: [
            SizedBox(
              width: 130,
              child: Text(k, style: const TextStyle(color: Colors.white70)),
            ),
            Expanded(
              child: Text(v,
                  style: const TextStyle(
                      color: Colors.white, fontWeight: FontWeight.w600)),
            ),
          ],
        ),
      );
}

class _ActionTile extends StatelessWidget {
  final IconData icon;
  final Color? iconColor;
  final String title;
  final String subtitle;
  final VoidCallback? onTap;
  const _ActionTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.onTap,
    this.iconColor,
  });
  @override
  Widget build(BuildContext context) {
    return Card(
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: Colors.grey.shade200),
      ),
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
        leading: Icon(icon, color: iconColor ?? AppTheme.primary, size: 28),
        title: Text(title, style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text(subtitle),
        trailing: const Icon(Icons.arrow_forward_ios_rounded, size: 16),
        onTap: onTap,
      ),
    );
  }
}

class _ErrorView extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;
  const _ErrorView({required this.message, required this.onRetry});
  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.error_outline, size: 48, color: AppTheme.error),
            const SizedBox(height: 12),
            Text(message, textAlign: TextAlign.center),
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed: onRetry,
              icon: const Icon(Icons.refresh),
              label: const Text('Retry'),
            ),
          ],
        ),
      ),
    );
  }
}


// ── Post-checkout landing page ────────────────────────────────────────────

class BillingSuccessScreen extends StatefulWidget {
  const BillingSuccessScreen({super.key});
  @override
  State<BillingSuccessScreen> createState() => _BillingSuccessScreenState();
}

class _BillingSuccessScreenState extends State<BillingSuccessScreen> {
  bool _checking = true;
  bool _isPro = false;
  int _attempts = 0;

  @override
  void initState() {
    super.initState();
    _poll();
  }

  Future<void> _poll() async {
    // Webhook may take a few seconds. Poll every 1.5s up to 8 attempts.
    while (mounted && _attempts < 8) {
      _attempts++;
      try {
        final api = context.read<ApiService>();
        final pp = context.read<ProfileProvider>();
        try {
          await pp.loadProfile();
        } catch (_) {}
        if (pp.isPremium) {
          if (mounted) {
            setState(() {
              _isPro = true;
              _checking = false;
            });
          }
          return;
        }
        final resp = await api.get('/api/v1/billing/subscription');
        final tier = (resp.data?['tier'] ?? '').toString().toLowerCase();
        if (isPremiumTier(tier)) {
          if (mounted) {
            setState(() {
              _isPro = true;
              _checking = false;
            });
          }
          return;
        }
      } catch (_) {/* swallow */}
      await Future.delayed(const Duration(milliseconds: 1500));
    }
    if (mounted) {
      setState(() {
        _checking = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (_checking) ...[
                  const CircularProgressIndicator(),
                  const SizedBox(height: 16),
                  const Text('Confirming your payment\u2026',
                      style: TextStyle(fontSize: 16)),
                ] else if (_isPro) ...[
                  const Icon(Icons.check_circle_rounded,
                      color: AppTheme.success, size: 80),
                  const SizedBox(height: 16),
                  Text('You\'re Pro now \ud83c\udf89',
                      style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                            fontWeight: FontWeight.w700,
                          )),
                  const SizedBox(height: 8),
                  const Text(
                    'All daily limits are removed and advanced features are unlocked.',
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 24),
                  FilledButton(
                    onPressed: () => context.go('/'),
                    style: FilledButton.styleFrom(
                        backgroundColor: AppTheme.primary, minimumSize: const Size(220, 48)),
                    child: const Text('Start applying'),
                  ),
                ] else ...[
                  const Icon(Icons.info_outline,
                      color: AppTheme.warning, size: 64),
                  const SizedBox(height: 12),
                  const Text(
                    'We haven\'t received your payment confirmation yet. '
                    'It usually arrives within a minute. Refresh the Subscription '
                    'page in a bit.',
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 20),
                  FilledButton(
                    onPressed: () => context.go('/subscription'),
                    child: const Text('Go to Subscription'),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}
