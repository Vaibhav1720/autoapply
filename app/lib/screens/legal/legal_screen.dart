/// Unified legal screen — Privacy Policy, Terms & Conditions, Refund Policy.
///
/// Accessible without authentication (registered as public routes).
/// Navigation: context.push('/privacy'), '/terms', or '/refund'
/// All three share the same shell; the route determines the initial tab.

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:auto_apply/config/theme.dart';

// ── Entry points ──────────────────────────────────────────────────────────

class PrivacyPolicyScreen extends StatelessWidget {
  const PrivacyPolicyScreen({super.key});
  @override
  Widget build(BuildContext context) => const _LegalScreen(initialTab: 0);
}

class TermsScreen extends StatelessWidget {
  const TermsScreen({super.key});
  @override
  Widget build(BuildContext context) => const _LegalScreen(initialTab: 1);
}

class RefundPolicyScreen extends StatelessWidget {
  const RefundPolicyScreen({super.key});
  @override
  Widget build(BuildContext context) => const _LegalScreen(initialTab: 2);
}

// ── Shell ─────────────────────────────────────────────────────────────────

class _LegalScreen extends StatefulWidget {
  final int initialTab;
  const _LegalScreen({required this.initialTab});
  @override
  State<_LegalScreen> createState() => _LegalScreenState();
}

class _LegalScreenState extends State<_LegalScreen>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs;

  static const _tabLabels = ['Privacy Policy', 'Terms & Conditions', 'Refund Policy'];

  @override
  void initState() {
    super.initState();
    _tabs = TabController(
        length: _tabLabels.length, vsync: this, initialIndex: widget.initialTab);
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Legal'),
        leading: BackButton(onPressed: () {
          if (context.canPop()) {
            context.pop();
          } else {
            context.go('/profile');
          }
        }),
        bottom: TabBar(
          controller: _tabs,
          isScrollable: true,
          tabAlignment: TabAlignment.start,
          tabs: [for (final l in _tabLabels) Tab(text: l)],
        ),
      ),
      body: TabBarView(
        controller: _tabs,
        children: const [
          _PrivacyContent(),
          _TermsContent(),
          _RefundContent(),
        ],
      ),
    );
  }
}

// ── Shared layout helpers ─────────────────────────────────────────────────

class _PolicyScaffold extends StatelessWidget {
  final String title;
  final String lastUpdated;
  final List<_PolicySection> sections;
  const _PolicyScaffold({
    required this.title,
    required this.lastUpdated,
    required this.sections,
  });

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 24),
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 800),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(title,
                  style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                        fontWeight: FontWeight.w800,
                        color: AppTheme.primary,
                      )),
              const SizedBox(height: 4),
              Text('Last updated: $lastUpdated',
                  style: TextStyle(color: Colors.grey.shade600, fontSize: 13)),
              const Divider(height: 32),
              for (final s in sections) ...[
                _SectionWidget(section: s),
                const SizedBox(height: 24),
              ],
              const SizedBox(height: 16),
              Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: AppTheme.primarySoft,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: AppTheme.primary.withValues(alpha: 0.18)),
                ),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  const Icon(Icons.mail_outline, size: 18, color: AppTheme.primary),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      'Questions? Contact us at vibhuu1720@gmail.com',
                      style: const TextStyle(
                          color: AppTheme.primary,
                          fontSize: 13,
                          fontWeight: FontWeight.w500),
                    ),
                  ),
                ]),
              ),
              const SizedBox(height: 32),
            ],
          ),
        ),
      ),
    );
  }
}

class _PolicySection {
  final String heading;
  final List<String> paras;
  final List<String>? bullets;
  const _PolicySection(this.heading, this.paras, {this.bullets});
}

class _SectionWidget extends StatelessWidget {
  final _PolicySection section;
  const _SectionWidget({required this.section});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(section.heading,
            style: const TextStyle(
                fontSize: 16, fontWeight: FontWeight.w700, color: Color(0xFF1e1b4b))),
        const SizedBox(height: 8),
        for (final p in section.paras) ...[
          Text(p, style: const TextStyle(fontSize: 14, height: 1.6, color: Color(0xFF374151))),
          const SizedBox(height: 6),
        ],
        if (section.bullets != null) ...[
          const SizedBox(height: 4),
          for (final b in section.bullets!)
            Padding(
              padding: const EdgeInsets.only(bottom: 4, left: 4),
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                const Text('\u2022  ',
                    style: TextStyle(
                        fontSize: 14, color: AppTheme.primary, fontWeight: FontWeight.w700)),
                Expanded(
                  child: Text(b,
                      style: const TextStyle(
                          fontSize: 14, height: 1.5, color: Color(0xFF374151))),
                ),
              ]),
            ),
        ],
      ],
    );
  }
}

