// Pricing + checkout screen.
//
// - Loads /api/v1/billing/plans (passes user's country → backend returns
//   INR/Razorpay plans for India or USD/LemonSqueezy for other countries).
// - Loads /api/v1/billing/subscription for the user's current sub state.
// - On Upgrade click:
//     India one-time → Standard Checkout modal (create-order + verify-payment)
//     India recurring → POST /api/v1/billing/razorpay/checkout (subscription link)
//     Others → POST /api/v1/billing/checkout (Lemon Squeezy)

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:go_router/go_router.dart';
// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;

import 'package:auto_apply/config/theme.dart';
import 'package:auto_apply/providers/auth_provider.dart';
import 'package:auto_apply/providers/profile_provider.dart';
import 'package:auto_apply/services/api_service.dart';
import 'package:auto_apply/utils/razorpay_checkout_web.dart';
import 'package:auto_apply/utils/pricing_copy.dart';
import 'package:auto_apply/utils/subscription_access.dart';

class PricingScreen extends StatefulWidget {
  const PricingScreen({super.key});

  @override
  State<PricingScreen> createState() => _PricingScreenState();
}

class _PricingScreenState extends State<PricingScreen> {
  bool _loading = true;
  bool _checkoutBusy = false;
  String? _busyPlanId;
  String? _error;
  List<Map<String, dynamic>> _plans = const [];
  Map<String, dynamic> _sub = const {};
  bool _isIndia = false;
  /// India Razorpay: default subscription; user may switch to one-time.
  String _paymentType = 'recurring';

  @override
  void initState() {
    super.initState();
    _load();
  }

