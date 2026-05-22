/// Shared premium / admin access checks for paywalls and limits.
library;

const Set<String> kPremiumTiers = {
  'premium',
  'pro',
  'lifetime',
  'career_plus',
  'admin',
};

bool isPremiumTier(String? tier) =>
    kPremiumTiers.contains((tier ?? 'free').toLowerCase());

String tierFromProfile(Map<String, dynamic>? profile) {
  if (profile == null) return 'free';
  return ((profile['subscription'] as Map?)?['tier'] ??
          profile['tier'] ??
          'free')
      .toString()
      .toLowerCase();
}

/// Matches [ADMIN_EMAILS] dart-define used for the admin dashboard.
bool isAdminEmail(String? email) {
  const adminRaw =
      String.fromEnvironment('ADMIN_EMAILS', defaultValue: 'vibhuu1720@gmail.com');
  final allowed = adminRaw
      .split(',')
      .map((e) => e.trim().toLowerCase())
      .where((e) => e.isNotEmpty)
      .toSet();
  return email != null && allowed.contains(email.trim().toLowerCase());
}

/// Profile tier, billing subscription tier, or admin email allow-list.
bool isPremiumAccess({
  Map<String, dynamic>? profile,
  String? billingTier,
  String? email,
}) {
  if (isAdminEmail(email)) return true;
  if (isPremiumTier(tierFromProfile(profile))) return true;
  return isPremiumTier(billingTier);
}