// ── Privacy Policy ────────────────────────────────────────────────────────

class _PrivacyContent extends StatelessWidget {
  const _PrivacyContent();

  @override
  Widget build(BuildContext context) {
    return _PolicyScaffold(
      title: 'Privacy Policy',
      lastUpdated: 'May 20, 2026',
      sections: const [
        _PolicySection(
          '1. Introduction',
          [
            'HirePanda ("we", "us", or "our") operates autoapplynow.in and the HirePanda web application. This Privacy Policy explains what information we collect, how we use it, and your rights regarding your data.',
            'By using HirePanda you agree to the practices described in this policy. If you do not agree, please discontinue use immediately.',
          ],
        ),
        _PolicySection(
          '2. Information We Collect',
          [
            'We collect information you provide directly and data generated automatically as you use our service.',
          ],
          bullets: [
            'Account data: name, email address, password (hashed), and Google OAuth identity.',
            'Profile data: resume content, work experience, education, skills, job preferences, application details (salary, notice period, visa status, etc.).',
            'Usage data: job searches you run, companies you track, AI suggestions you request, and in-app actions (timestamped).',
            'Payment data: subscription status and payment provider references (Razorpay or Lemon Squeezy order/subscription IDs). We do NOT store your card number, CVV, or bank credentials — those are held exclusively by our payment processors.',
            'Technical data: browser type, IP address, device type, referring URL, and page-load telemetry collected automatically via Azure Application Insights.',
          ],
        ),
        _PolicySection(
          '3. Cookies & Local Storage',
          [
            'HirePanda uses browser local storage (not traditional cookies) to persist your authentication token so you remain logged in across sessions. We do not use third-party advertising cookies.',
          ],
          bullets: [
            'auth_token: your session JWT, stored in localStorage and cleared on sign-out.',
            'Application Insights: Microsoft\'s analytics SDK may set first-party cookies to correlate telemetry sessions.',
          ],
        ),
        _PolicySection(
          '4. Analytics',
          [
            'We use Microsoft Azure Application Insights to monitor application performance, error rates, and aggregate usage patterns. This data is used solely to improve the product and is never sold or shared with third-party advertisers.',
            'We do not use Google Analytics, Meta Pixel, or any advertising-network tracking on autoapplynow.in.',
          ],
        ),
        _PolicySection(
          '5. How We Use Your Data',
          [
            'We use your information exclusively to provide and improve the HirePanda service.',
          ],
          bullets: [
            'Authenticate you and keep your account secure.',
            'Personalise job discovery results using AI matching against your profile.',
            'Auto-fill job application forms via the HirePanda Chrome Extension.',
            'Generate AI resume-tailoring suggestions against target job descriptions.',
            'Process payments and manage your subscription through Razorpay (India) or Lemon Squeezy (international).',
            'Send transactional emails (payment receipts, account alerts). We do not send unsolicited marketing emails.',
            'Detect and prevent fraud, abuse, and security incidents.',
          ],
        ),
        _PolicySection(
          '6. Payment Data & Processors',
          [
            'For users in India, payments are processed by Razorpay. For all other countries, payments are processed by Lemon Squeezy. Both processors are PCI-DSS compliant.',
            'When you initiate a payment, your browser is redirected to the processor\'s hosted checkout. We receive only a payment reference ID and subscription status — never your raw card or banking details.',
            'Razorpay\'s privacy policy: https://razorpay.com/privacy/\nLemon Squeezy\'s privacy policy: https://www.lemonsqueezy.com/privacy',
          ],
        ),
        _PolicySection(
          '7. Data Sharing',
          [
            'We do not sell, rent, or trade your personal data. We share data only with:',
          ],
          bullets: [
            'Microsoft Azure: cloud infrastructure hosting our database, file storage, and AI services (Azure OpenAI).',
            'Razorpay / Lemon Squeezy: solely for payment processing.',
            'Law enforcement: only when legally required by a court order or applicable law.',
          ],
        ),
        _PolicySection(
          '8. Data Retention',
          [
            'We retain your account and profile data for as long as your account is active. You may delete your account at any time from the Profile page — this permanently erases all your data within 30 days.',
            'Usage event records (daily quota tracking) are automatically deleted after 24 hours via Cosmos DB TTL.',
          ],
        ),
        _PolicySection(
          '9. Security',
          [
            'All data in transit is encrypted using TLS 1.2+. Data at rest is encrypted by Microsoft Azure. Authentication tokens are signed JWTs. We perform regular security reviews.',
            'Despite these measures, no internet transmission is 100% secure. Please use a strong, unique password and sign out on shared devices.',
          ],
        ),
        _PolicySection(
          '10. Your Rights',
          [
            'Depending on your location you may have rights to access, correct, export, or delete your personal data. To exercise any right, email vibhuu1720@gmail.com. We will respond within 30 days.',
          ],
          bullets: [
            'Access: request a copy of all data we hold about you.',
            'Correction: request corrections to inaccurate data.',
            'Deletion: delete your account and all associated data.',
            'Portability: export your profile data as JSON.',
          ],
        ),
        _PolicySection(
          '11. Children\'s Privacy',
          [
            'HirePanda is intended for users aged 18 and above. We do not knowingly collect data from children under 18. If you believe a child has created an account, please contact us immediately.',
          ],
        ),
        _PolicySection(
          '12. Changes to This Policy',
          [
            'We may update this Privacy Policy from time to time. Material changes will be communicated via in-app notification or email. Continued use of HirePanda after changes constitutes acceptance.',
          ],
        ),
      ],
    );
  }
}