  String get _userCountry {
    final pp = context.read<ProfileProvider>();
    return ((pp.profile?['applicationDetails'] as Map?)?['country'] as String? ?? '')
        .trim()
        .toUpperCase();
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
      final country = _userCountry;
      final planParams = <String, String>{
        '_': DateTime.now().millisecondsSinceEpoch.toString(),
      };
      if (country.isNotEmpty) planParams['country'] = country;
      final plansResp = await api.get(
        '/api/v1/billing/plans',
        queryParameters: planParams,
      );
      final plans = (plansResp.data?['plans'] as List?) ?? const [];
      final currency = (plansResp.data?['currency'] as String?) ?? 'USD';
      Map<String, dynamic> sub = const {};
      try {
        final subResp = await api.get('/api/v1/billing/subscription');
        if (subResp.data is Map) sub = Map<String, dynamic>.from(subResp.data);
      } catch (_) {/* not signed in or no profile yet */}
      if (!mounted) return;
      final isIndia = currency == 'INR' || isIndiaCountry(country);
      setState(() {
        _plans = filterActiveBillingPlans(
          plans.cast<Map<String, dynamic>>(),
          isIndia: isIndia,
        );
        _sub = sub;
        _isIndia = isIndia;
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

  Future<void> _startCheckout(Map<String, dynamic> plan) async {
    if (_checkoutBusy) return;
    setState(() {
      _checkoutBusy = true;
      _busyPlanId = plan['id'] as String?;
    });
    try {
      final api = context.read<ApiService>();
      final provider = (plan['paymentProvider'] as String?) ?? 'lemonsqueezy';

      // India one-time: Razorpay Standard Checkout (modal on this page).
      if (_isIndia && provider == 'razorpay' && _paymentType == 'one_time') {
        await _startRazorpayStandardCheckout(api, plan);
        return;
      }

      final endpoint = provider == 'razorpay'
          ? '/api/v1/billing/razorpay/checkout'
          : '/api/v1/billing/checkout';

      final body = <String, dynamic>{'planId': plan['id']};
      if (_isIndia) {
        body['paymentType'] = _paymentType;
      }

      final resp = await api.post(endpoint, data: body);
      final url = (resp.data?['url'] ?? '').toString();
      if (url.isEmpty) throw Exception('Checkout URL was empty');

      html.window.open(url, '_blank');
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(provider == 'razorpay'
                ? 'Opening Razorpay secure checkout\u2026'
                : 'Opening secure checkout in a new tab\u2026'),
            duration: const Duration(seconds: 3),
          ),
        );
      }
    } catch (e) {
      if (!mounted) return;
      String msg;
      final s = e.toString();
      if (s.contains('503') || s.contains('PAYMENT_UNAVAILABLE')) {
        msg = _isIndia && _paymentType == 'one_time'
            ? 'One-time payment is not yet configured for this plan. Please use the subscription option or contact support.'
            : 'Checkout is being set up. Please try again soon or contact support.';
      } else {
        msg = 'Checkout failed: $s';
      }
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(msg), backgroundColor: AppTheme.error),
      );
    } finally {
      if (mounted) {
        setState(() {
          _checkoutBusy = false;
          _busyPlanId = null;
        });
      }
    }
  }

  Future<void> _startRazorpayStandardCheckout(
    ApiService api,
    Map<String, dynamic> plan,
  ) async {
    final planId = plan['id']?.toString() ?? '';
    final orderResp = await api.post(
      '/api/v1/billing/razorpay/create-order',
      data: {'planId': planId},
    );
    final data = orderResp.data is Map
        ? Map<String, dynamic>.from(orderResp.data as Map)
        : <String, dynamic>{};

    final orderId = (data['order_id'] ?? '').toString();
    final keyId = (data['key_id'] ?? '').toString();
    final amount = data['amount'] is int
        ? data['amount'] as int
        : int.tryParse('${data['amount']}') ?? 0;
    final currency = (data['currency'] ?? 'INR').toString();
    final testMode = data['testMode'] == true || keyId.startsWith('rzp_test_');
    if (orderId.isEmpty || keyId.isEmpty || amount < 100) {
      throw Exception('Invalid order response from server');
    }

    if (testMode && mounted) {
      final proceed = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Test mode payment'),
          content: const Text(
            'You are using Razorpay test keys. The UPI QR shown is a dummy — '
            'scanning it with PhonePe/GPay will not work.\n\n'
            'To complete a test payment:\n'
            '• Tap Cards → use test card 5267 3184 3456 8039, any CVV, future expiry, '
            'then tap Success on the next screen\n'
            '• Or tap UPI → enter success@razorpay (not the QR)\n\n'
            'For a real scannable QR, switch to live keys (rzp_live_) in Razorpay Dashboard.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Continue to checkout'),
            ),
          ],
        ),
      );
      if (proceed != true) return;
    }

    final pp = context.read<ProfileProvider>();
    final auth = context.read<AuthProvider>();
    final personal = pp.personal;
    final name = '${personal['firstName'] ?? ''} ${personal['lastName'] ?? ''}'
        .trim();
    final email = (pp.profile?['email'] ?? auth.email ?? '').toString().trim();
    final displayName = name.isNotEmpty ? name : (auth.name ?? 'ApplyRight User');

    final payment = await openRazorpayStandardCheckout(
      keyId: keyId,
      orderId: orderId,
      amountPaise: amount,
      currency: currency,
      description: 'ApplyRight ${plan['name'] ?? 'Pro'}',
      customerName: displayName,
      customerEmail: email.isNotEmpty ? email : 'user@example.com',
      testMode: testMode,
    );

    if (payment == null) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Payment cancelled.'),
            duration: Duration(seconds: 2),
          ),
        );
      }
      return;
    }

    final verifyResp = await api.post(
      '/api/v1/billing/razorpay/verify-payment',
      data: {
        'planId': planId,
        'razorpay_order_id': payment['razorpay_order_id'],
        'razorpay_payment_id': payment['razorpay_payment_id'],
        'razorpay_signature': payment['razorpay_signature'],
      },
    );
    final ok = verifyResp.data is Map &&
        (verifyResp.data['success'] == true ||
            verifyResp.data['tier'] == 'pro');
    if (!ok) {
      throw Exception('Payment verification failed');
    }

    try {
      await pp.loadProfile();
    } catch (_) {}

    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Payment successful — ApplyRight Pro is now active!'),
          backgroundColor: AppTheme.success,
          duration: Duration(seconds: 4),
        ),
      );
      context.push('/billing/success');
    }
  }

  bool get _isCancelled {
    final s = (_sub['status'] ?? '').toString().toLowerCase();
    return s == 'cancelled' || s == 'expired';
  }

  /// Pro/admin from ProfileProvider (profile + billing + admin email) or billing snapshot.
  bool _isProUser(ProfileProvider pp) {
    return pp.isPremium || isPremiumTier((_sub['tier'] ?? '').toString());
  }

  @override
  Widget build(BuildContext context) {
    final pp = context.watch<ProfileProvider>();
    final isPro = _isProUser(pp);
    final hasActivePro = isPro && !_isCancelled;
    final width = MediaQuery.of(context).size.width;
    final isWide = width >= 900;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Pricing'),
        leading: BackButton(onPressed: () => context.go('/profile')),
      ),
      body: SafeArea(
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? _ErrorView(message: _error!, onRetry: _load)
                : SingleChildScrollView(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 24),
                    child: Center(
                      child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 1100),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            _Header(
                              isPro: isPro,
                              cancelled: _isCancelled,
                              sub: _sub,
                              isIndia: _isIndia,
                            ),
                            const SizedBox(height: 24),
                            // Payment type toggle — India (Razorpay) only
                            if (!hasActivePro && _isIndia) ...[
                              _PaymentTypeToggle(
                                value: _paymentType,
                                onChanged: (v) => setState(() => _paymentType = v),
                              ),
                              const SizedBox(height: 20),
                            ],
                            isWide
                                ? IntrinsicHeight(
                                    child: Row(
                                      crossAxisAlignment: CrossAxisAlignment.stretch,
                                      children: [
                                        for (final p in _plans)
                                          Expanded(
                                            child: Padding(
                                              padding: const EdgeInsets.symmetric(horizontal: 8),
                                              child: _PlanCard(
                                                plan: p,
                                                isCurrent: _isCurrent(p),
                                                isBusy: _busyPlanId == p['id'] && _checkoutBusy,
                                                isPro: hasActivePro,
                                                isIndia: _isIndia,
                                                paymentType: _paymentType,
                                                resubscribe: _isCancelled,
                                                onSubscribe: () => _startCheckout(p),
                                              ),
                                            ),
                                          ),
                                      ],
                                    ),
                                  )
                                : Column(
                                    children: [
                                      for (final p in _plans)
                                        Padding(
                                          padding: const EdgeInsets.only(bottom: 16),
                                          child: _PlanCard(
                                            plan: p,
                                            isCurrent: _isCurrent(p),
                                            isBusy: _busyPlanId == p['id'] && _checkoutBusy,
                                            isPro: hasActivePro,
                                            isIndia: _isIndia,
                                            paymentType: _paymentType,
                                            resubscribe: _isCancelled,
                                            onSubscribe: () => _startCheckout(p),
                                          ),
                                        ),
                                    ],
                                  ),
                            const SizedBox(height: 28),
                            _FAQ(isIndia: _isIndia),
                            const SizedBox(height: 12),
                          ],
                        ),
                      ),
                    ),
                  ),
      ),
    );
  }

  bool _isCurrent(Map<String, dynamic> plan) {
    if (_isCancelled) return false;
    final id = (plan['id'] ?? '').toString();
    final tier = (_sub['tier'] ?? '').toString().toLowerCase();
    final interval = (_sub['interval'] ?? '').toString().toLowerCase();
    if (id == 'free' && tier == 'free') return true;
    if (id == 'pro_weekly' && tier == 'pro' && interval.startsWith('week')) return true;
    if (id == 'pro_monthly' && tier == 'pro' && interval.startsWith('month')) return true;
    if (id == 'pro_yearly' && tier == 'pro' && interval.startsWith('year')) return true;
    return false;
  }
}


