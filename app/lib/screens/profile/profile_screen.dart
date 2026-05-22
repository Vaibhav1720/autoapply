import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:go_router/go_router.dart';
import 'dart:convert';
import 'dart:async';
import 'dart:typed_data';
// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;
import 'package:dio/dio.dart';
import 'package:auto_apply/config/theme.dart';
import 'package:auto_apply/data/search_taxonomy.dart';
import 'package:auto_apply/providers/auth_provider.dart';
import 'package:auto_apply/providers/profile_provider.dart';
import 'package:auto_apply/services/api_service.dart';
import 'package:auto_apply/widgets/simple_markdown.dart';
import 'package:auto_apply/widgets/profile_guards.dart';
import 'package:auto_apply/widgets/tailor_resume_dialog.dart';

class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key});
  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  final _skillCtrl = TextEditingController();
  bool _uploadingResume = false;
  String _extStatus = 'checking'; // checking, not_installed, installed, connected
  StreamSubscription<html.MessageEvent>? _msgSub;
  Timer? _extProbeTimer;
  int _autofillCompleteness = 100;
  int _missingCount = 0;

  // Resume insights (POST /api/v1/resume/insights). Lazy-loaded the first
  // time the Resume card is visible; cheap to recompute on demand.
  bool _insightsLoading = false;
  String? _insightsError;
  Map<String, dynamic>? _insights;

  // "Tailor my resume" state (POST /api/v1/resume/suggest-improvements).
  bool _suggesting = false;

  @override
  void initState() {
    super.initState();
    Future.microtask(() => context.read<ProfileProvider>().loadProfile().then((_) => _loadMissingInfo()));
    _initExtensionListener();
    _checkExtension();
  }

  Future<void> _loadMissingInfo() async {
    try {
      final api = context.read<ApiService>();
      final resp = await api.get('/api/v1/profile/missing-info');
      if (!mounted) return;
      final data = resp.data as Map<String, dynamic>;
      setState(() {
        _autofillCompleteness = (data['completeness'] ?? 100) as int;
        _missingCount = ((data['missing'] as List?) ?? []).length;
      });
    } catch (_) { /* non-blocking */ }
    // Kick off insights too — it reads from the same Discover cache and
    // is safe to call even before the user has uploaded a resume.
    _loadInsights();
  }

  Future<void> _loadInsights() async {
    if (_insightsLoading) return;
    setState(() { _insightsLoading = true; _insightsError = null; });
    try {
      final api = context.read<ApiService>();
      final resp = await api.get('/api/v1/resume/insights');
      if (!mounted) return;
      dynamic raw = resp.data;
      if (raw is String) {
        try { raw = jsonDecode(raw); } catch (_) {}
      }
      setState(() => _insights = (raw is Map) ? Map<String, dynamic>.from(raw) : null);
    } catch (e) {
      if (mounted) setState(() => _insightsError = e.toString());
    } finally {
      if (mounted) setState(() => _insightsLoading = false);
    }
  }

  Future<void> _suggestImprovements({String targetRole = ''}) async {
    if (_suggesting) return;
    if (!await ensureResumeUploaded(context, action: 'tailor your resume')) {
      return;
    }

    // Pull the candidate's currently-saved industry + role keywords so the
    // dialog opens pre-populated with what they used most recently.
    final profile = context.read<ProfileProvider>().profile;
    final prefs = (profile?['preferences'] as Map?) ?? const {};
    final savedIndustry = (prefs['industry']?.toString() ?? '').toLowerCase();
    String selectedIndustry = kIndustries.any((i) => i.id == savedIndustry)
        ? savedIndustry
        : 'tech';
    final savedKeywords = <String>[
      for (final k in (prefs['keywords'] as List?) ?? const [])
        if (k.toString().trim().isNotEmpty) k.toString().trim(),
    ];
    final initialTitles = <String>{
      ...savedKeywords.take(4),
      if (targetRole.isNotEmpty && !savedKeywords.contains(targetRole)) targetRole,
    }.toList();

    final picked = await showTailorResumeDialog(
      context,
      initialIndustryId: selectedIndustry,
      initialTitles: initialTitles,
    );
    if (picked == null || !mounted) return;

    setState(() => _suggesting = true);
    try {
      final api = context.read<ApiService>();
      final titles = picked.titles;
      // Body intentionally omits `jobs` so the backend pulls the user's
      // most recent Discover results (top-scoring first) AND mines
      // cross-resume signal from peer profiles.
      final resp = await api.post('/api/v1/resume/suggest-improvements',
          data: {
            'industry': picked.industry,
            'targetRole': titles.isNotEmpty ? titles.first : targetRole,
            'targetTitles': titles,
          });
      dynamic raw = resp.data;
      if (raw is String) {
        try { raw = jsonDecode(raw); } catch (_) {}
      }
      final data = (raw is Map) ? Map<String, dynamic>.from(raw) : <String, dynamic>{};
      if (!mounted) return;
      final md = (data['suggestionsMarkdown'] as String? ?? '').trim();
      final noJobsMsg = (data['noJobsMessage'] as String? ?? '').trim();
      await showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Resume tailoring suggestions'),
          content: SizedBox(
            width: 600,
            child: SingleChildScrollView(child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                if (md.isNotEmpty)
                  SimpleMarkdown(md)
                else if (noJobsMsg.isNotEmpty)
                  Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    const Icon(Icons.info_outline, size: 18, color: AppTheme.primary),
                    const SizedBox(width: 8),
                    Expanded(child: Text(noJobsMsg,
                        style: const TextStyle(color: AppTheme.textSecondary))),
                  ])
                else
                  const Text('AI suggestions unavailable right now — try again in a moment.',
                      style: TextStyle(color: AppTheme.textSecondary)),
              ],
            )),
          ),
          actions: [TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Close'))],
        ),
      );
    } on DioException catch (e) {
      if (!mounted) return;
      String msg = 'Could not generate suggestions. ';
      final respData = e.response?.data;
      if (respData is Map) {
        final serverMsg = (respData['error']?['message'] as String?) ??
            (respData['message'] as String?);
        if (serverMsg != null && serverMsg.isNotEmpty) {
          msg = serverMsg;
        } else if (e.response?.statusCode == 401) {
          msg += 'Session expired — please sign out and sign in again.';
        } else if ((e.response?.statusCode ?? 0) >= 500) {
          msg += 'Server error — try again in a moment.';
        } else {
          msg += 'Please try again.';
        }
      } else if (e.type == DioExceptionType.connectionTimeout ||
          e.type == DioExceptionType.receiveTimeout) {
        msg += 'The AI is taking too long — please try again.';
      } else {
        msg += 'Please try again.';
      }
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(msg), backgroundColor: AppTheme.error,
            duration: const Duration(seconds: 5)),
      );
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not generate suggestions. Please try again.'),
            backgroundColor: AppTheme.error),
      );
    } finally {
      if (mounted) setState(() => _suggesting = false);
    }
  }

  Future<void> _confirmDeleteAccount() async {
    final controller = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete account?'),
        content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Text(
            'This will permanently delete your HirePanda account, profile, '
            'uploaded resumes, and saved answers. This cannot be undone.',
          ),
          const SizedBox(height: 12),
          const Text('Type DELETE to confirm:'),
          const SizedBox(height: 8),
          TextField(controller: controller, decoration: const InputDecoration(border: OutlineInputBorder())),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          ElevatedButton(
            style: ElevatedButton.styleFrom(backgroundColor: Colors.red, foregroundColor: Colors.white),
            onPressed: () => Navigator.pop(ctx, controller.text.trim() == 'DELETE'),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      final api = context.read<ApiService>();
      await api.deleteWithData('/api/v1/account', data: {'confirm': 'DELETE'});
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Could not delete account: $e'), backgroundColor: Colors.red),
        );
      }
      return;
    }
    if (!mounted) return;
    context.read<AuthProvider>().logout();
    context.go('/login');
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Your account has been deleted.')),
    );
  }

  /// Register the cross-window message listener exactly once.
  void _initExtensionListener() {
    _msgSub?.cancel();
    _msgSub = html.window.onMessage.listen((event) {
      final data = event.data;
      if (data is! Map) return;
      final type = data['type'];
      if (type == 'AUTOAPPLY_EXTENSION_STATUS' && data['installed'] == true) {
        if (!mounted) return;
        setState(() => _extStatus = _extStatus == 'connected' ? 'connected' : 'installed');
        _syncTokenToExtension();
      } else if (type == 'AUTOAPPLY_TOKEN_SYNCED' && data['ok'] == true) {
        if (mounted) setState(() => _extStatus = 'connected');
      }
    });
  }

  void _checkExtension() {
    _extProbeTimer?.cancel();
    // Read DOM attribute (set by content.js immediately on load)
    final attr = html.document.documentElement?.getAttribute('data-autoapply-ext') ?? '';
    if (attr == 'connected') {
      setState(() => _extStatus = 'connected');
      return;
    }
    if (attr == 'installed') {
      setState(() => _extStatus = 'installed');
      _syncTokenToExtension();
      return;
    }

    // Attribute not set yet — probe via postMessage. The listener registered in
    // _initExtensionListener will flip state when extension responds.
    setState(() => _extStatus = 'checking');
    html.window.postMessage({'type': 'AUTOAPPLY_CHECK_EXTENSION'}, '*');

    // Re-check the DOM attribute after a short delay (content script may set it
    // after document_idle). If still nothing, mark as not_installed.
    _extProbeTimer = Timer(const Duration(milliseconds: 1200), () {
      if (!mounted) return;
      final retry = html.document.documentElement?.getAttribute('data-autoapply-ext') ?? '';
      if (retry == 'connected') {
        setState(() => _extStatus = 'connected');
      } else if (retry == 'installed') {
        setState(() => _extStatus = 'installed');
        _syncTokenToExtension();
      } else if (_extStatus == 'checking') {
        setState(() => _extStatus = 'not_installed');
      }
    });
  }

  void _syncTokenToExtension() {
    final token = html.window.localStorage['auth_token'];
    if (token != null && token.isNotEmpty) {
      html.window.postMessage({'type': 'AUTOAPPLY_SYNC_TOKEN', 'token': token}, '*');
    }
  }

  @override
  void dispose() {
    _skillCtrl.dispose();
    _msgSub?.cancel();
    _extProbeTimer?.cancel();
    super.dispose();
  }

  Widget _buildInsightsCard() {
    final ins = _insights;
    final missing = (ins?['topMissingKeywords'] as List?) ?? const [];
    final matched = (ins?['topMatchedKeywords'] as List?) ?? const [];
    final headline = (ins?['suggestedHeadline'] ?? '').toString();
    final highCount = (ins?['highScoreCount'] ?? 0) as int;
    final totalAnalysed = (ins?['totalJobsAnalysed'] ?? 0) as int;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              const Icon(Icons.insights, color: AppTheme.primary, size: 20),
              const SizedBox(width: 8),
              const Expanded(child: Text('Resume insights',
                  style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15))),
              IconButton(
                tooltip: 'Refresh',
                icon: _insightsLoading
                    ? const SizedBox(width: 14, height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.refresh, size: 18),
                onPressed: _insightsLoading ? null : _loadInsights,
              ),
            ]),
            const SizedBox(height: 4),
            if (totalAnalysed == 0 && !_insightsLoading)
              const Text(
                'Run a Discover search first \u2014 then come back here to see which '
                'keywords differentiate the jobs you match best.',
                style: TextStyle(color: AppTheme.textSecondary, fontSize: 12),
              )
            else ...[
              Text(
                'Comparing $highCount best-matched job${highCount == 1 ? "" : "s"} '
                'against $totalAnalysed total analysed.',
                style: const TextStyle(color: AppTheme.textSecondary, fontSize: 12),
              ),
              if (missing.isNotEmpty) ...[
                const SizedBox(height: 12),
                const Text('Missing keywords (in your top matches, not on your resume)',
                    style: TextStyle(fontWeight: FontWeight.w500, fontSize: 13)),
                const SizedBox(height: 6),
                Wrap(spacing: 6, runSpacing: 6, children: [
                  for (final k in missing.take(12))
                    Chip(
                      label: Text((k is Map ? k['keyword'] : k).toString(),
                          style: const TextStyle(fontSize: 11)),
                      backgroundColor: AppTheme.error.withValues(alpha: 0.08),
                      side: BorderSide(color: AppTheme.error.withValues(alpha: 0.3)),
                    ),
                ]),
              ],
              if (matched.isNotEmpty) ...[
                const SizedBox(height: 12),
                const Text('Strengths (already on your resume)',
                    style: TextStyle(fontWeight: FontWeight.w500, fontSize: 13)),
                const SizedBox(height: 6),
                Wrap(spacing: 6, runSpacing: 6, children: [
                  for (final k in matched.take(8))
                    Chip(
                      label: Text((k is Map ? k['keyword'] : k).toString(),
                          style: const TextStyle(fontSize: 11)),
                      backgroundColor: AppTheme.success.withValues(alpha: 0.08),
                      side: BorderSide(color: AppTheme.success.withValues(alpha: 0.3)),
                    ),
                ]),
              ],
              if (headline.isNotEmpty) ...[
                const SizedBox(height: 12),
                const Text('Suggested headline',
                    style: TextStyle(fontWeight: FontWeight.w500, fontSize: 13)),
                const SizedBox(height: 4),
                SelectableText(headline,
                    style: const TextStyle(fontSize: 13, fontStyle: FontStyle.italic)),
              ],
            ],
            if (_insightsError != null) ...[
              const SizedBox(height: 8),
              Text('Insights unavailable: $_insightsError',
                  style: const TextStyle(color: AppTheme.error, fontSize: 11)),
            ],
            const SizedBox(height: 12),
            SizedBox(width: double.infinity, child: ElevatedButton.icon(
              onPressed: _suggesting ? null : () => _suggestImprovements(),
              icon: _suggesting
                  ? const SizedBox(width: 16, height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Icon(Icons.auto_awesome),
              label: Text(_suggesting ? 'Asking AI\u2026' : 'Tailor my resume to top matches'),
            )),
          ],
        ),
      ),
    );
  }

  Widget _buildExtensionCard() {
    IconData icon;
    Color iconColor;
    String title;
    String subtitle;
    Widget? action;

    switch (_extStatus) {
      case 'connected':
        icon = Icons.extension;
        iconColor = AppTheme.success;
        title = 'Chrome Extension Connected';
        subtitle = 'Autofill is ready. Visit any job application and click the extension icon.';
        action = null;
        break;
      case 'installed':
        icon = Icons.extension;
        iconColor = Colors.orange;
        title = 'Extension Installed — Syncing...';
        subtitle = 'Token is being synced automatically.';
        action = TextButton(
          onPressed: _syncTokenToExtension,
          child: const Text('Retry Sync'),
        );
        break;
      default: // not_installed or checking
        icon = Icons.extension_off;
        iconColor = AppTheme.textSecondary;
        title = 'Install Chrome Extension';
        subtitle = 'Autofill job application forms with one click.';
        action = Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SizedBox(height: 12),
            SizedBox(width: double.infinity, child: FilledButton.icon(
              onPressed: () => html.window.open(
                'https://chromewebstore.google.com/detail/autoapply-%E2%80%93-job-form-auto/anjgpjhdecnibcbogkclafanemofndea',
                '_blank',
              ),
              icon: const Icon(Icons.open_in_new, size: 18),
              label: const Text('Install from Chrome Web Store'),
              style: FilledButton.styleFrom(
                backgroundColor: AppTheme.primary,
                padding: const EdgeInsets.symmetric(vertical: 12),
              ),
            )),
            const SizedBox(height: 10),
            const Text(
              'After installing, come back here — it will connect automatically.',
              style: TextStyle(fontSize: 12, color: Colors.black54, fontStyle: FontStyle.italic),
            ),
            const SizedBox(height: 12),
            SizedBox(width: double.infinity, child: OutlinedButton.icon(
              onPressed: () => _checkExtension(),
              icon: const Icon(Icons.refresh, size: 16),
              label: const Text('Check Again'),
            )),
          ],
        );
    }

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              Icon(icon, color: iconColor, size: 24),
              const SizedBox(width: 12),
              Expanded(child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
                  const SizedBox(height: 2),
                  Text(subtitle, style: const TextStyle(color: AppTheme.textSecondary, fontSize: 12)),
                ],
              )),
              if (_extStatus == 'connected')
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                  decoration: BoxDecoration(
                    color: AppTheme.success.withValues(alpha: 0.1),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: const Text('Active', style: TextStyle(color: AppTheme.success, fontSize: 11, fontWeight: FontWeight.w600)),
                ),
            ]),
            if (action != null) ...[const SizedBox(height: 8), action],
          ],
        ),
      ),
    );
  }

  Widget _setupStep(String number, String text) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 20, height: 20,
            decoration: BoxDecoration(color: AppTheme.primary, borderRadius: BorderRadius.circular(10)),
            child: Center(child: Text(number, style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.bold))),
          ),
          const SizedBox(width: 8),
          Expanded(child: Text(text, style: const TextStyle(fontSize: 12))),
        ],
      ),
    );
  }

  void _downloadExtension() {
    // The ZIP is bundled into the Flutter web build at /autoapply-extension.zip
    // (sourced from app/web/autoapply-extension.zip, regenerated by tools/build_extension_zip.ps1)
    final anchor = html.AnchorElement(href: '/autoapply-extension.zip')
      ..download = 'autoapply-extension.zip'
      ..target = '_self';
    html.document.body?.append(anchor);
    anchor.click();
    anchor.remove();
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Downloading extension... unzip it next.'), duration: Duration(seconds: 3)),
      );
    }
  }

  Future<void> _copyChromeExtensionsUrl() async {
    await Clipboard.setData(const ClipboardData(text: 'chrome://extensions'));
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Copied! Open a new Chrome tab, paste in the address bar, press Enter.'),
          duration: Duration(seconds: 4),
        ),
      );
    }
  }

  Widget _buildAutofillReadinessCard() {
    final pct = _autofillCompleteness.clamp(0, 100);
    final color = pct >= 80 ? AppTheme.success : (pct >= 50 ? Colors.orange : Colors.red);
    return Card(
      color: color.withValues(alpha: 0.05),
      shape: RoundedRectangleBorder(
        side: BorderSide(color: color.withValues(alpha: 0.3)),
        borderRadius: BorderRadius.circular(12),
      ),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => context.push('/application-details').then((_) => _loadMissingInfo()),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(children: [
                Icon(Icons.auto_awesome, color: color, size: 22),
                const SizedBox(width: 10),
                Expanded(child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Improve Autofill', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 14, color: color)),
                    const SizedBox(height: 2),
                    Text(
                      '$_missingCount common question${_missingCount == 1 ? "" : "s"} missing — fill them once to skip the popup on every job.',
                      style: const TextStyle(fontSize: 12, color: AppTheme.textSecondary),
                    ),
                  ],
                )),
                const Icon(Icons.chevron_right),
              ]),
              const SizedBox(height: 12),
              ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: LinearProgressIndicator(
                  value: pct / 100,
                  minHeight: 6,
                  backgroundColor: color.withValues(alpha: 0.15),
                  valueColor: AlwaysStoppedAnimation(color),
                ),
              ),
              const SizedBox(height: 6),
              Text('$pct% complete', style: TextStyle(fontSize: 11, color: color, fontWeight: FontWeight.w600)),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: const Text('Profile'),
        actions: [
          Consumer<ProfileProvider>(
            builder: (_, pp, __) {
              if (pp.isPremium) {
                return Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 10),
                  child: ActionChip(
                    avatar: const Icon(Icons.workspace_premium_rounded,
                        color: Colors.white, size: 18),
                    backgroundColor: AppTheme.primary,
                    label: const Text('Pro',
                        style: TextStyle(color: Colors.white, fontWeight: FontWeight.w700)),
                    onPressed: () => context.push('/subscription'),
                  ),
                );
              }
              return Padding(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 10),
                child: FilledButton.icon(
                  onPressed: () => context.push('/pricing'),
                  icon: const Icon(Icons.auto_awesome, size: 16),
                  label: const Text('Upgrade'),
                  style: FilledButton.styleFrom(
                    backgroundColor: AppTheme.primary,
                    padding: const EdgeInsets.symmetric(horizontal: 14),
                    visualDensity: VisualDensity.compact,
                  ),
                ),
              );
            },
          ),
          IconButton(icon: const Icon(Icons.logout), tooltip: 'Logout',
            onPressed: () { context.read<AuthProvider>().logout(); context.go('/login'); }),
          PopupMenuButton<String>(
            tooltip: 'More',
            onSelected: (v) { if (v == 'delete') _confirmDeleteAccount(); },
            itemBuilder: (_) => const [
              PopupMenuItem(
                value: 'delete',
                child: Row(children: [
                  Icon(Icons.delete_forever, color: Colors.red),
                  SizedBox(width: 8),
                  Text('Delete my account', style: TextStyle(color: Colors.red)),
                ]),
              ),
            ],
          ),
        ],
      ),
      body: Consumer<ProfileProvider>(builder: (_, pp, __) {
        if (pp.loading && pp.profile == null) return const Center(child: CircularProgressIndicator());

        final name = '${pp.personal['firstName'] ?? ''} ${pp.personal['lastName'] ?? ''}'.trim();
        final displayName = name.isNotEmpty ? name : (context.read<AuthProvider>().name ?? '');
        final skills = pp.technicalSkills;
        final experience = pp.experience;
        final education = pp.education;
        final prefs = pp.preferences;

        return RefreshIndicator(
          onRefresh: () => pp.loadProfile(),
          child: ListView(
            padding: const EdgeInsets.all(16),
            children: [
              // Header
              Card(child: Padding(padding: const EdgeInsets.all(20), child: Row(children: [
                CircleAvatar(radius: 30, backgroundColor: AppTheme.primary,
                  child: Text(displayName.isNotEmpty ? displayName[0].toUpperCase() : '?',
                      style: const TextStyle(fontSize: 24, color: Colors.white))),
                const SizedBox(width: 16),
                Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(displayName.isNotEmpty ? displayName : 'Your Name',
                      style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
                  Text(context.read<AuthProvider>().email ?? '',
                      style: const TextStyle(color: AppTheme.textSecondary)),
                ])),
              ]))),
              const SizedBox(height: 16),

              // Application Details button
              Card(child: ListTile(
                leading: const Icon(Icons.assignment, color: AppTheme.primary),
                title: const Text('Application Details', style: TextStyle(fontWeight: FontWeight.w600)),
                subtitle: const Text('Address, visa, salary, cover letter — for autofill', style: TextStyle(fontSize: 12)),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => context.push('/application-details').then((_) => _loadMissingInfo()),
              )),
              const SizedBox(height: 8),

              // Subscription / Pricing tile — visible to everyone
              Card(child: ListTile(
                leading: const Icon(Icons.workspace_premium_rounded, color: AppTheme.primary),
                title: const Text('Subscription & billing', style: TextStyle(fontWeight: FontWeight.w600)),
                subtitle: const Text('View plan, invoices, manage payment', style: TextStyle(fontSize: 12)),
                trailing: const Icon(Icons.chevron_right),
                onTap: () {
                  context.push(pp.isPremium ? '/subscription' : '/pricing');
                },
              )),
              const SizedBox(height: 12),

              // Autofill readiness card — only shows if there are missing fields
              if (_missingCount > 0) ...[
                _buildAutofillReadinessCard(),
                const SizedBox(height: 12),
              ],

              // Chrome Extension card
              _buildExtensionCard(),
              const SizedBox(height: 16),

              // Resume
              _SectionCard(title: 'Resume', icon: Icons.description, child: Column(
                crossAxisAlignment: CrossAxisAlignment.start, children: [
                  if (pp.resumeUrl != null) Row(children: [
                    const Icon(Icons.check_circle, color: AppTheme.success, size: 16),
                    const SizedBox(width: 4),
                    Text('Resume uploaded (v${pp.resumeVersion})', style: const TextStyle(color: AppTheme.success, fontSize: 13)),
                  ]),
                  const SizedBox(height: 8),
                  const Text('Upload PDF to auto-extract skills, education & experience.',
                      style: TextStyle(fontSize: 12, color: AppTheme.textSecondary)),
                  const SizedBox(height: 8),
                  SizedBox(width: double.infinity, child: ElevatedButton.icon(
                    onPressed: _uploadingResume ? null : _pickAndUploadResume,
                    icon: _uploadingResume ? const SizedBox(width: 16, height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white)) : const Icon(Icons.upload_file),
                    label: Text(_uploadingResume ? 'Uploading...' : 'Upload Resume (PDF)'),
                  )),
                ],
              )),
              const SizedBox(height: 12),

              // Resume insights — keyword gap vs the user's best-matched jobs.
              _buildInsightsCard(),
              const SizedBox(height: 12),

              // Skills
              _SectionCard(title: 'Skills (${skills.length})', icon: Icons.code, child: Column(
                crossAxisAlignment: CrossAxisAlignment.start, children: [
                  if (skills.isNotEmpty) Wrap(spacing: 6, runSpacing: 6, children: skills.map((s) => Chip(
                    label: Text(s), deleteIcon: const Icon(Icons.close, size: 16),
                    onDeleted: () => _removeSkill(s))).toList()),
                  if (skills.isEmpty) const Text('No skills added. Upload resume or add manually.',
                      style: TextStyle(color: AppTheme.textSecondary, fontSize: 13)),
                  const SizedBox(height: 8),
                  Row(children: [
                    Expanded(child: TextField(controller: _skillCtrl,
                      decoration: const InputDecoration(hintText: 'Add a skill (e.g. Python)', border: OutlineInputBorder(), isDense: true),
                      onSubmitted: (_) => _addSkill())),
                    const SizedBox(width: 8),
                    IconButton(onPressed: _addSkill, icon: const Icon(Icons.add_circle, color: AppTheme.primary)),
                  ]),
                ],
              )),
              const SizedBox(height: 12),

              // Education
              _SectionCard(title: 'Education (${education.length})', icon: Icons.school, child: Column(
                crossAxisAlignment: CrossAxisAlignment.start, children: [
                  if (education.isEmpty) const Text('No education added.', style: TextStyle(color: AppTheme.textSecondary, fontSize: 13)),
                  ...education.asMap().entries.map((entry) {
                    final i = entry.key;
                    final edu = entry.value as Map<String, dynamic>;
                    return ListTile(
                      contentPadding: EdgeInsets.zero, dense: true,
                      title: Text(edu['degree'] ?? edu['university'] ?? '', style: const TextStyle(fontWeight: FontWeight.w600)),
                      subtitle: Text([edu['university'], edu['year']?.toString()].where((s) => s != null && s.isNotEmpty).join(' • ')),
                      trailing: IconButton(icon: const Icon(Icons.delete_outline, size: 18, color: AppTheme.error),
                        onPressed: () {
                          final list = List<dynamic>.from(education); list.removeAt(i);
                          pp.updateProfile({'education': list});
                        }),
                    );
                  }),
                  const SizedBox(height: 8),
                  OutlinedButton.icon(
                    onPressed: () => _showAddEducationDialog(pp),
                    icon: const Icon(Icons.add, size: 18), label: const Text('Add Education')),
                ],
              )),
              const SizedBox(height: 12),

              // Experience
              _SectionCard(title: 'Experience (${experience.length})', icon: Icons.work_outline, child: Column(
                crossAxisAlignment: CrossAxisAlignment.start, children: [
                  if (experience.isEmpty) const Text('No experience added.', style: TextStyle(color: AppTheme.textSecondary, fontSize: 13)),
                  ...experience.asMap().entries.map((entry) {
                    final i = entry.key;
                    final exp = entry.value as Map<String, dynamic>;
                    return ListTile(
                      contentPadding: EdgeInsets.zero, dense: true,
                      title: Text(exp['title'] ?? '', style: const TextStyle(fontWeight: FontWeight.w600)),
                      subtitle: Text([exp['company'], exp['from'], exp['to']].where((s) => s != null && s.toString().isNotEmpty).join(' • ')),
                      trailing: IconButton(icon: const Icon(Icons.delete_outline, size: 18, color: AppTheme.error),
                        onPressed: () {
                          final list = List<dynamic>.from(experience); list.removeAt(i);
                          pp.updateProfile({'experience': list});
                        }),
                    );
                  }),
                  const SizedBox(height: 8),
                  OutlinedButton.icon(
                    onPressed: () => _showAddExperienceDialog(pp),
                    icon: const Icon(Icons.add, size: 18), label: const Text('Add Experience')),
                ],
              )),
              const SizedBox(height: 12),

              // Job-search preferences (keywords, locations, years of
              // experience) live on the Discover screen now so users can
              // tweak and re-run their search without bouncing into Profile.
              _SectionCard(
                title: 'Job Preferences',
                icon: Icons.tune,
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 4),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Icon(Icons.info_outline,
                          size: 18, color: AppTheme.textSecondary),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text(
                              'Job titles, locations and years of experience '
                              'are now managed on the Discover page so they '
                              'travel with each search.',
                              style: TextStyle(
                                  fontSize: 13,
                                  color: AppTheme.textSecondary,
                                  height: 1.45),
                            ),
                            const SizedBox(height: 8),
                            OutlinedButton.icon(
                              onPressed: () => context.go('/'),
                              icon: const Icon(Icons.search, size: 16),
                              label: const Text('Open Discover'),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              // ── Legal links ────────────────────────────────────────────
              _SectionCard(
                title: 'Legal & Support',
                icon: Icons.gavel_rounded,
                child: Column(children: [
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    dense: true,
                    leading: const Icon(Icons.headset_mic_rounded, color: AppTheme.primary, size: 20),
                    title: const Text('Contact & Support'),
                    trailing: const Icon(Icons.chevron_right, size: 18),
                    onTap: () => context.push('/contact'),
                  ),
                  const Divider(height: 1),
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    dense: true,
                    leading: const Icon(Icons.privacy_tip_outlined, color: AppTheme.primary, size: 20),
                    title: const Text('Privacy Policy'),
                    trailing: const Icon(Icons.chevron_right, size: 18),
                    onTap: () => context.push('/privacy'),
                  ),
                  const Divider(height: 1),
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    dense: true,
                    leading: const Icon(Icons.description_outlined, color: AppTheme.primary, size: 20),
                    title: const Text('Terms & Conditions'),
                    trailing: const Icon(Icons.chevron_right, size: 18),
                    onTap: () => context.push('/terms'),
                  ),
                  const Divider(height: 1),
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    dense: true,
                    leading: const Icon(Icons.receipt_long_outlined, color: AppTheme.primary, size: 20),
                    title: const Text('Refund Policy'),
                    trailing: const Icon(Icons.chevron_right, size: 18),
                    onTap: () => context.push('/refund'),
                  ),
                ]),
              ),
              const SizedBox(height: 40),
            ],
          ),
        );
      }),
    );
  }

  // ── Resume Upload ─────────────────────────────────────────────────────────

  Future<void> _pickAndUploadResume() async {
    final completer = Completer<List<int>?>();
    final input = html.FileUploadInputElement()..accept = '.pdf';
    input.click();
    input.onChange.listen((event) {
      final files = input.files;
      if (files == null || files.isEmpty) { completer.complete(null); return; }
      final reader = html.FileReader();
      reader.readAsArrayBuffer(files[0]);
      reader.onLoadEnd.listen((_) {
        try {
          final result = reader.result;
          if (result is List<int>) {
            completer.complete(result);
          } else if (result is ByteBuffer) {
            completer.complete(result.asUint8List());
          } else if (result != null) {
            // Fallback: try converting via typed_data
            completer.complete(Uint8List.view(result as ByteBuffer));
          } else {
            completer.complete(null);
          }
        } catch (e) {
          debugPrint('[resume] FileReader result cast failed: $e');
          completer.complete(null);
        }
      });
      reader.onError.listen((_) => completer.complete(null));
    });
    final bytes = await completer.future;
    if (bytes == null || bytes.isEmpty) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Could not read the file. Try a different PDF.'), backgroundColor: AppTheme.error));
      }
      return;
    }

    if (bytes.length > 10 * 1024 * 1024) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('File too large (max 10 MB).'), backgroundColor: AppTheme.error));
      }
      return;
    }

    setState(() => _uploadingResume = true);
    try {
      final api = context.read<ApiService>();
      final resp = await api.post('/api/v1/profile/resume', data: {'fileBase64': base64Encode(bytes), 'fileName': 'resume.pdf'});
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(resp.data['message']?.toString() ?? 'Resume uploaded'), backgroundColor: AppTheme.success));
        context.read<ProfileProvider>().loadProfile();
      }
    } catch (e) {
      debugPrint('[resume] upload error: $e');
      String msg = 'Upload failed';
      final es = e.toString();
      if (es.contains('401')) {
        msg = 'Session expired. Please login again.';
      } else if (es.contains('413') || es.contains('too large')) {
        msg = 'File too large. Try a smaller PDF.';
      } else if (es.contains('timeout') || es.contains('Timeout')) {
        msg = 'Upload timed out. Check your connection and try again.';
      } else if (es.contains('connection') || es.contains('XMLHttp') || es.contains('network')) {
        msg = 'Network error. Check your internet connection.';
      } else if (es.contains('500') || es.contains('INTERNAL')) {
        msg = 'Server error processing resume. Try again in a moment.';
      } else {
        // Show truncated actual error for debugging
        msg = 'Upload failed: ${es.length > 120 ? es.substring(0, 120) : es}';
      }
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(msg), backgroundColor: AppTheme.error, duration: const Duration(seconds: 6)));
      }
    }
    if (mounted) setState(() => _uploadingResume = false);
  }

  // ── Skills ────────────────────────────────────────────────────────────────

  void _addSkill() {
    final skill = _skillCtrl.text.trim();
    if (skill.isEmpty) return;
    final pp = context.read<ProfileProvider>();
    final current = pp.technicalSkills;
    if (!current.contains(skill)) {
      pp.updateProfile({'skills': {'technical': [...current, skill]}});
    }
    _skillCtrl.clear();
  }

  void _removeSkill(String skill) {
    final pp = context.read<ProfileProvider>();
    final current = List<String>.from(pp.technicalSkills);
    current.remove(skill);
    pp.updateProfile({'skills': {'technical': current}});
  }

  // ── Add Education Dialog ──────────────────────────────────────────────────

  void _showAddEducationDialog(ProfileProvider pp) {
    final degreeCtrl = TextEditingController();
    final uniCtrl = TextEditingController();
    final yearCtrl = TextEditingController();
    showDialog(context: context, builder: (ctx) => AlertDialog(
      title: const Text('Add Education'),
      content: Column(mainAxisSize: MainAxisSize.min, children: [
        TextField(controller: degreeCtrl, decoration: const InputDecoration(labelText: 'Degree (e.g. B.Tech Computer Science)', border: OutlineInputBorder(), isDense: true)),
        const SizedBox(height: 12),
        TextField(controller: uniCtrl, decoration: const InputDecoration(labelText: 'University / College', border: OutlineInputBorder(), isDense: true)),
        const SizedBox(height: 12),
        TextField(controller: yearCtrl, decoration: const InputDecoration(labelText: 'Graduation Year', border: OutlineInputBorder(), isDense: true), keyboardType: TextInputType.number),
      ]),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
        ElevatedButton(onPressed: () {
          if (degreeCtrl.text.trim().isEmpty && uniCtrl.text.trim().isEmpty) return;
          final edu = List<dynamic>.from(pp.education);
          edu.add({'degree': degreeCtrl.text.trim(), 'university': uniCtrl.text.trim(), 'year': yearCtrl.text.trim()});
          pp.updateProfile({'education': edu});
          Navigator.pop(ctx);
        }, child: const Text('Add')),
      ],
    ));
  }

  // ── Add Experience Dialog ─────────────────────────────────────────────────

  void _showAddExperienceDialog(ProfileProvider pp) {
    final titleCtrl = TextEditingController();
    final companyCtrl = TextEditingController();
    final fromCtrl = TextEditingController();
    final toCtrl = TextEditingController();
    showDialog(context: context, builder: (ctx) => AlertDialog(
      title: const Text('Add Experience'),
      content: Column(mainAxisSize: MainAxisSize.min, children: [
        TextField(controller: titleCtrl, decoration: const InputDecoration(labelText: 'Job Title', border: OutlineInputBorder(), isDense: true)),
        const SizedBox(height: 12),
        TextField(controller: companyCtrl, decoration: const InputDecoration(labelText: 'Company', border: OutlineInputBorder(), isDense: true)),
        const SizedBox(height: 12),
        Row(children: [
          Expanded(child: TextField(controller: fromCtrl, decoration: const InputDecoration(labelText: 'From (e.g. 2022)', border: OutlineInputBorder(), isDense: true))),
          const SizedBox(width: 8),
          Expanded(child: TextField(controller: toCtrl, decoration: const InputDecoration(labelText: 'To (or Present)', border: OutlineInputBorder(), isDense: true))),
        ]),
      ]),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
        ElevatedButton(onPressed: () {
          if (titleCtrl.text.trim().isEmpty) return;
          final exp = List<dynamic>.from(pp.experience);
          exp.add({'title': titleCtrl.text.trim(), 'company': companyCtrl.text.trim(), 'from': fromCtrl.text.trim(), 'to': toCtrl.text.trim()});
          pp.updateProfile({'experience': exp});
          Navigator.pop(ctx);
        }, child: const Text('Add')),
      ],
    ));
  }
}