// ── Terms & Conditions ────────────────────────────────────────────────────

class _TermsContent extends StatelessWidget {
  const _TermsContent();

  @override
  Widget build(BuildContext context) {
    return _PolicyScaffold(
      title: 'Terms & Conditions',
      lastUpdated: 'May 20, 2026',
      sections: const [
        _PolicySection(
          '1. Acceptance of Terms',
          [
            'By accessing or using HirePanda ("Service") at autoapplynow.in, you agree to be bound by these Terms & Conditions ("Terms"). If you do not agree, you must not use the Service.',
            'These Terms form a legally binding agreement between you and HirePanda. We reserve the right to update them at any time; continued use after updates constitutes acceptance.',
          ],
        ),
        _PolicySection(
          '2. Eligibility & Account',
          [
            'You must be at least 18 years old to use HirePanda. By registering, you represent that all information you provide is accurate and that you are authorised to enter into this agreement.',
          ],
          bullets: [
            'You are responsible for maintaining the confidentiality of your account credentials.',
            'You must notify us immediately at vibhuu1720@gmail.com if you suspect unauthorised access.',
            'One account per person. Creating multiple accounts to circumvent free-tier limits is prohibited.',
          ],
        ),
        _PolicySection(
          '3. Permitted Use',
          [
            'HirePanda is a job-discovery and application-assistance tool for individual job seekers. You may use it solely for personal, non-commercial job-search purposes.',
          ],
          bullets: [
            'Searching for jobs and tracking employer career pages.',
            'Using AI autofill to assist in completing job application forms.',
            'Generating AI resume-tailoring suggestions based on your own profile.',
          ],
        ),
        _PolicySection(
          '4. Prohibited Use',
          [
            'The following activities are strictly prohibited and may result in immediate account termination without refund:',
          ],
          bullets: [
            'Scraping, harvesting, or bulk-downloading data from HirePanda or third-party sites via the Service.',
            'Using the Service to submit applications on behalf of other individuals.',
            'Reverse-engineering, decompiling, or creating derivative products from HirePanda.',
            'Uploading false, misleading, or fraudulent profile information.',
            'Attempting to bypass rate limits, quotas, or access controls.',
            'Using automated bots or scripts to interact with the Service.',
            'Violating any applicable law or third-party rights.',
          ],
        ),
        _PolicySection(
          '5. Subscription Terms',
          [
            'HirePanda offers a Free plan and a Pro plan. Pro plans are available on monthly or yearly billing cycles.',
          ],
          bullets: [
            'Free plan: access is subject to daily usage quotas as displayed in the app.',
            'Pro plan (India): billed in INR via Razorpay. Monthly plan at ₹199/month; yearly plan at ₹1,799/year.',
            'Pro plan (International): billed in USD via Lemon Squeezy. Monthly plan at \$9.99/month; yearly plan at \$89.99/year.',
            'Subscriptions auto-renew unless cancelled before the end of the current billing period.',
            'You can cancel anytime from the Subscription page. Access continues until the end of the paid period.',
            'We reserve the right to change pricing with 30 days\' notice to existing subscribers.',
          ],
        ),
        _PolicySection(
          '6. Free-Tier Limits',
          [
            'Free accounts are subject to the following daily limits (limits may be adjusted at our discretion):',
          ],
          bullets: [
            '2 Discover job searches per day.',
            '2 LinkedIn searches per day.',
            '5 AI autofill suggestions per day.',
            'Tracking up to 5 companies.',
          ],
        ),
        _PolicySection(
          '7. AI-Generated Content',
          [
            'HirePanda uses large language models (Azure OpenAI) to generate resume suggestions, job scores, and autofill answers. You acknowledge that:',
          ],
          bullets: [
            'AI outputs are suggestions only and may contain errors or inaccuracies.',
            'You are solely responsible for reviewing and verifying all AI-generated content before submitting it in any job application.',
            'HirePanda does not guarantee that AI suggestions will improve your job-search outcomes.',
          ],
        ),
        _PolicySection(
          '8. Third-Party Job Listings',
          [
            'HirePanda surfaces job listings scraped from publicly available company career pages. We are not responsible for the accuracy, availability, or terms of any third-party job posting.',
            'Applying for a job creates a direct relationship between you and the employer. HirePanda is not party to that relationship.',
          ],
        ),
        _PolicySection(
          '9. Intellectual Property',
          [
            'All software, design, trademarks, and content on autoapplynow.in are owned by HirePanda or its licensors. You are granted a limited, non-exclusive, non-transferable licence to use the Service for personal job-search purposes.',
            'Your profile data and uploaded resume remain your property. You grant HirePanda a licence to process them solely to provide the Service.',
          ],
        ),
        _PolicySection(
          '10. Cancellations',
          [
            'You may cancel your Pro subscription at any time through the Subscription page or by contacting us at vibhuu1720@gmail.com.',
          ],
          bullets: [
            'Cancellation takes effect at the end of the current billing period.',
            'No partial refunds are issued for unused time within a billing period (see Refund Policy).',
            'After cancellation your account reverts to the Free plan automatically.',
          ],
        ),
        _PolicySection(
          '11. Disclaimers & Limitation of Liability',
          [
            'HirePanda is provided "as is" without warranties of any kind. We do not guarantee continuous, error-free service or specific job-search outcomes.',
            'To the maximum extent permitted by law, HirePanda\'s total liability to you for any claim arising from your use of the Service shall not exceed the amount you paid us in the 30 days preceding the claim.',
          ],
        ),
        _PolicySection(
          '12. Governing Law',
          [
            'These Terms are governed by the laws of India. Any disputes shall be subject to the exclusive jurisdiction of the courts in India.',
          ],
        ),
        _PolicySection(
          '13. Contact',
          [
            'For questions about these Terms, please contact us at vibhuu1720@gmail.com.',
          ],
        ),
      ],
    );
  }
}