// ── UI parts ──────────────────────────────────────────────────────────────

class _Header extends StatelessWidget {
  final bool isPro;
  final bool cancelled;
  final bool isIndia;
  final Map<String, dynamic> sub;
  const _Header({
    required this.isPro,
    required this.cancelled,
    required this.sub,
    required this.isIndia,
  });

  String? get _accessUntilRaw {
    final ends = (sub['endsAt'] ?? '').toString();
    if (ends.isNotEmpty) return ends;
    if (cancelled) {
      final renews = (sub['renewsAt'] ?? '').toString();
      return renews.isNotEmpty ? renews : null;
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final accessUntil = _accessUntilRaw;
    final accessUntilLabel =
        accessUntil != null && accessUntil.isNotEmpty ? _fmtDate(accessUntil) : null;

    return Column(
      children: [
        Text(
          cancelled
              ? 'Your Pro plan was cancelled'
              : isPro
                  ? 'You\'re on ApplyRight Pro \ud83c\udf89'
                  : 'Find your next job, faster',
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                fontWeight: FontWeight.w700,
                color: AppTheme.primary,
              ),
        ),
        const SizedBox(height: 8),
        Text(
          cancelled
              ? (accessUntilLabel != null
                  ? 'Pick a plan below to subscribe again. You keep Pro access until $accessUntilLabel.'
                  : 'Pick a plan below to subscribe again. You keep Pro access until the end of your paid period.')
              : isPro
                  ? 'All Pro features are unlocked. Manage your subscription anytime.'
                  : isIndia
                      ? 'Cancel any time \u2022 30-day money-back guarantee \u2022 Secure checkout via Razorpay'
                      : 'Cancel any time \u2022 30-day money-back guarantee \u2022 Secure checkout via Lemon Squeezy',
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                color: Colors.grey.shade700,
              ),
        ),
        if (isPro) ...[
          const SizedBox(height: 16),
          Wrap(
            alignment: WrapAlignment.center,
            spacing: 12,
            runSpacing: 8,
            children: [
              if (accessUntilLabel != null)
                _Chip(
                  icon: cancelled ? Icons.event_busy : Icons.event_repeat,
                  label: cancelled
                      ? 'Pro access until $accessUntilLabel'
                      : 'Renews $accessUntilLabel',
                )
              else if (!cancelled &&
                  (sub['renewsAt'] ?? '').toString().isNotEmpty)
                _Chip(
                  icon: Icons.event_repeat,
                  label: 'Renews ${_fmtDate(sub['renewsAt'])}',
                ),
              if ((sub['status'] ?? '').toString().isNotEmpty)
                _Chip(
                  icon: Icons.verified_user,
                  label: 'Status: ${sub['status']}',
                ),
            ],
          ),
        ],
      ],
    );
  }

  String _fmtDate(dynamic v) {
    if (v == null) return '';
    try {
      final dt = DateTime.parse(v.toString()).toLocal();
      return '${dt.year}-${dt.month.toString().padLeft(2, '0')}-${dt.day.toString().padLeft(2, '0')}';
    } catch (_) {
      return v.toString();
    }
  }
}

