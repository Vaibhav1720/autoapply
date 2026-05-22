// User-facing Pro pricing lines (country-aware). Does not affect billing APIs.

bool isIndiaCountry(String? country) {
  final c = (country ?? '').trim().toUpperCase();
  return c == 'IN' || c == 'IND' || c == 'INDIA';
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
  const monthly = 9.99;
  final perDay = _perDayCeil(89, 365);
  return 'Just \$$monthly/month \u2014 less than \$$perDay/day';
}
