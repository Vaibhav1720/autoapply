// User-facing Pro pricing lines (country-aware). Does not affect billing APIs.

bool isIndiaCountry(String? country) {
  final c = (country ?? '').trim().toUpperCase();
  return c == 'IN' || c == 'IND' || c == 'INDIA';
}

/// Only these paid plans are sold (API may lag behind deploy).
const Set<String> kBillingPlanIdsIndia = {'free', 'pro_monthly'};
const Set<String> kBillingPlanIdsInternational = {'free', 'pro_weekly'};

List<Map<String, dynamic>> filterActiveBillingPlans(
  List<Map<String, dynamic>> plans, {
  required bool isIndia,
}) {
  final allowed = isIndia ? kBillingPlanIdsIndia : kBillingPlanIdsInternational;
  final out = <Map<String, dynamic>>[];
  for (final p in plans) {
    final id = (p['id'] ?? '').toString();
    if (!allowed.contains(id)) continue;
    final copy = Map<String, dynamic>.from(p);
    if (id == 'pro_monthly' && isIndia) {
      copy['priceInr'] = 199;
      copy['amountPaise'] = 19900;
      copy['ctaLabel'] = 'Upgrade — ₹199/mo';
      copy['highlight'] = true;
    }
    if (id == 'pro_weekly' && !isIndia) {
      copy['priceUsd'] = 0.99;
      copy['ctaLabel'] = 'Upgrade — \$0.99/wk';
      copy['highlight'] = true;
    }
    out.add(copy);
  }
  return out;
}

/// Smallest display unit strictly greater than [amount] / [days].
/// e.g. 89/365 → 0.25 USD; 199/30 → 7 INR.
num _perDayCeil(num amount, int days) {
  if (days <= 0) return amount;
  final raw = amount / days;
  if (raw == raw.truncateToDouble()) return raw;
  // One step up in the smallest currency unit we show.
  if (raw < 1) {
    return (raw * 100).ceil() / 100;
  }
  return raw.ceil();
}

/// Highlight for upgrade dialogs: monthly price + implied daily cost.
String upgradePriceHighlight({required bool isIndia}) {
  if (isIndia) {
    const monthly = 199;
    final perDay = _perDayCeil(monthly, 30);
    return 'Just \u20b9$monthly/month \u2014 less than \u20b9$perDay/day';
  }
  const weekly = 0.99;
  final perDay = _perDayCeil(weekly, 7);
  return 'Just \$$weekly/week \u2014 about \$$perDay/day';
}