class _Chip extends StatelessWidget {
  final IconData icon;
  final String label;
  const _Chip({required this.icon, required this.label});
  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(
        color: AppTheme.primarySoft,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: AppTheme.primary.withValues(alpha: 0.18)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14, color: AppTheme.primary),
          const SizedBox(width: 6),
          Text(label, style: const TextStyle(fontSize: 12, color: AppTheme.primary)),
        ],
      ),
    );
  }
}

class _PlanCard extends StatelessWidget {
  final Map<String, dynamic> plan;
  final bool isCurrent;
  final bool isBusy;
  final bool isPro;
  final bool isIndia;
  final String paymentType;
  final bool resubscribe;
  final VoidCallback onSubscribe;

  const _PlanCard({
    required this.plan,
    required this.isCurrent,
    required this.isBusy,
    required this.isPro,
    required this.isIndia,
    required this.paymentType,
    required this.resubscribe,
    required this.onSubscribe,
  });

  @override
  Widget build(BuildContext context) {
    final highlight = plan['highlight'] == true;
    final isFree = plan['id'] == 'free';
    final interval = (plan['interval'] ?? '').toString();
    final features = (plan['features'] as List?)?.cast<String>() ?? const [];

    // Display price string: prefer INR for India, USD for others
    String priceDisplay;
    if (isFree) {
      priceDisplay = 'Free';
    } else if (isIndia) {
      final inr = (plan['priceInr'] as num?) ?? 0;
      priceDisplay = '\u20b9${inr.toStringAsFixed(0)}';
    } else {
      final usd = (plan['priceUsd'] as num?) ?? 0;
      priceDisplay = '\$${usd.toStringAsFixed(2)}';
    }

    return Container(
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(
          color: highlight ? AppTheme.primary : Colors.grey.shade200,
          width: highlight ? 2 : 1,
        ),
        boxShadow: [
          if (highlight)
            BoxShadow(
              color: AppTheme.primary.withValues(alpha: 0.10),
              blurRadius: 24,
              offset: const Offset(0, 8),
            ),
        ],
      ),
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text(
                  plan['name']?.toString() ?? '',
                  style: Theme.of(context).textTheme.titleLarge?.copyWith(
                        fontWeight: FontWeight.w700,
                      ),
                ),
                const Spacer(),
                if (highlight)
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                    decoration: BoxDecoration(
                      color: AppTheme.primary,
                      borderRadius: BorderRadius.circular(999),
                    ),
                    child: const Text('Best value',
                        style: TextStyle(
                            color: Colors.white,
                            fontSize: 11,
                            fontWeight: FontWeight.w600)),
                  ),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              plan['tagline']?.toString() ?? '',
              style: TextStyle(color: Colors.grey.shade600, fontSize: 13),
            ),
            const SizedBox(height: 16),
            Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text(
                  priceDisplay,
                  style: Theme.of(context).textTheme.displaySmall?.copyWith(
                        fontWeight: FontWeight.w800,
                        color: AppTheme.primary,
                      ),
                ),
                if (!isFree) ...[
                  const SizedBox(width: 6),
                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: Text(
                      '/ $interval',
                      style: TextStyle(color: Colors.grey.shade600, fontSize: 14),
                    ),
                  ),
                ],
              ],
            ),
            const SizedBox(height: 18),
            for (final f in features)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Padding(
                      padding: EdgeInsets.only(top: 2),
                      child: Icon(Icons.check_circle_rounded,
                          color: AppTheme.success, size: 18),
                    ),
                    const SizedBox(width: 8),
                    Expanded(child: Text(f, style: const TextStyle(fontSize: 14))),
                  ],
                ),
              ),
            const SizedBox(height: 18),
            SizedBox(
              width: double.infinity,
              height: 48,
              child: _ctaButton(context),
            ),
          ],
        ),
      ),
    );
  }

  Widget _ctaButton(BuildContext ctx) {
    if (isBusy) {
      return const FilledButton(
        onPressed: null,
        child: SizedBox(
          width: 22,
          height: 22,
          child: CircularProgressIndicator(strokeWidth: 2.4, color: Colors.white),
        ),
      );
    }
    if (plan['id'] == 'free') {
      return FilledButton.tonal(
        onPressed: null,
        child: Text(isPro ? 'Downgraded after billing ends' : 'Current plan'),
      );
    }
    if (isCurrent) {
      return FilledButton.tonal(
        onPressed: null,
        child: const Text('Current plan'),
      );
    }
    if (resubscribe && plan['id'] != 'free') {
      final interval = (plan['interval'] as String?) ?? 'month';
      final periodShort = _periodShort(interval);
      final priceStr = isIndia
          ? '₹${(plan['priceInr'] as num?)?.toStringAsFixed(0) ?? ''}'
          : '\$${(plan['priceUsd'] as num?)?.toStringAsFixed(2) ?? ''}';
      final suffix = isIndia && paymentType == 'one_time' ? '' : '/$periodShort';
      return FilledButton(
        onPressed: onSubscribe,
        style: FilledButton.styleFrom(backgroundColor: AppTheme.primary),
        child: Text('Subscribe again — $priceStr$suffix'),
      );
    }
    final String ctaLabel;
    final interval = (plan['interval'] as String?) ?? 'month';
    final periodShort = _periodShort(interval);
    if (isIndia && (plan['paymentProvider'] as String?) == 'razorpay') {
      final priceStr = '₹${(plan['priceInr'] as num?)?.toStringAsFixed(0) ?? ''}';
      ctaLabel = paymentType == 'one_time'
          ? 'Pay once — $priceStr'
          : 'Subscribe — $priceStr/$periodShort';
    } else {
      final priceStr = '\$${(plan['priceUsd'] as num?)?.toStringAsFixed(2) ?? ''}';
      ctaLabel = 'Subscribe — $priceStr/$periodShort';
    }

    return FilledButton(
      onPressed: onSubscribe,
      style: FilledButton.styleFrom(
        backgroundColor: AppTheme.primary,
        textStyle: const TextStyle(fontWeight: FontWeight.w600),
      ),
      child: Text(ctaLabel),
    );
  }
}