// ── Refund Policy ─────────────────────────────────────────────────────────

class _RefundContent extends StatelessWidget {
  const _RefundContent();

  @override
  Widget build(BuildContext context) {
    return _PolicyScaffold(
      title: 'Refund Policy',
      lastUpdated: 'May 20, 2026',
      sections: const [
        _PolicySection(
          '1. General Policy — No Refunds',
          [
            'All payments made to HirePanda are final and non-refundable.',
            'By completing a purchase you acknowledge that you have reviewed the plan features and agree that no refund will be issued once payment is processed — whether for monthly or yearly subscriptions, regardless of usage.',
          ],
        ),
        _PolicySection(
          '2. Why We Have a No-Refund Policy',
          [
            'HirePanda provides immediate access to AI-powered features upon payment. Because the full value of the subscription (unlimited job searches, AI autofill, resume tailoring) is made available instantly and is consumed progressively, we are unable to recover that value once granted.',
            'We encourage you to use the Free plan to evaluate the Service before upgrading to a paid plan.',
          ],
        ),
        _PolicySection(
          '3. Cancellations',
          [
            'You may cancel your subscription at any time. Cancellation stops future billing but does not entitle you to a refund for the current billing period.',
          ],
          bullets: [
            'Your Pro access remains active until the end of the paid billing period.',
            'After the period ends your account automatically reverts to the Free plan.',
            'No prorated or partial refunds are issued for unused days.',
          ],
        ),
        _PolicySection(
          '4. Duplicate Charges',
          [
            'If you were charged more than once for the same subscription period due to a technical error, please contact us within 7 days at vibhuu1720@gmail.com with proof of the duplicate transaction. Verified duplicate charges will be reversed.',
          ],
        ),
        _PolicySection(
          '5. Payment Disputes',
          [
            'Initiating a chargeback or payment dispute without first contacting us may result in immediate account suspension. We strongly encourage you to reach out to us directly — we resolve legitimate billing issues promptly.',
          ],
        ),
        _PolicySection(
          '6. Contact',
          [
            'For billing queries or to report a duplicate charge, email vibhuu1720@gmail.com. Include your registered email, transaction ID, and a description of the issue. We respond within 2 business days.',
          ],
        ),
      ],
    );
  }
}
