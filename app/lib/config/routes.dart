import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;
import 'package:auto_apply/screens/main_shell.dart';
import 'package:auto_apply/screens/auth/login_screen.dart';
import 'package:auto_apply/screens/auth/signup_screen.dart';
import 'package:auto_apply/screens/discover/discover_screen.dart';
import 'package:auto_apply/screens/companies/companies_screen.dart';
import 'package:auto_apply/screens/profile/profile_screen.dart';
import 'package:auto_apply/screens/profile/application_details_screen.dart';
import 'package:auto_apply/screens/admin/admin_dashboard_screen.dart';
import 'package:auto_apply/screens/billing/pricing_screen.dart';
import 'package:auto_apply/screens/billing/subscription_screen.dart';
import 'package:auto_apply/screens/legal/legal_screen.dart';
import 'package:auto_apply/screens/contact/contact_screen.dart';

final GlobalKey<NavigatorState> _rootNavigatorKey = GlobalKey<NavigatorState>();

// Check if user has a stored token
String _getInitialLocation() {
  final token = html.window.localStorage['auth_token'];
  return (token != null && token.isNotEmpty) ? '/' : '/login';
}

/// GoRouter route definitions.
final GoRouter appRouter = GoRouter(
  navigatorKey: _rootNavigatorKey,
  initialLocation: _getInitialLocation(),
  redirect: (context, state) {
    final isAuth = html.window.localStorage['auth_token']?.isNotEmpty ?? false;
    final loc = state.matchedLocation;
    final isPublicRoute = loc == '/login' || loc == '/signup' ||
        loc == '/privacy' || loc == '/terms' || loc == '/refund' ||
        loc == '/contact';
    if (!isAuth && !isPublicRoute) return '/login';
    if (isAuth && (loc == '/login' || loc == '/signup')) return '/';
    return null;
  },
  routes: [
    GoRoute(path: '/login', builder: (_, __) => const LoginScreen()),
    GoRoute(path: '/signup', builder: (_, __) => const SignupScreen()),
    StatefulShellRoute.indexedStack(
      builder: (context, state, navigationShell) {
        return MainShell(navigationShell: navigationShell);
      },
      branches: [
        StatefulShellBranch(
          routes: [
            GoRoute(path: '/', builder: (_, __) => const DiscoverScreen()),
          ],
        ),
        StatefulShellBranch(
          routes: [
            GoRoute(path: '/companies', builder: (_, __) => const CompaniesScreen()),
          ],
        ),
        StatefulShellBranch(
          routes: [
            GoRoute(path: '/profile', builder: (_, __) => const ProfileScreen()),
          ],
        ),
      ],
    ),
    GoRoute(path: '/application-details', builder: (_, __) => const ApplicationDetailsScreen()),
    GoRoute(path: '/admin', builder: (_, __) => const AdminDashboardScreen()),
    GoRoute(path: '/pricing', builder: (_, __) => const PricingScreen()),
    GoRoute(path: '/subscription', builder: (_, __) => const SubscriptionScreen()),
    GoRoute(path: '/billing/success', builder: (_, __) => const BillingSuccessScreen()),
    // Legal — public, no auth required
    GoRoute(path: '/privacy', builder: (_, __) => const PrivacyPolicyScreen()),
    GoRoute(path: '/terms', builder: (_, __) => const TermsScreen()),
    GoRoute(path: '/refund', builder: (_, __) => const RefundPolicyScreen()),
    // Contact / Support — public, no auth required
    GoRoute(path: '/contact', builder: (_, __) => const ContactScreen()),
  ],
);