String _periodShort(String interval) {
  switch (interval) {
    case 'year':
      return 'yr';
    case 'week':
      return 'wk';
    default:
      return 'mo';
  }
}

// ── Payment type toggle (India / Razorpay only) ───────────────────────────

class _PaymentTypeToggle extends StatelessWidget {
  final String value;
  final ValueChanged<String> onChanged;
  const _PaymentTypeToggle({
    required this.value,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Text(
          'How would you like to pay?',
          style: Theme.of(context).textTheme.titleSmall?.copyWith(
                fontWeight: FontWeight.w700,
                color: AppTheme.textSecondary,
              ),
        ),
        const SizedBox(height: 10),
        Container(
          decoration: BoxDecoration(
            color: AppTheme.primarySoft,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: AppTheme.primary.withValues(alpha: 0.18)),
          ),
          padding: const EdgeInsets.all(4),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              _ToggleOption(
                label: 'Subscription',
                sublabel: 'Renews each period',
                icon: Icons.autorenew_rounded,
                selected: value == 'recurring',
                onTap: () => onChanged('recurring'),
              ),
              const SizedBox(width: 4),
              _ToggleOption(
                label: 'One-time payment',
                sublabel: 'Single period only',
                icon: Icons.payment_rounded,
                selected: value == 'one_time',
                onTap: () => onChanged('one_time'),
              ),
            ],
          ),
        ),
        const SizedBox(height: 8),
        Text(
          value == 'recurring'
              ? 'Billed each period via Razorpay. Cancel anytime from your profile. (Recommended)'
              : 'Pay for one period. Access expires at the end — no further charges.',
          style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
          textAlign: TextAlign.center,
        ),
      ],
    );
  }
}