// ── Shared Widgets ──────────────────────────────────────────────────────────

class _SectionCard extends StatelessWidget {
  final String title;
  final IconData icon;
  final Widget child;
  const _SectionCard({required this.title, required this.icon, required this.child});
  @override
  Widget build(BuildContext context) {
    return Card(child: Padding(padding: const EdgeInsets.all(16), child: Column(
      crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(icon, size: 20, color: AppTheme.primary), const SizedBox(width: 8),
          Text(title, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 16)),
        ]),
        const SizedBox(height: 12), child,
      ],
    )));
  }
}

class _AddChipField extends StatefulWidget {
  final String hint;
  final ValueChanged<String> onAdd;
  const _AddChipField({required this.hint, required this.onAdd});
  @override
  State<_AddChipField> createState() => _AddChipFieldState();
}

class _AddChipFieldState extends State<_AddChipField> {
  final _ctrl = TextEditingController();
  @override
  void dispose() { _ctrl.dispose(); super.dispose(); }
  void _submit() { final v = _ctrl.text.trim(); if (v.isNotEmpty) { widget.onAdd(v); _ctrl.clear(); } }
  @override
  Widget build(BuildContext context) {
    return Row(children: [
      Expanded(child: TextField(controller: _ctrl,
        decoration: InputDecoration(hintText: widget.hint, border: const OutlineInputBorder(), isDense: true),
        onSubmitted: (_) => _submit())),
      const SizedBox(width: 8),
      IconButton(onPressed: _submit, icon: const Icon(Icons.add_circle, color: AppTheme.primary)),
    ]);
  }
}