class _ToggleOption extends StatelessWidget {
  final String label;
  final String sublabel;
  final IconData icon;
  final bool selected;
  final VoidCallback onTap;
  const _ToggleOption({
    required this.label,
    required this.sublabel,
    required this.icon,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
        decoration: BoxDecoration(
          color: selected ? AppTheme.primary : Colors.transparent,
          borderRadius: BorderRadius.circular(9),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon,
                size: 20,
                color: selected ? Colors.white : AppTheme.primary),
            const SizedBox(height: 4),
            Text(
              label,
              style: TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w700,
                color: selected ? Colors.white : AppTheme.primary,
              ),
            ),
            Text(
              sublabel,
              style: TextStyle(
                fontSize: 11,
                color: selected
                    ? Colors.white.withValues(alpha: 0.85)
                    : Colors.grey.shade600,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _FAQ extends StatelessWidget {
  final bool isIndia;
  const _FAQ({required this.isIndia});

  @override
  Widget build(BuildContext context) {
    final faqs = [
      [
        'How is my payment processed?',
        isIndia
            ? 'Razorpay is our secure Indian payment gateway. Your card details never touch our servers. UPI, net banking, cards, and wallets are all supported.'
            : 'Lemon Squeezy is the secure merchant of record. Your card never touches our servers. They handle all global taxes (VAT, GST, sales tax) so you see one transparent price.',
      ],
      [
        'Can I cancel anytime?',
        'Yes \u2014 cancel from your account page in one click. You keep Pro access until the end of the current billing period.',
      ],
      [
        'Do you offer refunds?',
        'Yes. We offer a 30-day money-back guarantee. Email us within 30 days of purchase and we\'ll process a full refund.',
      ],
      [
        'What payment methods do you accept?',
        isIndia
            ? 'UPI, credit/debit cards (Visa, Mastercard, RuPay), net banking, Paytm, PhonePe, and most popular wallets. Payments are in Indian Rupees (INR).'
            : 'All major cards (Visa, Mastercard, Amex, Discover), Apple Pay, Google Pay, PayPal. Charges appear in your local currency.',
      ],
      [
        'Will I receive an invoice?',
        isIndia
            ? 'Yes. Razorpay emails a GST-compliant invoice for every payment. You can also download past receipts from the Razorpay dashboard.'
            : 'Yes. Lemon Squeezy emails a tax-compliant invoice for every charge. You can also download past invoices from the customer portal.',
      ],
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text('Frequently asked questions',
            style: Theme.of(context).textTheme.titleLarge?.copyWith(
                  fontWeight: FontWeight.w700,
                )),
        const SizedBox(height: 8),
        for (final q in faqs)
          Card(
            elevation: 0,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
              side: BorderSide(color: Colors.grey.shade200),
            ),
            margin: const EdgeInsets.only(bottom: 8),
            child: ExpansionTile(
              tilePadding: const EdgeInsets.symmetric(horizontal: 16),
              title: Text(q[0],
                  style: const TextStyle(fontWeight: FontWeight.w600)),
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                  child: Align(
                    alignment: Alignment.centerLeft,
                    child: Text(q[1], style: const TextStyle(height: 1.4)),
                  ),
                ),
              ],
            ),
          ),
      ],
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
