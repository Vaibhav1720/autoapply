import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:go_router/go_router.dart';
import 'package:dio/dio.dart' show CancelToken, DioException, DioExceptionType, Options;
import 'dart:async';
import 'dart:math' as math;
import 'dart:convert';
// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;
import 'package:auto_apply/config/theme.dart';
import 'package:auto_apply/services/api_service.dart';
import 'package:auto_apply/providers/profile_provider.dart';
import 'package:auto_apply/widgets/simple_markdown.dart';
import 'package:auto_apply/widgets/profile_guards.dart';
import 'package:auto_apply/data/search_taxonomy.dart';

class DiscoverScreen extends StatefulWidget {
  const DiscoverScreen({super.key});

  @override
  State<DiscoverScreen> createState() => _DiscoverScreenState();
}

// localStorage key for persisting the last successful Discover result.
// Bump the version suffix if the persisted shape ever changes.
const String _kDiscoverCacheKey = 'autoapply.discover.cache.v1';

/// Convert an ISO-8601 timestamp into a short, human-readable "x ago"
/// string for the job tile recency badge. Returns an empty string when
/// the input is missing or unparseable so callers can hide the badge.
///
/// The verb prefix is up to the caller — this only returns the duration
/// portion (e.g. "5m ago") so we can render either "Posted 5m ago" (real
/// source date) or "Last checked 5m ago" (scraper run time).
String _formatRecency(String? iso, {String emptyLabel = ''}) {
  if (iso == null || iso.isEmpty) return emptyLabel;
  final dt = DateTime.tryParse(iso);
  if (dt == null) return emptyLabel;
  final diff = DateTime.now().toUtc().difference(dt.toUtc());
  if (diff.inMinutes < 1) return 'just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
  if (diff.inHours < 24) return '${diff.inHours}h ago';
  if (diff.inDays < 7) return '${diff.inDays}d ago';
  if (diff.inDays < 30) return '${(diff.inDays / 7).floor()}w ago';
  if (diff.inDays < 365) return '${(diff.inDays / 30).floor()}mo ago';
  return '${(diff.inDays / 365).floor()}y ago';
}

class _DiscoverScreenState extends State<DiscoverScreen> {
  List<Map<String, dynamic>> _grouped = [];
  bool _loading = false;
  bool _loadingCached = true;
  bool _cancelled = false;
  // CancelToken shared by every in-flight HTTP request belonging to the
  // CURRENT search. Replaced on each new _discover() run, and `.cancel()`ed
  // on _stopSearch() / dispose() / browser unload so requests are killed
  // server-side instead of just being dropped client-side.
  CancelToken? _searchCancelToken;
  // Browser beforeunload subscription so we can cancel in-flight requests
  // when the user reloads or closes the tab.
  StreamSubscription<html.Event>? _unloadSub;
  String? _error;
  int _totalFound = 0;
  int _companiesScanned = 0;
  int _companiesTotal = 0;
  String _scrapedAt = '';

  // Currently expanded company tile (only one at a time).
  String? _selectedCompany;

  // ── Search controls (multi-value) ───────────────────────────────────
  final List<String> _titles = <String>[];
  final List<String> _locations = <String>[];
  final _queryCtrl = TextEditingController();
  final _locationCtrl = TextEditingController();
  final _queryFocus = FocusNode();
  final _locationFocus = FocusNode();
  // Industry — controls which roles we suggest in the title autocomplete
  // and is sent to the backend so prompts/scrapers adapt to non-tech users.
  String _industryId = 'tech';

  // ── LinkedIn search state ────────────────────────────────────────────
  Map<String, dynamic>? _linkedInGroup;
  List<Map<String, dynamic>> _linkedInGroups = [];
  bool _linkedInLoading = false;
  String? _linkedInError;
  int? _linkedInPoolSize;
  bool _linkedInExpanded = true;

  // ── Resume tailoring (POST /api/v1/resume/suggest-improvements) ─────
  bool _suggesting = false;

  // ── Debounce: minimum gap between search invocations ─────────────────
  DateTime? _lastSearchTrigger;
  static const Duration _kMinSearchGap = Duration(seconds: 2);

  // ── Dedup guards ─────────────────────────────────────────────────────
  // Signature (titles + locations, normalised) of the last *successful*
  // search, plus when it ran. We use this to short-circuit duplicate clicks
  // that would otherwise re-scrape every selected company for nothing.
  String? _lastSearchSig;
  DateTime? _lastSearchAt;
  String? _lastLinkedInSig;
  DateTime? _lastLinkedInAt;
  // How long a cached result is considered "fresh enough" to skip re-running.
  static const Duration _kDedupWindow = Duration(minutes: 5);

  // Session-scoped flag — only nudge once per browser session so we don't
  // pester the user every time they revisit Discover.
  static const String _kIncompletePromptShownKey =
      'autoapply.discover.incomplete_prompt_shown.v1';

  String _currentSearchSig() {
    final t = [..._allTitles()]..sort();
    final l = [..._allLocations()]..sort();
    return '${t.join("|").toLowerCase()}::${l.join("|").toLowerCase()}';
  }

  @override
  void initState() {
    super.initState();
    _loadCached();
    _prefillFromProfile().then((_) => _maybePromptIncompleteProfile());
    // Browser refresh / tab close: kill any in-flight discover requests so
    // we don't leave the backend churning on work the user has abandoned.
    _unloadSub = html.window.onBeforeUnload.listen((_) {
      try {
        _searchCancelToken?.cancel('page-unload');
      } catch (_) {}
    });
  }

  @override
  void dispose() {
    try {
      _searchCancelToken?.cancel('screen-disposed');
    } catch (_) {}
    _unloadSub?.cancel();
    _queryCtrl.dispose();
    _locationCtrl.dispose();
    _queryFocus.dispose();
    _locationFocus.dispose();
    super.dispose();
  }

  Future<void> _prefillFromProfile() async {
    // Wait one frame so context.read works
    await Future<void>.delayed(Duration.zero);
    if (!mounted) return;
    final pp = context.read<ProfileProvider>();
    if (pp.profile == null) {
      try { await pp.loadProfile(); } catch (_) {}
    }
    final prefs = pp.profile?['preferences'] as Map<String, dynamic>?;
    final kw = (prefs?['keywords'] as List?)?.cast<String>() ?? const [];
    final locs = (prefs?['locations'] as List?)?.cast<String>() ?? const [];
    final savedIndustry = prefs?['industry'] as String?;
    if (mounted) {
      setState(() {
        if (_titles.isEmpty && kw.isNotEmpty) {
          _titles.addAll(kw.take(2).where((s) => s.trim().isNotEmpty));
        }
        if (_locations.isEmpty && locs.isNotEmpty) {
          _locations.addAll(locs.take(2).where((s) => s.trim().isNotEmpty));
        }
        if (savedIndustry != null &&
            kIndustries.any((i) => i.id == savedIndustry)) {
          _industryId = savedIndustry;
        }
      });
    }
  }

  /// First-load nudge: if the profile is missing the basics needed for
  /// good autofill (name, resume, at least one job-title preference), pop a
  /// friendly dialog with a "Go to Profile" CTA. Shown at most once per
  /// browser session.
  Future<void> _maybePromptIncompleteProfile() async {
    if (!mounted) return;
    if (html.window.sessionStorage[_kIncompletePromptShownKey] == '1') return;
    final pp = context.read<ProfileProvider>();
    final profile = pp.profile;
    if (profile == null) return;

    final personal = (profile['personal'] as Map?) ?? const {};
    final docs = (profile['documents'] as Map?) ?? const {};
    final prefs = (profile['preferences'] as Map?) ?? const {};
    final firstName = (personal['firstName'] ?? '').toString().trim();
    final lastName = (personal['lastName'] ?? '').toString().trim();
    final resumeUrl = (docs['resumeUrl'] ?? '').toString().trim();
    final keywords = (prefs['keywords'] as List?) ?? const [];

    final missing = <String>[];
    if (firstName.isEmpty || lastName.isEmpty) missing.add('Name');
    if (resumeUrl.isEmpty) missing.add('Resume');
    if (keywords.isEmpty && _titles.isEmpty) missing.add('Job titles');
    if (missing.isEmpty) return;

    html.window.sessionStorage[_kIncompletePromptShownKey] = '1';
    if (!mounted) return;
    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        title: const Text('Finish setting up your profile'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'A complete profile gives you better matches and lets the '
              'extension autofill applications without re-prompting.',
            ),
            const SizedBox(height: 12),
            const Text('Still missing:',
                style: TextStyle(fontWeight: FontWeight.w600)),
            const SizedBox(height: 6),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final m in missing)
                  Chip(label: Text(m, style: const TextStyle(fontSize: 12))),
              ],
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Later'),
          ),
          ElevatedButton(
            onPressed: () {
              Navigator.of(ctx).pop();
              context.go('/profile');
            },
            child: const Text('Go to Profile'),
          ),
        ],
      ),
    );
  }

  void _onIndustryChanged(String id) {
    if (id == _industryId) return;
    setState(() => _industryId = id);
    // Persist the choice so the next session and the backend prompts use it.
    final pp = context.read<ProfileProvider>();
    final existing =
        Map<String, dynamic>.from(pp.profile?['preferences'] as Map? ?? {});
    existing['industry'] = id;
    // Fire-and-forget — don't block the UI on the network.
    pp.updateProfile({'preferences': existing}).catchError((_) {});
  }

  /// Save the chips currently in the search bar to `profile.preferences`
  /// so the tailor flow (and any other downstream surface) can read the
  /// user's CURRENT intent. Fire-and-forget; don't block the search.
  void _persistSearchPreferences() {
    try {
      final pp = context.read<ProfileProvider>();
      final existing =
          Map<String, dynamic>.from(pp.profile?['preferences'] as Map? ?? {});
      final titles = _allTitles().take(6).toList();
      final locs = _allLocations().take(6).toList();
      bool dirty = false;
      final prevKw = (existing['keywords'] as List?)?.cast<String>() ?? const [];
      final prevLoc = (existing['locations'] as List?)?.cast<String>() ?? const [];
      if (titles.isNotEmpty &&
          (prevKw.length != titles.length ||
              !List.generate(titles.length, (i) => prevKw[i] == titles[i])
                  .every((b) => b))) {
        existing['keywords'] = titles;
        dirty = true;
      }
      if (locs.isNotEmpty &&
          (prevLoc.length != locs.length ||
              !List.generate(locs.length, (i) => prevLoc[i] == locs[i])
                  .every((b) => b))) {
        existing['locations'] = locs;
        dirty = true;
      }
      if (dirty) {
        pp.updateProfile({'preferences': existing}).catchError((_) {});
      }
    } catch (_) {}
  }

  /// Expand each title into its known synonyms (if any) so the scrapers
  /// run multiple variants and we get better recall. The original title
  /// is always included first.
  List<String> _expandedQueries() {
    final out = <String>[];
    final seen = <String>{};
    for (final t in _allTitles()) {
      final canonical = t.trim();
      if (canonical.isEmpty) continue;
      final key = canonical.toLowerCase();
      if (seen.add(key)) out.add(canonical);
      final syns = kRoleSynonyms[canonical];
      if (syns != null) {
        for (final s in syns) {
          if (seen.add(s.toLowerCase())) out.add(s);
        }
      }
    }
    return out;
  }

  Future<void> _loadCached() async {
    // Restore the last successful search result (if any) from localStorage so
    // a page refresh / tab reopen shows the previous list immediately and the
    // user only re-runs a search when they explicitly want fresh data.
    try {
      final raw = html.window.localStorage[_kDiscoverCacheKey];
      if (raw != null && raw.isNotEmpty) {
        final dynamic decoded = jsonDecode(raw);
        if (decoded is Map) {
          final groupsRaw = decoded['groups'];
          final List<Map<String, dynamic>> groups = <Map<String, dynamic>>[];
          if (groupsRaw is List) {
            for (final g in groupsRaw) {
              if (g is! Map) continue;
              final m = Map<String, dynamic>.from(g);
              // Drop legacy cached empty / slug-named entries (e.g. "comp-uber")
              // so users on an older cache don't see them after this fix.
              final jobs = m['jobs'];
              if (jobs is! List || jobs.isEmpty) continue;
              final name = (m['company'] ?? '').toString();
              if (name.toLowerCase().startsWith('comp-')) continue;
              groups.add(m);
            }
          }
          final liRaw = decoded['linkedIn'];
          Map<String, dynamic>? li;
          if (liRaw is Map) li = Map<String, dynamic>.from(liRaw);

          final liGroupsRaw = decoded['linkedInGroups'];
          final restoredLiGroups = <Map<String, dynamic>>[];
          if (liGroupsRaw is List) {
            for (final g in liGroupsRaw) {
              if (g is Map) restoredLiGroups.add(Map<String, dynamic>.from(g));
            }
          }

          final titlesRaw = decoded['titles'];
          final locsRaw = decoded['locations'];

          if (mounted) {
            setState(() {
              _grouped = groups;
              _linkedInGroup = li;
              _linkedInGroups = restoredLiGroups;
              _linkedInPoolSize = decoded['linkedInPoolSize'] as int?;
              _totalFound = (decoded['totalFound'] is num)
                  ? (decoded['totalFound'] as num).toInt()
                  : 0;
              _companiesScanned = (decoded['companiesScanned'] is num)
                  ? (decoded['companiesScanned'] as num).toInt()
                  : 0;
              _companiesTotal = (decoded['companiesTotal'] is num)
                  ? (decoded['companiesTotal'] as num).toInt()
                  : 0;
              _scrapedAt = decoded['scrapedAt']?.toString() ?? '';
              if (titlesRaw is List && _titles.isEmpty) {
                _titles.addAll(titlesRaw
                    .map((e) => e.toString())
                    .where((s) => s.trim().isNotEmpty));
              }
              if (locsRaw is List && _locations.isEmpty) {
                _locations.addAll(locsRaw
                    .map((e) => e.toString())
                    .where((s) => s.trim().isNotEmpty));
              }
              // Restore dedup memory so an in-session second search with
              // the same chips doesn't re-scrape. We intentionally do NOT
              // restore `_lastSearchAt` — a page reload should let the
              // very next Search click run fresh instead of popping the
              // "cached results" dialog.
              final sigRaw = decoded['lastSearchSig'];
              if (sigRaw is String && sigRaw.isNotEmpty) {
                _lastSearchSig = sigRaw;
              }
            });
          }
        }
      }
    } catch (e) {
      debugPrint('[discover] cache restore failed: $e');
    }
    if (mounted) setState(() => _loadingCached = false);
  }

  /// Persist the current Discover view to localStorage so it survives a
  /// page refresh. Best-effort — failures are logged but not surfaced.
  void _saveCache() {
    try {
      final payload = <String, dynamic>{
        'groups': _grouped,
        'linkedIn': _linkedInGroup,
        'linkedInGroups': _linkedInGroups,
        'linkedInPoolSize': _linkedInPoolSize,
        'totalFound': _totalFound,
        'companiesScanned': _companiesScanned,
        'companiesTotal': _companiesTotal,
        'scrapedAt': _scrapedAt,
        'titles': _titles,
        'locations': _locations,
        // Persist dedup memory across page reloads — without this, a browser
        // refresh would invalidate the dedup window and the next search
        // gesture (e.g. pull-to-refresh) would re-scrape everything.
        'lastSearchSig': _lastSearchSig,
        // NOTE: we intentionally do NOT persist `lastSearchAt`. The dedup
        // window is in-memory only so a browser refresh / hard refresh
        // resets it, which means the next Search click after a reload
        // runs fresh instead of popping a "cached results" dialog.
      };
      html.window.localStorage[_kDiscoverCacheKey] = jsonEncode(payload);
    } catch (e) {
      debugPrint('[discover] cache save failed: $e');
    }
  }

  void _stopSearch() {
    // Kill any in-flight HTTP requests immediately so the backend stops
    // doing work we're about to discard. Wrapped in try/catch because
    // calling .cancel() twice on the same token throws.
    try {
      _searchCancelToken?.cancel('user-stopped');
    } catch (_) {}
    _searchCancelToken = null;
    setState(() {
      _cancelled = true;
      _loading = false;
    });
  }

  Future<void> _discover({bool force = false}) async {
    // Guard 1: a search is already running.
    if (_loading) return;

    // Guard 2: debounce — prevent rapid start/stop from stacking searches.
    final now = DateTime.now();
    if (_lastSearchTrigger != null &&
        now.difference(_lastSearchTrigger!) < _kMinSearchGap) {
      return;
    }
    _lastSearchTrigger = now;

    // Guard 3: resume is mandatory — the entire matching pipeline depends
    // on it. Block early with a friendly nudge instead of letting the
    // backend return generic "no skills" results.
    if (!await ensureResumeUploaded(context, action: 'find jobs that match you')) {
      return;
    }

    // Lock immediately so a second tap during the async quota check can't
    // sneak through.
    setState(() {
      _loading = true;
      _cancelled = false;
      _error = null;
    });

    // Guard 0: check remaining quota from server BEFORE doing anything.
    // This catches the case where the user used all searches already.
    try {
      final api = context.read<ApiService>();
      final usageResp = await api.get('/api/v1/profile/usage');
      final usageData = usageResp.data is Map ? usageResp.data : {};
      final limits = usageData['limits'] as Map?;
      final usage = usageData['usage'] as Map?;
      if (limits != null && usage != null) {
        final discoverLimit = limits['discovers'] as int? ?? 999;
        final discoverUsed = usage['discovers'] as int? ?? 0;
        if (discoverUsed >= discoverLimit && usageData['tier'] == 'free') {
          if (mounted) setState(() => _loading = false);
          _showUpgradePopup(context, usageData['upgradeMessage']?.toString() ?? '');
          return;
        }
      }
    } catch (_) {
      // If usage check fails, let the search proceed — server will enforce.
    }

    // Guard 2: same query as last successful run, results still on screen,
    // and the run was recent. Skip the heavy fan-out and let the user know
    // they can force a refresh if they really want fresh data.
    final sig = _currentSearchSig();
    final hasResults = _grouped.isNotEmpty || _linkedInGroups.isNotEmpty;
    final fresh = _lastSearchAt != null &&
        DateTime.now().difference(_lastSearchAt!) < _kDedupWindow;
    if (!force && hasResults && _lastSearchSig == sig && fresh) {
      // Show a proper dialog (not a SnackBar): users complained the
      // SnackBar was easy to miss and hard to dismiss, and they wanted an
      // explicit warning before kicking off a long refresh.
      if (mounted) setState(() => _loading = false);
      final scrapedLabel = _HeroSearch._formatScrapedAt(_scrapedAt);
      final choice = await showDialog<String>(
        context: context,
        barrierDismissible: true,
        builder: (ctx) => AlertDialog(
          title: const Text('Showing cached results'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'These results are from $scrapedLabel for the same titles '
                'and locations.',
              ),
              const SizedBox(height: 12),
              const Text(
                'Refreshing will scan every selected company again and can '
                'take up to 2 minutes on a cold cache.',
                style: TextStyle(fontWeight: FontWeight.w600),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(ctx).pop('cancel'),
              child: const Text('Use cached'),
            ),
            ElevatedButton.icon(
              onPressed: () => Navigator.of(ctx).pop('refresh'),
              icon: const Icon(Icons.refresh, size: 18),
              label: const Text('Refresh anyway'),
            ),
          ],
        ),
      );
      if (choice == 'refresh') {
        // ignore: unawaited_futures
        _discover(force: true);
      }
      return;
    }

    // Fresh CancelToken for this run. Any prior in-flight requests are
    // killed first so we don't double up scrapes.
    try {
      _searchCancelToken?.cancel('superseded');
    } catch (_) {}
    _searchCancelToken = CancelToken();
    setState(() {
      _grouped = [];
      _totalFound = 0;
      _companiesScanned = 0;
      _companiesTotal = 0;
      _selectedCompany = null;
    });

    // Persist the chosen titles + locations on the profile so other surfaces
    // (resume tailor, insights) can use the user's CURRENT search intent.
    _persistSearchPreferences();

    final searchId = _newSearchId();

    try {
      final api = context.read<ApiService>();

      List<String> selectedIds = <String>[];
      try {
        final resp = await api
            .get('/api/v1/companies/selected')
            .timeout(const Duration(seconds: 10));
        // Robust parse — never cast directly. Dio may decode to
        // Map<dynamic,dynamic> or even a String depending on platform.
        dynamic raw = resp.data;
        if (raw is String) {
          try {
            raw = raw.isEmpty ? null : jsonDecode(raw);
          } catch (_) {}
        }
        List? list;
        if (raw is Map) {
          final sel = raw['selected'];
          if (sel is List) list = sel;
        } else if (raw is List) {
          list = raw;
        }
        for (final e in (list ?? const [])) {
          String id = '';
          if (e is Map) {
            final v = e['id'] ?? e['companyId'];
            if (v != null) id = v.toString();
          } else if (e != null) {
            id = e.toString();
          }
          if (id.isNotEmpty) selectedIds.add(id);
        }
      } catch (e) {
        debugPrint('[discover] /companies/selected failed: $e');
      }

      if (selectedIds.isEmpty) {
        final pp = context.read<ProfileProvider>();
        if (pp.profile == null) {
          try { await pp.loadProfile().timeout(const Duration(seconds: 8)); } catch (_) {}
        }
        final pIds = (pp.profile?['selectedCompanies'] as List?) ?? const [];
        selectedIds = pIds.map((e) => e.toString()).where((s) => s.isNotEmpty).toList();
      }

      // Dedupe to keep the "scanned of total" counter honest — if the
      // upstream lists overlap we'd otherwise both inflate `total` and
      // double-count the per-company increments.
      selectedIds = selectedIds.toSet().toList();
      if (selectedIds.length > 50) selectedIds = selectedIds.take(50).toList();

      if (selectedIds.isEmpty) {
        if (mounted) setState(() {
          _error = 'No companies selected. Visit the Companies tab and pick at least one.';
          _loading = false;
        });
        return;
      }

      if (mounted) setState(() {
        _companiesTotal = selectedIds.length;
      });

      // ── Single bulk call replaces 150 individual per-company calls ──
      // The bulk endpoint scrapes all native-scraper companies server-side
      // in parallel with its own deadline, then returns all results at once.
      // This is 5-10x faster than the old per-company approach and avoids
      // individual call timeouts.
      final cancelToken = _searchCancelToken;
      final resp = await api.post('/api/v1/jobs/discover/bulk', data: {
        'query': _expandedQueries().isNotEmpty ? _expandedQueries().first : '',
        'queries': _expandedQueries(),
        'locations': _allLocations(),
        'searchId': searchId,
        'industry': _industryId,
      }, cancelToken: cancelToken, options: Options(
        // Bulk discover scrapes all selected companies server-side with AI scoring.
        // Typical time: 120-200s. Give it 5 minutes before timing out.
        receiveTimeout: const Duration(minutes: 5),
        sendTimeout: const Duration(minutes: 1),
      ));

      dynamic raw = resp.data;
      if (raw is String) {
        try { raw = jsonDecode(raw); } catch (_) {}
      }
      final Map bulkData = (raw is Map) ? raw : const {};
      final resultsList = bulkData['results'] as List? ?? [];

      if (mounted) {
        final newGroups = <Map<String, dynamic>>[];
        int totalFound = 0;
        for (final r in resultsList) {
          if (r is! Map) continue;
          final result = Map<String, dynamic>.from(r);
          final rawCompany = result['company']?.toString();
          final companyId = result['companyId']?.toString() ?? '';
          final company = _prettyCompanyName(rawCompany, companyId);
          final jobsRaw = result['jobs'];
          final jobs = <Map<String, dynamic>>[];
          if (jobsRaw is List) {
            for (final j in jobsRaw) {
              if (j is Map) jobs.add(Map<String, dynamic>.from(j));
            }
          }
          final count = (result['count'] is int)
              ? result['count'] as int
              : jobs.length;
          if (jobs.isNotEmpty) {
            newGroups.add({'company': company, 'jobs': jobs, 'count': count});
            totalFound += count;
          }
        }
        newGroups.sort((a, b) =>
            _bestScore(b).compareTo(_bestScore(a)));
        setState(() {
          _grouped = newGroups;
          _totalFound = totalFound;
          _companiesScanned = resultsList.length;
        });
      }

      _scrapedAt = DateTime.now().toIso8601String();
      _lastSearchSig = sig;
      _lastSearchAt = DateTime.now();
      _saveCache();
    } catch (e) {
      final msg = _describeApiError(e, fallback: 'Could not load jobs. Please try again.');
      debugPrint('[discover] error: $msg | raw=$e');
      if (_is429(e) && mounted) {
        _showUpgradePopup(context, _extract429Message(e));
      } else if (mounted) {
        setState(() => _error = msg);
      }
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _discoverCompany(ApiService api, String companyId, String searchId) async {
    if (_cancelled) return;
    final cancelToken = _searchCancelToken;
    try {
      final resp = await api.post('/api/v1/jobs/discover/company', data: {
        'companyId': companyId,
        'queries': _expandedQueries(),
        'locations': _allLocations(),
        'searchId': searchId,
        'industry': _industryId,
      }, cancelToken: cancelToken);
      // Robust parse — Dio may give Map<String,dynamic>, Map<dynamic,dynamic>,
      // or even a JSON String depending on platform/config. Never cast directly.
      dynamic raw = resp.data;
      if (raw is String) {
        try { raw = jsonDecode(raw); } catch (_) {}
      }
      final Map data = (raw is Map) ? raw : const {};
      final rawCompany = data['company']?.toString();
      // Backend should always send a friendly name (Phase — _company_display_name)
      // but keep this as a safety net so we never render `comp-amazon` to users.
      final company = _prettyCompanyName(rawCompany, companyId);
      final jobsRaw = data['jobs'];
      final jobs = <Map<String, dynamic>>[];
      if (jobsRaw is List) {
        for (final j in jobsRaw) {
          if (j is Map) {
            jobs.add(Map<String, dynamic>.from(j));
          }
        }
      }
      final countVal = data['count'];
      final count = countVal is int ? countVal : (countVal is num ? countVal.toInt() : jobs.length);

      if (mounted) {
        setState(() {
          // Only surface companies that actually returned matches. Empty
          // groups (and the slug-like "comp-foo" placeholder names that
          // sometimes leaked through for them) just clutter the UI.
          if (jobs.isNotEmpty) {
            _grouped.add({'company': company, 'jobs': jobs, 'count': count});
            _grouped.sort((a, b) =>
                _bestScore(b).compareTo(_bestScore(a)));
            _totalFound += count;
          }
          _companiesScanned++;
        });
      }
    } catch (e) {
      // Cancellation is expected when the user stops the search or refreshes
      // the page — don't bump the scanned counter and don't log noise.
      if (e is DioException && CancelToken.isCancel(e)) {
        debugPrint('[discover] $companyId cancelled');
        return;
      }
      // Errors are debug-logged but not rendered as ghost cards — the user
      // explicitly asked to hide "comp-<slug>" placeholders for companies
      // that produced no matches (failures included).
      debugPrint('[discover] $companyId failed: $e');
      if (mounted) {
        setState(() {
          _companiesScanned++;
        });
      }
    }
  }

  int _bestScore(Map<String, dynamic> group) {
    final jobs = group['jobs'] as List? ?? [];
    if (jobs.isEmpty) return 0;
    return jobs.fold<int>(0, (max, j) {
      final s = (j['aiScore'] as int?) ?? (j['matchScore'] as int?) ?? 0;
      return s > max ? s : max;
    });
  }

  /// Defense-in-depth display-name resolver.
  ///
  /// The backend's `_company_display_name()` already strips the `comp-` prefix
  /// and looks the company up in the registry, but a stale build, a non-200
  /// response, or a brand-new company id that hasn't shipped to the API yet
  /// could still leak `comp-amazon` into the UI. This helper guarantees we
  /// never show that to a user. It title-cases the slug after stripping the
  /// `comp-` prefix and replacing dashes with spaces.
  String _prettyCompanyName(String? backendName, String companyId) {
    final fromBackend = backendName?.trim();
    if (fromBackend != null &&
        fromBackend.isNotEmpty &&
        !fromBackend.toLowerCase().startsWith('comp-')) {
      return fromBackend;
    }
    final slug = companyId.startsWith('comp-')
        ? companyId.substring(5)
        : companyId;
    final cleaned = slug.replaceAll('-', ' ').replaceAll('_', ' ').trim();
    if (cleaned.isEmpty) return companyId;
    return cleaned
        .split(RegExp(r'\s+'))
        .map((w) => w.isEmpty
            ? w
            : '${w[0].toUpperCase()}${w.substring(1).toLowerCase()}')
        .join(' ');
  }

  // ── Multi-value input helpers ───────────────────────────────────────────
  /// Random ID used to group all per-company calls of one user search action,
  /// so the backend daily quota is charged once per Search click rather than
  /// once per company in the fan-out.
  String _newSearchId() {
    final r = math.Random.secure();
    final ts = DateTime.now().millisecondsSinceEpoch;
    // NOTE: `1 << 32` is 0 on the web (dart2js bitwise ops are 32-bit signed),
    // which makes Random.nextInt throw "max must be in range 0 < max ≤ 2^32".
    // Use 1 << 30 (max safe positive 32-bit shift) instead.
    final rand = r.nextInt(1 << 30).toRadixString(36);
    return 's_${ts}_$rand';
  }

  /// Pending text in the query field is treated as another title chip
  /// (so the user doesn't have to press Enter before searching).
  List<String> _allTitles() {
    final list = [..._titles];
    final pending = _queryCtrl.text.trim();
    if (pending.isNotEmpty && !list.contains(pending)) list.add(pending);
    return list;
  }

  List<String> _allLocations() {
    final list = [..._locations];
    final pending = _locationCtrl.text.trim();
    if (pending.isNotEmpty && !list.contains(pending)) list.add(pending);
    return list.map(_normalizeLocation).toList();
  }

  /// Strip parenthetical aliases ("Bengaluru (Bangalore), ...") and collapse
  /// whitespace before sending to the backend / external scrapers, which
  /// expect plain "City, Region, Country" strings.
  ///
  /// Special case: "Remote (India)" / "Remote (US)" / "Remote (Europe)" must
  /// NOT lose the country hint — otherwise the backend can't tell the user
  /// wanted India-remote vs US-remote and lets every "remote" job through.
  /// We rewrite those into "Remote, <region>" so the country-detection logic
  /// still has something to match on.
  String _normalizeLocation(String s) {
    final m = RegExp(r'^Remote\s*\(([^)]+)\)\s*$', caseSensitive: false)
        .firstMatch(s.trim());
    if (m != null) {
      return 'Remote, ${m.group(1)!.trim()}';
    }
    final stripped = s.replaceAll(RegExp(r'\s*\([^)]*\)'), '');
    return stripped.replaceAll(RegExp(r'\s+'), ' ').trim();
  }

  void _addTitle(String value) {
    final v = value.trim();
    if (v.isEmpty) return;
    setState(() {
      if (!_titles.contains(v)) _titles.add(v);
      _queryCtrl.clear();
    });
  }

  void _addLocation(String value) {
    final v = value.trim();
    if (v.isEmpty) return;
    setState(() {
      if (!_locations.contains(v)) _locations.add(v);
      _locationCtrl.clear();
    });
  }

  void _removeTitle(String v) => setState(() => _titles.remove(v));
  void _removeLocation(String v) => setState(() => _locations.remove(v));

  // ── LinkedIn search (separate from per-company discover) ────────────────
  Future<void> _searchLinkedIn({bool force = false}) async {
    if (_linkedInLoading) return;

    // Debounce rapid clicks
    final now = DateTime.now();
    if (_lastSearchTrigger != null &&
        now.difference(_lastSearchTrigger!) < _kMinSearchGap) {
      return;
    }
    _lastSearchTrigger = now;

    // Resume mandatory — LinkedIn ranking uses skill embeddings.
    if (!await ensureResumeUploaded(context, action: 'rank LinkedIn jobs for you')) {
      return;
    }

    // Pre-check quota before hitting the API
    try {
      final api = context.read<ApiService>();
      final usageResp = await api.get('/api/v1/profile/usage');
      final usageData = usageResp.data is Map ? usageResp.data : {};
      final limits = usageData['limits'] as Map?;
      final usage = usageData['usage'] as Map?;
      if (limits != null && usage != null) {
        final liLimit = limits['linkedin'] as int? ?? 999;
        final liUsed = usage['linkedin'] as int? ?? 0;
        if (liUsed >= liLimit && usageData['tier'] == 'free') {
          _showUpgradePopup(context, usageData['upgradeMessage']?.toString() ?? '');
          return;
        }
      }
    } catch (_) {}

    final sig = _currentSearchSig();
    final fresh = _lastLinkedInAt != null &&
        DateTime.now().difference(_lastLinkedInAt!) < _kDedupWindow;
    if (!force && _linkedInGroup != null && _lastLinkedInSig == sig && fresh) {
      final messenger = ScaffoldMessenger.maybeOf(context);
      messenger?.hideCurrentSnackBar();
      messenger?.showSnackBar(SnackBar(
        content: Text('LinkedIn results from ${_HeroSearch._formatScrapedAt(_scrapedAt)} '
            'still match this query.'),
        action: SnackBarAction(
          label: 'Refresh anyway',
          onPressed: () => _searchLinkedIn(force: true),
        ),
        duration: const Duration(seconds: 4),
      ));
      return;
    }
    setState(() {
      _linkedInLoading = true;
      _linkedInError = null;
    });
    try {
      final api = context.read<ApiService>();
      // Use the neutral "external/search" alias instead of the legacy
      // "/jobs/linkedin/search" path. Many ad/tracker blockers (uBlock
      // EasyPrivacy, Brave Shields, NextDNS, etc.) silently drop POSTs
      // whose URL contains the word "linkedin", which made it look like
      // the request was failing locally with no backend log.
      final resp = await api.post('/api/v1/jobs/external/search', data: {
        'queries': _expandedQueries(),
        'locations': _allLocations(),
        'searchId': _newSearchId(),
        'industry': _industryId,
      }, options: Options(
        // LinkedIn search fetches ~1000 cards + AI scoring. Takes 60-120s.
        receiveTimeout: const Duration(minutes: 5),
        sendTimeout: const Duration(minutes: 1),
      ));
      // Robust parse: handle Map<dynamic,dynamic> and JSON-string responses
      dynamic raw = resp.data;
      if (raw is String) {
        try { raw = jsonDecode(raw); } catch (_) {}
      }
      final Map data = (raw is Map) ? raw : const {};
      // Parse grouped response (new API) or flat jobs (backward compat)
      final groupsRaw = data['groups'];
      final liGroups = <Map<String, dynamic>>[];
      final allLiJobs = <Map<String, dynamic>>[];
      if (groupsRaw is List && groupsRaw.isNotEmpty) {
        for (final g in groupsRaw) {
          if (g is Map) {
            final group = Map<String, dynamic>.from(g);
            final gJobs = <Map<String, dynamic>>[];
            for (final j in (group['jobs'] as List? ?? [])) {
              if (j is Map) {
                final jm = Map<String, dynamic>.from(j);
                gJobs.add(jm);
                allLiJobs.add(jm);
              }
            }
            group['jobs'] = gJobs;
            if (gJobs.isNotEmpty) liGroups.add(group);
          }
        }
      } else {
        // Flat jobs fallback
        final jobsRaw = data['jobs'];
        if (jobsRaw is List) {
          for (final j in jobsRaw) {
            if (j is Map) allLiJobs.add(Map<String, dynamic>.from(j));
          }
        }
        if (allLiJobs.isNotEmpty) {
          liGroups.add({
            'company': 'LinkedIn',
            'jobs': allLiJobs,
            'count': allLiJobs.length,
            'source': 'linkedin',
          });
        }
      }
      if (mounted) {
        setState(() {
          _linkedInGroups = liGroups;
          _linkedInGroup = {
            'company': 'LinkedIn Jobs',
            'jobs': allLiJobs,
            'count': data['count'] ?? allLiJobs.length,
            'source': 'linkedin',
          };
          _linkedInPoolSize = data['poolSize'] as int?;
          _selectedCompany = liGroups.isNotEmpty
              ? (liGroups.first['company']?.toString() ?? 'LinkedIn')
              : 'LinkedIn';
          _scrapedAt = DateTime.now().toIso8601String();
        });
        _lastLinkedInSig = sig;
        _lastLinkedInAt = DateTime.now();
        _saveCache();
      }
    } catch (e) {
      final msg = _describeApiError(e, fallback: 'Failed to fetch LinkedIn jobs.');
      debugPrint('[linkedin] error: $msg | raw=$e');
      if (_is429(e) && mounted) {
        _showUpgradePopup(context, _extract429Message(e));
      } else if (mounted) {
        setState(() => _linkedInError = msg);
      }
    }
    if (mounted) setState(() => _linkedInLoading = false);
  }

  // ── Upgrade / rate-limit helpers ──────────────────────────────────────

  bool _is429(Object e) {
    try {
      final dynamic d = e;
      return d.response?.statusCode == 429;
    } catch (_) {
      return false;
    }
  }

  String _extract429Message(Object e) {
    try {
      final dynamic d = e;
      final r = d.response;
      if (r?.data is Map) {
        final err = r.data['error'];
        if (err is Map && err['message'] is String) return err['message'];
        if (r.data['message'] is String) return r.data['message'];
      }
    } catch (_) {}
    return '';
  }

  void _showUpgradePopup(BuildContext ctx, String serverMessage) {
    final pp = context.read<ProfileProvider>();
    final country = ((pp.profile?['applicationDetails'] as Map?)?['country'] as String? ?? '')
        .trim()
        .toUpperCase();
    final isIndia = country == 'IN' || country == 'IND' || country == 'INDIA';

    showDialog(
      context: ctx,
      builder: (dialogCtx) => Dialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        child: Container(
          constraints: const BoxConstraints(maxWidth: 400),
          padding: const EdgeInsets.all(28),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(20),
            gradient: LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [
                const Color(0xFF6366f1).withValues(alpha: 0.06),
                Colors.white,
                const Color(0xFF8b5cf6).withValues(alpha: 0.04),
              ],
            ),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              // Icon
              Container(
                width: 64, height: 64,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: const LinearGradient(colors: [Color(0xFF6366f1), Color(0xFF8b5cf6)]),
                  boxShadow: [BoxShadow(color: const Color(0xFF6366f1).withValues(alpha: 0.3), blurRadius: 16, offset: const Offset(0, 4))],
                ),
                child: const Icon(Icons.rocket_launch_rounded, color: Colors.white, size: 32),
              ),
              const SizedBox(height: 20),

              // Title
              const Text(
                'Unlock Your Full Potential',
                style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800, color: Color(0xFF1e1b4b)),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 12),

              // Price highlight — country-aware
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                decoration: BoxDecoration(
                  color: const Color(0xFF6366f1).withValues(alpha: 0.08),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: const Color(0xFF6366f1).withValues(alpha: 0.2)),
                ),
                child: Column(
                  children: [
                    Text(
                      isIndia ? 'From \u20b9199/month' : 'From \$9.99/month',
                      style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w900, color: Color(0xFF6366f1)),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      isIndia
                          ? 'Save 25% with the yearly plan (\u20b91,799)'
                          : 'Save 25% with the yearly plan (\$89.99)',
                      style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: Color(0xFF4b5563)),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 16),

              // Motivational text
              const Text(
                'A small monthly investment that helps you land your dream job and change your career path forever.',
                style: TextStyle(fontSize: 13, color: Color(0xFF4b5563), height: 1.5),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 16),

              // What you get
              const Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _UpgradeFeatureRow(icon: Icons.search, text: 'Unlimited job searches'),
                  _UpgradeFeatureRow(icon: Icons.auto_fix_high, text: 'Unlimited AI autofill'),
                  _UpgradeFeatureRow(icon: Icons.description, text: 'Full AI resume tailoring'),
                  _UpgradeFeatureRow(icon: Icons.business, text: 'Unlimited company selections'),
                  _UpgradeFeatureRow(icon: Icons.public, text: 'Unlimited LinkedIn searches'),
                ],
              ),
              const SizedBox(height: 24),

              // CTA button
              SizedBox(
                width: double.infinity,
                height: 48,
                child: FilledButton(
                  onPressed: () {
                    Navigator.pop(dialogCtx);
                    context.push('/pricing');
                  },
                  style: FilledButton.styleFrom(
                    backgroundColor: const Color(0xFF6366f1),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                  ),
                  child: const Text('See Pro plans', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
                ),
              ),
              const SizedBox(height: 10),

              // Dismiss
              TextButton(
                onPressed: () => Navigator.pop(dialogCtx),
                child: const Text('Maybe later', style: TextStyle(color: Color(0xFF9ca3af), fontSize: 13)),
              ),
            ],
          ),
        ),
      ),
    );
  }

  /// Build a human-readable, actionable error message from a Dio/HTTP error.
  /// Surfaces status code, server-provided error.message, network failure
  /// type, and timeout. Logs the raw exception too so DevTools shows detail.
  String _describeApiError(Object e, {required String fallback}) {
    try {
      final dynamic d = e;
      // 1) Server returned a structured error body
      final r = d.response;
      if (r != null) {
        final code = r.statusCode;
        String? serverMsg;
        if (r.data is Map) {
          final err = r.data['error'];
          if (err is Map && err['message'] is String) {
            serverMsg = err['message'] as String;
          } else if (r.data['message'] is String) {
            serverMsg = r.data['message'] as String;
          }
        } else if (r.data is String && (r.data as String).isNotEmpty) {
          serverMsg = (r.data as String).length > 240
              ? '${(r.data as String).substring(0, 240)}…'
              : r.data as String;
        }
        if (code == 401) {
          return 'Your session expired (401). Please sign out and sign in again.';
        }
        if (code == 429) {
          return serverMsg ?? 'Daily search limit reached (429). Try again tomorrow or upgrade.';
        }
        if (code != null && code >= 500) {
          return 'Server error ($code) fetching LinkedIn jobs. ${serverMsg ?? "Please retry in a moment."}';
        }
        if (serverMsg != null) return '$serverMsg${code != null ? " ($code)" : ""}';
        if (code != null) return '$fallback (HTTP $code)';
      }
      // 2) Dio-style network/timeout errors (no response)
      final type = d.type?.toString() ?? '';
      if (type.contains('connectionTimeout')) {
        return 'Connection timed out reaching the server. Check your internet and retry.';
      }
      if (type.contains('receiveTimeout') || type.contains('sendTimeout')) {
        return 'LinkedIn search took too long and timed out. Please retry.';
      }
      if (type.contains('connectionError') || type.contains('unknown')) {
        return 'Network error — could not reach the API. Check your connection (or the API may be restarting).';
      }
      final m = d.message;
      if (m is String && m.isNotEmpty) return '$fallback ($m)';
    } catch (_) {}
    return fallback;
  }

  Future<void> _suggestImprovements() async {
    if (_suggesting) return;
    if (!await ensureResumeUploaded(context, action: 'tailor your resume')) {
      return;
    }
    final jobs = <Map<String, dynamic>>[];
    for (final g in _grouped) {
      final js = g['jobs'];
      if (js is List) {
        for (final j in js) {
          if (j is Map) jobs.add(Map<String, dynamic>.from(j));
        }
      }
    }
    final liJobs = _linkedInGroup?['jobs'];
    if (liJobs is List) {
      for (final j in liJobs) {
        if (j is Map) jobs.add(Map<String, dynamic>.from(j));
      }
    }
    // Also include jobs from LinkedIn grouped results
    for (final g in _linkedInGroups) {
      final gJobs = g['jobs'];
      if (gJobs is List) {
        for (final j in gJobs) {
          if (j is Map) jobs.add(Map<String, dynamic>.from(j));
        }
      }
    }
    jobs.sort((a, b) {
      final sa = (a['aiScore'] ?? a['score'] ?? 0) as num;
      final sb = (b['aiScore'] ?? b['score'] ?? 0) as num;
      return sb.compareTo(sa);
    });
    final topJobs = jobs.take(50).toList();

    setState(() => _suggesting = true);
    try {
      final api = context.read<ApiService>();
      final body = <String, dynamic>{
        'targetRole': _titles.isNotEmpty ? _titles.first : '',
        'targetTitles': _titles,
        'industry': _industryId,
      };
      if (topJobs.isNotEmpty) body['jobs'] = topJobs;
      final resp = await api.post('/api/v1/resume/suggest-improvements', data: body, options: Options(
        receiveTimeout: const Duration(minutes: 3),
        sendTimeout: const Duration(seconds: 30),
      ));
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
                  const Text('AI suggestions unavailable right now \u2014 try again in a moment.',
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
          msg += 'Session expired. Please sign out and sign in again.';
        } else if ((e.response?.statusCode ?? 0) >= 500) {
          msg += 'Server error — try again in a moment.';
        } else {
          msg += 'Please try again.';
        }
      } else if (e.type == DioExceptionType.connectionTimeout ||
          e.type == DioExceptionType.receiveTimeout) {
        msg += 'The AI is taking too long — please try again.';
      } else if (e.response?.statusCode == 401) {
        msg += 'Session expired. Please sign out and sign in again.';
      } else {
        msg += 'Please try again.';
      }
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(msg), backgroundColor: AppTheme.error,
            duration: const Duration(seconds: 5)),
      );
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: const Text('Could not generate suggestions. Please try again.'),
              backgroundColor: AppTheme.error, duration: const Duration(seconds: 5)),
        );
      }
    } finally {
      if (mounted) setState(() => _suggesting = false);
    }
  }

  // ── Feedback dialog ─────────────────────────────────────────────────────
  Future<void> _showFeedbackDialog() async {
    final textCtrl = TextEditingController();
    String category = 'feedback';
    final result = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) => AlertDialog(
          title: const Text('Feedback / Feature Request'),
          content: SizedBox(
            width: 500,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SegmentedButton<String>(
                  segments: const [
                    ButtonSegment(value: 'feedback', label: Text('Feedback')),
                    ButtonSegment(value: 'feature', label: Text('Feature Request')),
                    ButtonSegment(value: 'bug', label: Text('Bug Report')),
                  ],
                  selected: {category},
                  onSelectionChanged: (v) => setDialogState(() => category = v.first),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: textCtrl,
                  maxLines: 5,
                  maxLength: 2000,
                  decoration: const InputDecoration(
                    hintText: 'Tell us what you think, what you\'d like to see, or what went wrong...',
                    border: OutlineInputBorder(),
                  ),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
            FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Submit'),
            ),
          ],
        ),
      ),
    );
    if (result != true || textCtrl.text.trim().isEmpty) return;
    try {
      final api = context.read<ApiService>();
      await api.post('/api/v1/feedback', data: {
        'text': textCtrl.text.trim(),
        'category': category,
        'page': 'discover',
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Thanks for your feedback!'), backgroundColor: AppTheme.success),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Could not submit: $e'), backgroundColor: AppTheme.error),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    // Company groups (from bulk/per-company discover) — separate from LinkedIn.
    final companyGroups = <Map<String, dynamic>>[..._grouped];
    // LinkedIn employer groups from the grouped response.
    final linkedInGroups = <Map<String, dynamic>>[..._linkedInGroups];
    final hasResults = companyGroups.isNotEmpty || linkedInGroups.isNotEmpty;

    return Scaffold(
      backgroundColor: Colors.transparent,
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _showFeedbackDialog,
        backgroundColor: AppTheme.primary,
        tooltip: 'Send Feedback',
        icon: const Icon(Icons.feedback_outlined, color: Colors.white, size: 20),
        label: const Text('Feedback', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
      ),
      body: RefreshIndicator(
        // Pull-to-refresh used to fire a brand-new search, which surprised
        // users ("why is it scraping again, I just wanted to scroll up?")
        // and left a half-finished search churning on the backend if the
        // user then hit browser refresh. Now it's a no-op: it cancels any
        // in-flight scrape and tells the user to use the Search button
        // when they want fresh data. Browser F5 is handled by the
        // beforeunload listener in initState which cancels the same token.
        onRefresh: () async {
          if (_loading) {
            _stopSearch();
            final messenger = ScaffoldMessenger.maybeOf(context);
            messenger?.hideCurrentSnackBar();
            messenger?.showSnackBar(const SnackBar(
              content: Text('Search cancelled.'),
              duration: Duration(seconds: 2),
            ));
          } else {
            final messenger = ScaffoldMessenger.maybeOf(context);
            messenger?.hideCurrentSnackBar();
            messenger?.showSnackBar(const SnackBar(
              content: Text(
                  'Showing cached results. Tap Search to fetch fresh jobs.'),
              duration: Duration(seconds: 3),
            ));
          }
        },
        child: SingleChildScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _HeroSearch(
                titles: _titles,
                locations: _locations,
                queryCtrl: _queryCtrl,
                locationCtrl: _locationCtrl,
                queryFocus: _queryFocus,
                locationFocus: _locationFocus,
                onAddTitle: _addTitle,
                onAddLocation: _addLocation,
                onRemoveTitle: _removeTitle,
                onRemoveLocation: _removeLocation,
                loading: _loading,
                scanned: _companiesScanned,
                total: _companiesTotal,
                onSearch: _discover,
                onStop: _stopSearch,
                totalFound: _totalFound,
                scrapedAt: _scrapedAt,
                onSearchLinkedIn: _searchLinkedIn,
                linkedInLoading: _linkedInLoading,
                linkedInJobCount: _linkedInGroups.isEmpty
                    ? null
                    : _linkedInGroups.fold<int>(0, (s, g) => s + ((g['jobs'] as List?)?.length ?? 0)),
                industryId: _industryId,
                onIndustryChanged: _onIndustryChanged,
              ),

              // Resume tailoring promo — sized like a feature tile so it's
              // visually obvious this is a value-add service the user can use.
              Padding(
                padding: const EdgeInsets.fromLTRB(24, 16, 24, 0),
                child: _ResumeTailorPromo(
                  loading: _suggesting,
                  onTap: _suggesting ? null : _suggestImprovements,
                  hasJobs: _grouped.isNotEmpty || _linkedInGroups.isNotEmpty,
                ),
              ),

              // ── Chrome extension install banner ──
              const _ExtensionInstallBanner(),

              if (_error != null || _linkedInError != null)
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 8),
                  child: Card(
                    color: AppTheme.error.withValues(alpha: 0.08),
                    shape: RoundedRectangleBorder(
                      borderRadius: AppTheme.cardRadius,
                      side: BorderSide(color: AppTheme.error.withValues(alpha: 0.3)),
                    ),
                    child: Padding(
                      padding: const EdgeInsets.all(12),
                      child: Row(
                        children: [
                          const Icon(Icons.error_outline, color: AppTheme.error, size: 18),
                          const SizedBox(width: 8),
                          Expanded(child: Text(
                            (_linkedInError ?? _error)!.length > 200
                                ? (_linkedInError ?? _error)!.substring(0, 200) + '…'
                                : (_linkedInError ?? _error)!,
                            style: const TextStyle(color: AppTheme.error, fontSize: 12),
                          )),
                        ],
                      ),
                    ),
                  ),
                ),

              if (_loadingCached)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 48),
                  child: Center(child: CircularProgressIndicator()),
                )
              else if (!hasResults)
                _buildEmpty()
              else ...[
                // ── Company results (native scrapers) ──
                if (companyGroups.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.fromLTRB(24, 8, 24, 16),
                    child: _CompanyChipBoard(
                      groups: companyGroups,
                      selected: _selectedCompany,
                      onSelect: (name) => setState(() {
                        _selectedCompany =
                            _selectedCompany == name ? null : name;
                      }),
                    ),
                  ),

                // ── LinkedIn Jobs section ──
                if (linkedInGroups.isNotEmpty || _linkedInLoading) ...[
                  Padding(
                    padding: const EdgeInsets.fromLTRB(24, 16, 24, 8),
                    child: InkWell(
                      onTap: linkedInGroups.isNotEmpty
                          ? () => setState(() => _linkedInExpanded = !_linkedInExpanded)
                          : null,
                      borderRadius: BorderRadius.circular(8),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        child: Row(
                          children: [
                            const Icon(Icons.public, size: 20, color: Color(0xFF0A66C2)),
                            const SizedBox(width: 8),
                            Text(
                              'LinkedIn Job Posts',
                              style: TextStyle(
                                fontSize: 16,
                                fontWeight: FontWeight.w700,
                                color: const Color(0xFF0A66C2),
                              ),
                            ),
                            const SizedBox(width: 8),
                            if (_linkedInPoolSize != null)
                              Flexible(
                                child: Text(
                                  '${linkedInGroups.fold<int>(0, (s, g) => s + ((g['jobs'] as List?)?.length ?? 0))} matched from $_linkedInPoolSize scanned',
                                  style: const TextStyle(fontSize: 11, color: AppTheme.textSecondary),
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                            const Spacer(),
                            if (_linkedInLoading)
                              const SizedBox(
                                width: 16, height: 16,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              ),
                            if (linkedInGroups.isNotEmpty && !_linkedInLoading)
                              Icon(
                                _linkedInExpanded ? Icons.expand_less : Icons.expand_more,
                                color: const Color(0xFF0A66C2),
                                size: 22,
                              ),
                          ],
                        ),
                      ),
                    ),
                  ),
                  if (linkedInGroups.isNotEmpty && _linkedInExpanded)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(24, 0, 24, 32),
                      child: _CompanyChipBoard(
                        groups: linkedInGroups,
                        selected: _selectedCompany,
                        onSelect: (name) => setState(() {
                          _selectedCompany =
                              _selectedCompany == name ? null : name;
                        }),
                      ),
                    ),
                ],
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildEmpty() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.work_outline, size: 64, color: Colors.grey[300]),
            const SizedBox(height: 16),
            const Text('No jobs yet', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            const Text(
              '1. Go to Companies tab and select companies\n'
              '2. Update your Profile with skills\n'
              '3. Come back and tap "Find Matching Jobs"',
              textAlign: TextAlign.center,
              style: TextStyle(color: AppTheme.textSecondary),
            ),
          ],
        ),
      ),
    );
  }
}

class _CompanyChipBoard extends StatefulWidget {
  final List<Map<String, dynamic>> groups;
  final String? selected;
  final ValueChanged<String> onSelect;

  const _CompanyChipBoard({
    required this.groups,
    required this.selected,
    required this.onSelect,
  });

  @override
  State<_CompanyChipBoard> createState() => _CompanyChipBoardState();
}

class _CompanyChipBoardState extends State<_CompanyChipBoard> {
  static const int _pageSize = 10;
  static const int _initialVisible = 10;
  int _visible = _initialVisible;

  @override
  Widget build(BuildContext context) {
    // Sort: companies with jobs first (by best score desc), then empties.
    final sorted = [...widget.groups];
    sorted.sort((a, b) {
      final aJobs = (a['jobs'] as List?) ?? const [];
      final bJobs = (b['jobs'] as List?) ?? const [];
      if (aJobs.isEmpty != bJobs.isEmpty) return aJobs.isEmpty ? 1 : -1;
      int best(List jobs) {
        if (jobs.isEmpty) return -1;
        return jobs.map<int>((j) {
          final m = Map<String, dynamic>.from(j);
          return (m['aiScore'] as int?) ?? (m['matchScore'] as int?) ?? 0;
        }).reduce((x, y) => x > y ? x : y);
      }
      return best(bJobs).compareTo(best(aJobs));
    });

    final totalCount = sorted.length;
    final visibleCount = _visible.clamp(0, totalCount);
    var visibleList = sorted.take(visibleCount).toList();

    // Always include the selected company even if it's outside the visible
    // window, so the expanded grid stays in sync with the chip row.
    if (widget.selected != null &&
        !visibleList.any((g) => g['company']?.toString() == widget.selected)) {
      final extra = sorted.firstWhere(
        (g) => g['company']?.toString() == widget.selected,
        orElse: () => <String, dynamic>{},
      );
      if (extra.isNotEmpty) visibleList = [...visibleList, extra];
    }

    final selectedGroup = widget.selected == null
        ? null
        : sorted.firstWhere(
            (g) => g['company']?.toString() == widget.selected,
            orElse: () => <String, dynamic>{},
          );

    final remaining = totalCount - visibleCount;
    final canShowMore = remaining > 0;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Toggle chips for the visible window.
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: visibleList
              .map((g) => _CompanyChip(
                    group: g,
                    isSelected:
                        widget.selected == g['company']?.toString(),
                    onTap: () =>
                        widget.onSelect(g['company']?.toString() ?? ''),
                  ))
              .toList(),
        ),

        if (canShowMore || _visible > _initialVisible) ...[
          const SizedBox(height: 14),
          Row(
            children: [
              if (canShowMore)
                _PagerButton(
                  icon: Icons.expand_more_rounded,
                  label: 'See ${remaining < _pageSize ? remaining : _pageSize} more',
                  primary: true,
                  onTap: () => setState(() {
                    _visible = (_visible + _pageSize).clamp(0, totalCount);
                  }),
                ),
              if (canShowMore && _visible > _initialVisible)
                const SizedBox(width: 10),
              if (_visible > _initialVisible)
                _PagerButton(
                  icon: Icons.expand_less_rounded,
                  label: 'Show less',
                  primary: false,
                  onTap: () => setState(() {
                    _visible = _initialVisible;
                  }),
                ),
              const SizedBox(width: 12),
              Text(
                'Showing $visibleCount of $totalCount companies',
                style: const TextStyle(
                  fontSize: 12,
                  color: AppTheme.textSecondary,
                ),
              ),
            ],
          ),
        ],

        // Selected company's job grid below.
        if (selectedGroup != null && selectedGroup.isNotEmpty) ...[
          const SizedBox(height: 28),
          _CompanyJobGrid(group: selectedGroup),
        ],
      ],
    );
  }
}

class _PagerButton extends StatelessWidget {
  final IconData icon;
  final String label;
  final bool primary;
  final VoidCallback onTap;
  const _PagerButton({
    required this.icon,
    required this.label,
    required this.primary,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Material(
      color: primary ? AppTheme.primary : AppTheme.surface,
      borderRadius: AppTheme.pillRadius,
      child: InkWell(
        borderRadius: AppTheme.pillRadius,
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 9),
          decoration: BoxDecoration(
            borderRadius: AppTheme.pillRadius,
            border: Border.all(
              color: primary ? AppTheme.primary : AppTheme.border,
            ),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon,
                  size: 16,
                  color: primary ? Colors.white : AppTheme.textSecondary),
              const SizedBox(width: 6),
              Text(
                label,
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w700,
                  color: primary ? Colors.white : AppTheme.textPrimary,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _CompanyChip extends StatelessWidget {
  final Map<String, dynamic> group;
  final bool isSelected;
  final VoidCallback onTap;

  const _CompanyChip({
    required this.group,
    required this.isSelected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final company = group['company']?.toString() ?? '';
    final jobs = (group['jobs'] as List?) ?? const [];
    final hasError = group['error'] == true;
    final hasJobs = jobs.isNotEmpty;

    // Best matching score across this company's jobs (AI-rerank wins).
    int topScore = 0;
    for (final j in jobs) {
      final m = Map<String, dynamic>.from(j as Map);
      final s = (m['aiScore'] as int?) ?? (m['matchScore'] as int?) ?? 0;
      if (s > topScore) topScore = s;
    }
    final scoreColor = topScore >= 70
        ? AppTheme.success
        : topScore >= 40
            ? AppTheme.warning
            : AppTheme.error;

    final bg = isSelected
        ? AppTheme.primary
        : (hasJobs ? AppTheme.surface : AppTheme.surfaceAlt);
    final fg = isSelected
        ? Colors.white
        : (hasJobs ? AppTheme.textPrimary : AppTheme.textSecondary);
    final borderColor = isSelected
        ? AppTheme.primary
        : (hasError ? AppTheme.error.withValues(alpha: 0.4) : AppTheme.border);

    return Material(
      color: bg,
      borderRadius: AppTheme.pillRadius,
      elevation: isSelected ? 2 : 0,
      shadowColor: AppTheme.primary.withValues(alpha: 0.25),
      child: InkWell(
        borderRadius: AppTheme.pillRadius,
        onTap: hasJobs ? onTap : null,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          decoration: BoxDecoration(
            borderRadius: AppTheme.pillRadius,
            border: Border.all(color: borderColor),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              CircleAvatar(
                radius: 12,
                backgroundColor: isSelected
                    ? Colors.white.withValues(alpha: 0.25)
                    : (hasJobs ? AppTheme.primarySoft : Colors.grey.shade200),
                child: Text(
                  company.isNotEmpty ? company[0].toUpperCase() : '?',
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w800,
                    color: isSelected
                        ? Colors.white
                        : (hasJobs ? AppTheme.primary : Colors.grey.shade600),
                  ),
                ),
              ),
              const SizedBox(width: 10),
              Text(
                company,
                style: TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w700,
                  color: fg,
                ),
              ),
              const SizedBox(width: 8),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: isSelected
                      ? Colors.white.withValues(alpha: 0.22)
                      : (hasJobs
                          ? AppTheme.primarySoft
                          : Colors.grey.shade200),
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Text(
                  hasError && jobs.isEmpty
                      ? '!'
                      : (hasJobs ? '${jobs.length}' : '0'),
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w800,
                    color: isSelected
                        ? Colors.white
                        : (hasJobs ? AppTheme.primary : Colors.grey.shade600),
                  ),
                ),
              ),
              if (hasJobs) ...[
                const SizedBox(width: 6),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                  decoration: BoxDecoration(
                    color: isSelected
                        ? Colors.white.withValues(alpha: 0.22)
                        : scoreColor.withValues(alpha: 0.15),
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: Text(
                    '$topScore%',
                    style: TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w800,
                      color: isSelected ? Colors.white : scoreColor,
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _CompanyJobGrid extends StatelessWidget {
  final Map<String, dynamic> group;
  const _CompanyJobGrid({required this.group});

  @override
  Widget build(BuildContext context) {
    final company = group['company']?.toString() ?? '';
    final jobs = (group['jobs'] as List?)
            ?.map<Map<String, dynamic>>((j) => Map<String, dynamic>.from(j))
            .toList() ??
        [];
    jobs.sort((a, b) {
      final aScore = (a['aiScore'] as int?) ?? (a['matchScore'] as int?) ?? 0;
      final bScore = (b['aiScore'] as int?) ?? (b['matchScore'] as int?) ?? 0;
      return bScore.compareTo(aScore);
    });

    final width = MediaQuery.of(context).size.width;
    // Responsive tile width to mimic the JPMC 3-up grid.
    int columns;
    if (width >= 1200) {
      columns = 3;
    } else if (width >= 820) {
      columns = 2;
    } else {
      columns = 1;
    }
    final spacing = 16.0;
    final available = (width - 48 - spacing * (columns - 1)).clamp(280.0, 1200.0);
    final tileWidth = available / columns;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Container(
              width: 4,
              height: 22,
              decoration: BoxDecoration(
                gradient: AppTheme.brandGradient,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            const SizedBox(width: 10),
            Text(
              '$company \u00b7 ${jobs.length} open ${jobs.length == 1 ? "role" : "roles"}',
              style: const TextStyle(
                fontSize: 18,
                fontWeight: FontWeight.w800,
                color: AppTheme.textPrimary,
                letterSpacing: -0.2,
              ),
            ),
          ],
        ),
        const SizedBox(height: 16),
        if (jobs.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 24),
            child: Text(
              'No matching jobs for this company.',
              style: TextStyle(color: AppTheme.textSecondary),
            ),
          )
        else
          Wrap(
            spacing: spacing,
            runSpacing: spacing,
            children: jobs
                .map((job) => SizedBox(
                      width: tileWidth,
                      child: RepaintBoundary(child: _JobTile(
                        job: job,
                        // Show the employer line whenever the tile lives in
                        // a group whose label is NOT the actual employer:
                        // the LinkedIn group rolls many companies under one
                        // banner, so the per-card company is meaningful.
                        showEmployer:
                            (group['source']?.toString() ?? '') == 'linkedin' ||
                                (job['company']?.toString().toLowerCase() ?? '') !=
                                    company.toLowerCase(),
                      )),
                    ))
                .toList(),
          ),
      ],
    );
  }
}

/// Compact JPMC-style tile rendered in the company job grid.
class _JobTile extends StatelessWidget {
  final Map<String, dynamic> job;
  /// When true, render a small employer subtitle under the title. Set true
  /// for tiles inside the LinkedIn group (where the parent header just says
  /// "LinkedIn" and the actual employer would otherwise be invisible) and
  /// any other context where the job's company differs from its group.
  final bool showEmployer;
  const _JobTile({required this.job, this.showEmployer = false});

  @override
  Widget build(BuildContext context) {
    final title = job['title']?.toString() ?? '';
    final company = job['company']?.toString() ?? '';
    final location = job['location']?.toString() ?? '';
    final matchScore = job['matchScore'] as int? ?? 0;
    final aiScore = job['aiScore'] as int? ?? 0;
    final rawAiReason = job['aiReason']?.toString() ?? '';
    // Treat any internal sentinel string (e.g. 'rerank-missed', 'rerank-failed')
    // as empty so we never expose backend pipeline state to the user.
    final aiReason = (rawAiReason.toLowerCase().startsWith('rerank') ||
                       rawAiReason.toLowerCase().contains('failed'))
        ? ''
        : rawAiReason;
    final matchReason = job['matchReason']?.toString() ?? '';
    final reasonText = aiReason.isNotEmpty ? aiReason : matchReason;
    final url = job['url']?.toString() ?? '';
    final skills = (job['skills'] as List?)?.cast<String>() ?? const [];
    // postedAt is now a real source-supplied date when available, else null.
    // firstSeenAt is always set by the scraper to "when did we last fetch
    // this card from the source". Older cached jobs (pre v22 backend) may
    // have only postedAt — in that case, postedAt was actually scrape time,
    // so treat it as firstSeenAt for display purposes.
    final rawPostedAt = job['postedAt']?.toString();
    final rawFirstSeenAt = job['firstSeenAt']?.toString();
    final hasFirstSeen = rawFirstSeenAt != null && rawFirstSeenAt.isNotEmpty;
    final postedAgo = (hasFirstSeen && rawPostedAt != null && rawPostedAt.isNotEmpty)
        ? _formatRecency(rawPostedAt)
        : '';
    final lastCheckedAgo = _formatRecency(
        hasFirstSeen ? rawFirstSeenAt : rawPostedAt);
    final displayScore = aiScore > 0 ? aiScore : matchScore;
    final scoreColor = displayScore >= 70
        ? AppTheme.success
        : displayScore >= 40
            ? AppTheme.warning
            : AppTheme.error;

    return Container(
      decoration: BoxDecoration(
        color: AppTheme.surface,
        borderRadius: AppTheme.cardRadius,
        border: Border.all(color: AppTheme.border),
        boxShadow: AppTheme.softShadow,
      ),
      child: InkWell(
        borderRadius: AppTheme.cardRadius,
        onTap: url.isNotEmpty
            ? () => _showJobApplySheet(context, title, company, url)
            : null,
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Text(
                      title,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w800,
                        color: AppTheme.textPrimary,
                        height: 1.25,
                      ),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 10, vertical: 4),
                    decoration: BoxDecoration(
                      color: scoreColor.withValues(alpha: 0.14),
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Text(
                      '$displayScore%',
                      style: TextStyle(
                        color: scoreColor,
                        fontWeight: FontWeight.w800,
                        fontSize: 12,
                      ),
                    ),
                  ),
                ],
              ),
              if (showEmployer && company.isNotEmpty) ...[
                const SizedBox(height: 6),
                Row(
                  children: [
                    const Icon(Icons.business_outlined,
                        size: 14, color: AppTheme.textSecondary),
                    const SizedBox(width: 4),
                    Expanded(
                      child: Text(
                        company,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 12,
                          fontWeight: FontWeight.w700,
                          color: AppTheme.textPrimary,
                        ),
                      ),
                    ),
                  ],
                ),
              ],
              const SizedBox(height: 10),
              if (location.isNotEmpty)
                Row(
                  children: [
                    const Icon(Icons.place_outlined,
                        size: 14, color: AppTheme.textSecondary),
                    const SizedBox(width: 4),
                    Expanded(
                      child: Text(
                        location,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 12,
                          color: AppTheme.textSecondary,
                        ),
                      ),
                    ),
                  ],
                ),
              if (postedAgo.isNotEmpty || lastCheckedAgo.isNotEmpty) ...[
                const SizedBox(height: 6),
                Row(
                  children: [
                    const Icon(Icons.schedule,
                        size: 13, color: AppTheme.textSecondary),
                    const SizedBox(width: 4),
                    Expanded(
                      child: Wrap(
                        spacing: 6,
                        runSpacing: 4,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: [
                          if (postedAgo.isNotEmpty)
                            Container(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 6, vertical: 2),
                              decoration: BoxDecoration(
                                color: AppTheme.primarySoft.withValues(alpha: 0.6),
                                borderRadius: BorderRadius.circular(4),
                              ),
                              child: Text(
                                'Posted $postedAgo',
                                style: const TextStyle(
                                  fontSize: 11,
                                  fontWeight: FontWeight.w700,
                                  color: AppTheme.primary,
                                ),
                              ),
                            ),
                          if (lastCheckedAgo.isNotEmpty)
                            Tooltip(
                              message:
                                  'When we last fetched this listing from the '
                                  'company\'s career site. Refresh the search '
                                  'to update.',
                              child: Text(
                                'Last checked $lastCheckedAgo',
                                style: const TextStyle(
                                  fontSize: 11,
                                  color: AppTheme.textSecondary,
                                ),
                              ),
                            ),
                        ],
                      ),
                    ),
                  ],
                ),
              ],
              if (skills.isNotEmpty) ...[
                const SizedBox(height: 10),
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: skills.take(4).map((s) {
                    return Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 8, vertical: 3),
                      decoration: BoxDecoration(
                        color: AppTheme.primarySoft,
                        borderRadius: BorderRadius.circular(6),
                      ),
                      child: Text(
                        s,
                        style: const TextStyle(
                          fontSize: 10,
                          fontWeight: FontWeight.w700,
                          color: AppTheme.primary,
                        ),
                      ),
                    );
                  }).toList(),
                ),
              ],
              if (reasonText.isNotEmpty) ...[
                const SizedBox(height: 12),
                Text(
                  reasonText,
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 12,
                    color: AppTheme.textSecondary,
                    height: 1.4,
                  ),
                ),
              ],
              const SizedBox(height: 12),
              Row(
                children: [
                  if (url.isNotEmpty) ...[
                    const Icon(Icons.open_in_new,
                        size: 13, color: AppTheme.primary),
                    const SizedBox(width: 4),
                    const Text(
                      'View role',
                      style: TextStyle(
                        fontSize: 12,
                        color: AppTheme.primary,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ],
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _CompanyGroup extends StatelessWidget {
  final Map<String, dynamic> group;
  const _CompanyGroup({required this.group});

  @override
  Widget build(BuildContext context) {
    final company = group['company']?.toString() ?? '';
    final jobs = (group['jobs'] as List?)
        ?.map<Map<String, dynamic>>((j) => Map<String, dynamic>.from(j))
        .toList() ?? [];

    // Sort jobs by best score (aiScore > matchScore)
    jobs.sort((a, b) {
      final aScore = (a['aiScore'] as int?) ?? (a['matchScore'] as int?) ?? 0;
      final bScore = (b['aiScore'] as int?) ?? (b['matchScore'] as int?) ?? 0;
      return bScore.compareTo(aScore);
    });

    final topScore = jobs.isNotEmpty
        ? ((jobs.first['aiScore'] as int?) ?? (jobs.first['matchScore'] as int?) ?? 0)
        : 0;
    final hasError = group['error'] == true;
    final errorMessage = (group['errorMessage'] as String?) ?? '';
    final isQuotaError = errorMessage.toLowerCase().contains('limit') || errorMessage.toLowerCase().contains('quota') || errorMessage.toLowerCase().contains('upgrade');

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      clipBehavior: Clip.antiAlias,
      child: ExpansionTile(
        initiallyExpanded: false,
        leading: CircleAvatar(
          radius: 18,
          backgroundColor: jobs.isEmpty ? Colors.grey[300]! : AppTheme.primary,
          child: Text(company.isNotEmpty ? company[0].toUpperCase() : '?',
              style: TextStyle(color: jobs.isEmpty ? Colors.grey[600] : Colors.white, fontWeight: FontWeight.bold, fontSize: 16)),
        ),
        title: Text(company, style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16,
            color: jobs.isEmpty ? AppTheme.textSecondary : AppTheme.textPrimary)),
        subtitle: Row(
          children: [
            if (jobs.isEmpty)
              Text(
                hasError
                  ? (isQuotaError ? 'Daily limit reached' : 'Failed to scan')
                  : 'No matching jobs in your location',
                style: TextStyle(fontSize: 12, color: hasError ? AppTheme.error : AppTheme.textSecondary),
              )
            else ...[
              Text('${jobs.length} jobs', style: const TextStyle(fontSize: 12, color: AppTheme.textSecondary)),
              const SizedBox(width: 8),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                decoration: BoxDecoration(
                  color: (topScore >= 70 ? AppTheme.success : topScore >= 40 ? AppTheme.warning : AppTheme.error)
                      .withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text('Best: $topScore%',
                    style: TextStyle(
                      fontSize: 10,
                      fontWeight: FontWeight.w600,
                      color: topScore >= 70 ? AppTheme.success : topScore >= 40 ? AppTheme.warning : AppTheme.error,
                    )),
              ),
            ],
          ],
        ),
        children: jobs.isEmpty
            ? [Padding(
                padding: const EdgeInsets.all(16),
                child: Text(
                  hasError
                    ? (errorMessage.isNotEmpty ? errorMessage : 'Error scanning this company. Try again.')
                    : 'No jobs found matching your location and experience preferences.',
                  style: const TextStyle(color: AppTheme.textSecondary, fontSize: 13),
                ),
              )]
            : jobs.map((job) => RepaintBoundary(child: _JobCard(job: job))).toList(),
      ),
    );
  }
}

/// Hero search block — large brand-styled headline + a wide pill-shaped
/// search box with two fields (Find Jobs / Near Location) and a magnifier
/// button on the right. Inspired by enterprise career-portal UIs.
class _HeroSearch extends StatelessWidget {
  final List<String> titles;
  final List<String> locations;
  final TextEditingController queryCtrl;
  final TextEditingController locationCtrl;
  final FocusNode queryFocus;
  final FocusNode locationFocus;
  final ValueChanged<String> onAddTitle;
  final ValueChanged<String> onAddLocation;
  final ValueChanged<String> onRemoveTitle;
  final ValueChanged<String> onRemoveLocation;
  final bool loading;
  final int scanned;
  final int total;
  final int totalFound;
  final String scrapedAt;
  final VoidCallback onSearch;
  final VoidCallback onStop;
  final VoidCallback onSearchLinkedIn;
  final bool linkedInLoading;
  final int? linkedInJobCount;
  final String industryId;
  final ValueChanged<String> onIndustryChanged;

  const _HeroSearch({
    required this.titles,
    required this.locations,
    required this.queryCtrl,
    required this.locationCtrl,
    required this.queryFocus,
    required this.locationFocus,
    required this.onAddTitle,
    required this.onAddLocation,
    required this.onRemoveTitle,
    required this.onRemoveLocation,
    required this.loading,
    required this.scanned,
    required this.total,
    required this.totalFound,
    required this.scrapedAt,
    required this.onSearch,
    required this.onStop,
    required this.onSearchLinkedIn,
    required this.linkedInLoading,
    required this.linkedInJobCount,
    required this.industryId,
    required this.onIndustryChanged,
  });

  @override
  Widget build(BuildContext context) {
    final width = MediaQuery.of(context).size.width;
    final isCompact = width < 720;

    return Container(
      width: double.infinity,
      padding: EdgeInsets.fromLTRB(24, isCompact ? 18 : 28, 24, isCompact ? 16 : 22),
      decoration: BoxDecoration(
        gradient: RadialGradient(
          center: const Alignment(0, -0.4),
          radius: 1.4,
          colors: [
            AppTheme.primarySoft.withValues(alpha: 0.9),
            AppTheme.background,
          ],
        ),
      ),
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 920),
          child: Column(
            children: [
              // Eyebrow tag — small, uppercase, brand pill.
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(
                  color: AppTheme.primarySoft,
                  borderRadius: BorderRadius.circular(20),
                  border: Border.all(
                      color: AppTheme.primary.withValues(alpha: 0.18)),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.auto_awesome_rounded,
                        size: 12, color: AppTheme.primary),
                    const SizedBox(width: 5),
                    Text(
                      'AI-RANKED  \u00B7  TOP COMPANIES  \u00B7  LINKEDIN',
                      style: TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.w800,
                        letterSpacing: 1.1,
                        color: AppTheme.primary,
                      ),
                    ),
                  ],
                ),
              ),
              SizedBox(height: isCompact ? 10 : 14),

              // Headline — short, single line on desktop, gradient accent.
              ShaderMask(
                shaderCallback: (r) =>
                    AppTheme.brandGradient.createShader(r),
                child: Text(
                  'We find and apply for you.',
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    fontSize: isCompact ? 26 : 36,
                    fontWeight: FontWeight.w800,
                    height: 1.05,
                    letterSpacing: -1.0,
                    color: Colors.white,
                  ),
                ),
              ),

              SizedBox(height: isCompact ? 6 : 8),

              // One-liner sub-copy.
              Text(
                'Skip the hunt — focus on the interview.',
                textAlign: TextAlign.center,
                style: TextStyle(
                  fontSize: isCompact ? 13 : 14,
                  color: AppTheme.textSecondary,
                ),
              ),

              SizedBox(height: isCompact ? 14 : 18),

              // Industry picker — PRIMARILY a role-autocomplete hint.
              // It also tunes the LLM rerank prompt, but it does NOT hard
              // filter the company catalog (we don't have employers in
              // every industry yet). The label below makes that explicit.
              Padding(
                padding: const EdgeInsets.only(left: 4, bottom: 6),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: const [
                    Text(
                      'SUGGEST ROLES FOR…',
                      style: TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.w800,
                        letterSpacing: 1.1,
                        color: AppTheme.textSecondary,
                      ),
                    ),
                    SizedBox(width: 6),
                    Tooltip(
                      message:
                          'Tunes the role suggestions in the search box and '
                          'gives the matcher a hint about your field. It '
                          'does NOT strictly filter the company catalog — '
                          'pick "Government / Non-profit" and you may '
                          'still see roles from tech employers, because the '
                          'catalog is mostly tech / finance companies today.',
                      child: Icon(Icons.info_outline,
                          size: 13, color: AppTheme.textSecondary),
                    ),
                  ],
                ),
              ),
              _IndustryPicker(
                selectedId: industryId,
                onChanged: onIndustryChanged,
              ),

              SizedBox(height: isCompact ? 10 : 12),

              // Search box — multi-chip inputs.
              Container(
                decoration: BoxDecoration(
                  color: AppTheme.surface,
                  borderRadius: BorderRadius.circular(14),
                  border: Border.all(color: AppTheme.border),
                  boxShadow: AppTheme.softShadow,
                ),
                child: isCompact
                    ? Column(
                        children: [
                          _MultiChipField(
                            label: 'JOB TITLES',
                            hint: titles.isEmpty
                                ? 'Pick a role or type your own'
                                : 'Add another title',
                            values: titles,
                            controller: queryCtrl,
                            focusNode: queryFocus,
                            onAdd: onAddTitle,
                            onRemove: onRemoveTitle,
                            onSubmitEmpty: onSearch,
                            suggestions: kRolesByIndustry[industryId] ?? const [],
                          ),
                          const Divider(height: 1, color: AppTheme.border),
                          _MultiChipField(
                            label: 'LOCATIONS',
                            hint: locations.isEmpty
                                ? 'Pick a city or type to search'
                                : 'Add another location',
                            values: locations,
                            controller: locationCtrl,
                            focusNode: locationFocus,
                            onAdd: onAddLocation,
                            onRemove: onRemoveLocation,
                            onSubmitEmpty: onSearch,
                            suggestions: kAllLocationOptions,
                          ),
                        ],
                      )
                    : IntrinsicHeight(
                        child: Row(
                          children: [
                            Expanded(
                              flex: 5,
                              child: _MultiChipField(
                                label: 'JOB TITLES',
                                hint: titles.isEmpty
                                    ? 'Pick a role or type your own'
                                    : 'Add another title',
                                values: titles,
                                controller: queryCtrl,
                                focusNode: queryFocus,
                                onAdd: onAddTitle,
                                onRemove: onRemoveTitle,
                                onSubmitEmpty: onSearch,
                                suggestions:
                                    kRolesByIndustry[industryId] ?? const [],
                              ),
                            ),
                            const VerticalDivider(width: 1, color: AppTheme.border),
                            Expanded(
                              flex: 4,
                              child: _MultiChipField(
                                label: 'LOCATIONS',
                                hint: locations.isEmpty
                                    ? 'Pick a city or type to search'
                                    : 'Add another location',
                                values: locations,
                                controller: locationCtrl,
                                focusNode: locationFocus,
                                onAdd: onAddLocation,
                                onRemove: onRemoveLocation,
                                onSubmitEmpty: onSearch,
                                suggestions: kAllLocationOptions,
                              ),
                            ),
                          ],
                        ),
                      ),
              ),

              const SizedBox(height: 14),

              // Two decoupled search buttons + status row
              Row(
                children: [
                  Expanded(
                    child: SizedBox(
                      height: 44,
                      child: FilledButton.icon(
                        onPressed: loading ? null : onSearch,
                        icon: Icon(
                          loading ? Icons.hourglass_top_rounded : Icons.business_rounded,
                          size: 18,
                        ),
                        label: Text(
                          loading ? 'Searching…' : 'Search Career Sites',
                          style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13),
                        ),
                        style: FilledButton.styleFrom(
                          backgroundColor: AppTheme.primary,
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: SizedBox(
                      height: 44,
                      child: FilledButton.icon(
                        onPressed: linkedInLoading ? null : onSearchLinkedIn,
                        icon: linkedInLoading
                            ? const SizedBox(
                                width: 16, height: 16,
                                child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                              )
                            : Container(
                                width: 20, height: 20,
                                alignment: Alignment.center,
                                decoration: BoxDecoration(
                                  color: Colors.white,
                                  borderRadius: BorderRadius.circular(3),
                                ),
                                child: const Text('in',
                                  style: TextStyle(color: Color(0xFF0A66C2), fontWeight: FontWeight.w900, fontSize: 12, height: 1.0)),
                              ),
                        label: Text(
                          linkedInLoading
                              ? 'Searching…'
                              : (linkedInJobCount != null ? 'LinkedIn ($linkedInJobCount)' : 'Search LinkedIn'),
                          style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13),
                        ),
                        style: FilledButton.styleFrom(
                          backgroundColor: const Color(0xFF0A66C2),
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                        ),
                      ),
                    ),
                  ),
                ],
              ),

              const SizedBox(height: 8),
              _statusRow(),
            ],
          ),
        ),
      ),
    );
  }

  Widget _statusRow() {
    if (loading) {
      return _LoadingHero(
        scanned: scanned,
        total: total,
        onStop: onStop,
      );
    }
    if (totalFound > 0) {
      return Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
            decoration: BoxDecoration(
              color: AppTheme.primarySoft,
              borderRadius: AppTheme.pillRadius,
            ),
            child: Text(
              '$totalFound matching jobs',
              style: const TextStyle(
                fontSize: 12, fontWeight: FontWeight.w700,
                color: AppTheme.primary, letterSpacing: 0.3,
              ),
            ),
          ),
          if (scrapedAt.isNotEmpty) ...[
            const SizedBox(width: 10),
            Text(
              _formatScrapedAt(scrapedAt),
              style: const TextStyle(fontSize: 11, color: AppTheme.textSecondary),
            ),
          ],
        ],
      );
    }
    return const SizedBox.shrink();
  }

  /// Format an ISO timestamp into a human label like:
  ///   "updated 14:32"           (today)
  ///   "updated yesterday 14:32" (yesterday)
  ///   "updated May 6, 14:32"    (older)
  static String _formatScrapedAt(String iso) {
    DateTime? dt;
    try {
      dt = DateTime.parse(iso).toLocal();
    } catch (_) {
      return 'updated $iso';
    }
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final that = DateTime(dt.year, dt.month, dt.day);
    final dayDiff = today.difference(that).inDays;
    final hh = dt.hour.toString().padLeft(2, '0');
    final mm = dt.minute.toString().padLeft(2, '0');
    if (dayDiff == 0) return 'updated $hh:$mm';
    if (dayDiff == 1) return 'updated yesterday $hh:$mm';
    const months = [
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
    ];
    return 'updated ${months[dt.month - 1]} ${dt.day}, $hh:$mm';
  }

  Widget _searchButton({double? width, bool isCompact = false}) {
    final disabled = loading;
    return SizedBox(
      width: width,
      height: isCompact ? 56 : null,
      child: Material(
        color: disabled ? AppTheme.surfaceAlt : AppTheme.primary,
        borderRadius: isCompact
            ? const BorderRadius.only(
                bottomLeft: Radius.circular(14),
                bottomRight: Radius.circular(14),
              )
            : const BorderRadius.only(
                topRight: Radius.circular(14),
                bottomRight: Radius.circular(14),
              ),
        child: InkWell(
          onTap: disabled ? null : onSearch,
          borderRadius: isCompact
              ? const BorderRadius.only(
                  bottomLeft: Radius.circular(14),
                  bottomRight: Radius.circular(14),
                )
              : const BorderRadius.only(
                  topRight: Radius.circular(14),
                  bottomRight: Radius.circular(14),
                ),
          child: Container(
            padding: EdgeInsets.symmetric(horizontal: isCompact ? 0 : 28),
            alignment: Alignment.center,
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  Icons.search_rounded,
                  color: disabled ? AppTheme.textSecondary : Colors.white,
                  size: 24,
                ),
                if (isCompact) ...[
                  const SizedBox(width: 8),
                  Text(
                    disabled ? 'Searching\u2026' : 'Search Career Sites',
                    style: TextStyle(
                      color: disabled ? AppTheme.textSecondary : Colors.white,
                      fontWeight: FontWeight.w700,
                      fontSize: 15,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _linkedInSearchButton({double? width}) {
    const liBlue = Color(0xFF0A66C2);
    final disabled = linkedInLoading;
    return SizedBox(
      width: width,
      height: 50,
      child: Material(
        color: disabled ? AppTheme.surfaceAlt : liBlue,
        borderRadius: BorderRadius.circular(14),
        child: InkWell(
          onTap: disabled ? null : onSearchLinkedIn,
          borderRadius: BorderRadius.circular(14),
          child: Container(
            alignment: Alignment.center,
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (disabled)
                  const SizedBox(
                    width: 16, height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                  )
                else
                  Container(
                    width: 22, height: 22,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: Colors.white,
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: const Text('in',
                      style: TextStyle(color: liBlue, fontWeight: FontWeight.w900, fontSize: 14, height: 1.0)),
                  ),
                const SizedBox(width: 10),
                Text(
                  disabled
                      ? 'Searching LinkedIn\u2026'
                      : (linkedInJobCount != null
                          ? 'Search LinkedIn ($linkedInJobCount found)'
                          : 'Search LinkedIn Jobs'),
                  style: TextStyle(
                    color: disabled ? AppTheme.textSecondary : Colors.white,
                    fontWeight: FontWeight.w700,
                    fontSize: 15,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Compact tag-input field: shows entered values as removable chips,
/// with a TextField at the end for adding more (Enter or comma commits).
/// When `suggestions` is non-empty, the input becomes a fuzzy-match
/// autocomplete to eliminate typos in city / role names.
class _MultiChipField extends StatefulWidget {
  final String label;
  final String hint;
  final List<String> values;
  final TextEditingController controller;
  final FocusNode focusNode;
  final ValueChanged<String> onAdd;
  final ValueChanged<String> onRemove;
  final VoidCallback onSubmitEmpty;
  final List<String> suggestions;

  const _MultiChipField({
    required this.label,
    required this.hint,
    required this.values,
    required this.controller,
    required this.focusNode,
    required this.onAdd,
    required this.onRemove,
    required this.onSubmitEmpty,
    this.suggestions = const [],
  });

  @override
  State<_MultiChipField> createState() => _MultiChipFieldState();
}

class _MultiChipFieldState extends State<_MultiChipField> {
  @override
  void initState() {
    super.initState();
    widget.focusNode.addListener(_handleFocusChange);
  }

  @override
  void dispose() {
    widget.focusNode.removeListener(_handleFocusChange);
    super.dispose();
  }

  /// When the field loses focus and there is uncommitted text, treat the
  /// focus loss as if the user had pressed Enter — adding the typed value
  /// as a chip. This avoids dropping work when users tab/click away.
  void _handleFocusChange() {
    if (widget.focusNode.hasFocus) return;
    final text = widget.controller.text.trim();
    if (text.isEmpty) return;
    widget.onAdd(text);
  }

  // Convenience getters so the original build code can keep using the same
  // identifiers without referencing `widget.` everywhere.
  String get label => widget.label;
  String get hint => widget.hint;
  List<String> get values => widget.values;
  TextEditingController get controller => widget.controller;
  FocusNode get focusNode => widget.focusNode;
  ValueChanged<String> get onAdd => widget.onAdd;
  ValueChanged<String> get onRemove => widget.onRemove;
  VoidCallback get onSubmitEmpty => widget.onSubmitEmpty;
  List<String> get suggestions => widget.suggestions;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: const TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w700,
              color: AppTheme.textSecondary,
              letterSpacing: 1.0,
            ),
          ),
          const SizedBox(height: 6),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              ...values.map((v) => Container(
                    padding: const EdgeInsets.fromLTRB(10, 4, 4, 4),
                    decoration: BoxDecoration(
                      color: AppTheme.primarySoft,
                      borderRadius: BorderRadius.circular(20),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          v,
                          style: const TextStyle(
                            fontSize: 13,
                            fontWeight: FontWeight.w700,
                            color: AppTheme.primary,
                          ),
                        ),
                        const SizedBox(width: 2),
                        InkWell(
                          borderRadius: BorderRadius.circular(20),
                          onTap: () => onRemove(v),
                          child: const Padding(
                            padding: EdgeInsets.all(3),
                            child: Icon(Icons.close_rounded,
                                size: 14, color: AppTheme.primary),
                          ),
                        ),
                      ],
                    ),
                  )),
              IntrinsicWidth(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(minWidth: 180),
                  child: _buildInput(),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildInput() {
    if (suggestions.isEmpty) {
      // Plain TextField fallback when no suggestion list is provided.
      return TextField(
        controller: controller,
        focusNode: focusNode,
        textInputAction: TextInputAction.next,
        onChanged: (val) {
          if (val.contains(',')) {
            for (final part in val.split(',')) {
              onAdd(part);
            }
          }
        },
        onSubmitted: (val) {
          if (val.trim().isEmpty) {
            onSubmitEmpty();
          } else {
            onAdd(val);
          }
        },
        decoration: _decoration(),
        style: _textStyle(),
      );
    }
    return RawAutocomplete<String>(
      textEditingController: controller,
      focusNode: focusNode,
      optionsBuilder: (TextEditingValue tev) {
        final q = tev.text.trim().toLowerCase();
        if (q.isEmpty) {
          return suggestions
              .where((s) => !values
                  .map((v) => v.toLowerCase())
                  .contains(s.toLowerCase()))
              .take(8);
        }
        // Substring match anywhere — prioritises starts-with first.
        final starts = <String>[];
        final contains = <String>[];
        for (final s in suggestions) {
          if (values
              .map((v) => v.toLowerCase())
              .contains(s.toLowerCase())) continue;
          final lc = s.toLowerCase();
          if (lc.startsWith(q)) {
            starts.add(s);
          } else if (lc.contains(q)) {
            contains.add(s);
          }
        }
        return [...starts, ...contains].take(10);
      },
      onSelected: (val) {
        onAdd(val);
      },
      fieldViewBuilder: (ctx, ctl, fn, onSubmit) {
        return TextField(
          controller: ctl,
          focusNode: fn,
          textInputAction: TextInputAction.next,
          onChanged: (val) {
            if (val.contains(',')) {
              for (final part in val.split(',')) {
                onAdd(part);
              }
            }
          },
          onSubmitted: (val) {
            if (val.trim().isEmpty) {
              onSubmitEmpty();
            } else {
              onAdd(val);
            }
          },
          decoration: _decoration(),
          style: _textStyle(),
        );
      },
      optionsViewBuilder: (ctx, onSelected, options) {
        return Align(
          alignment: Alignment.topLeft,
          child: Material(
            elevation: 6,
            borderRadius: BorderRadius.circular(10),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxHeight: 280, maxWidth: 360),
              child: ListView.separated(
                padding: EdgeInsets.zero,
                shrinkWrap: true,
                itemCount: options.length,
                separatorBuilder: (_, __) =>
                    const Divider(height: 1, color: AppTheme.border),
                itemBuilder: (ctx, i) {
                  final opt = options.elementAt(i);
                  return InkWell(
                    onTap: () => onSelected(opt),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 12, vertical: 10),
                      child: Text(
                        opt,
                        style: const TextStyle(
                          fontSize: 13,
                          color: AppTheme.textPrimary,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                    ),
                  );
                },
              ),
            ),
          ),
        );
      },
    );
  }

  InputDecoration _decoration() => InputDecoration(
        hintText: hint,
        hintStyle: const TextStyle(
          color: AppTheme.textSecondary,
          fontSize: 14,
          fontWeight: FontWeight.w400,
        ),
        border: InputBorder.none,
        enabledBorder: InputBorder.none,
        focusedBorder: InputBorder.none,
        isDense: true,
        filled: false,
        contentPadding: const EdgeInsets.symmetric(vertical: 6),
      );

  TextStyle _textStyle() => const TextStyle(
        fontSize: 14,
        color: AppTheme.textPrimary,
        fontWeight: FontWeight.w500,
      );
}

/// Horizontal scrollable strip of industry pills. The selected industry
/// drives the role autocomplete suggestions and is sent to the backend
/// so prompts and default queries can be tuned per field.
class _IndustryPicker extends StatelessWidget {
  final String selectedId;
  final ValueChanged<String> onChanged;
  const _IndustryPicker({required this.selectedId, required this.onChanged});

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 36,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 4),
        itemCount: kIndustries.length,
        separatorBuilder: (_, __) => const SizedBox(width: 6),
        itemBuilder: (ctx, i) {
          final ind = kIndustries[i];
          final selected = ind.id == selectedId;
          return Material(
            color: selected ? AppTheme.primary : AppTheme.surface,
            borderRadius: BorderRadius.circular(18),
            child: InkWell(
              borderRadius: BorderRadius.circular(18),
              onTap: () => onChanged(ind.id),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(18),
                  border: Border.all(
                      color: selected
                          ? AppTheme.primary
                          : AppTheme.border),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(ind.emoji, style: const TextStyle(fontSize: 13)),
                    const SizedBox(width: 6),
                    Text(
                      ind.label,
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w600,
                        color:
                            selected ? Colors.white : AppTheme.textPrimary,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}

/// Distinctive LinkedIn search button with brand-blue gradient + count badge.
class _LinkedInPill extends StatelessWidget {
  final bool loading;
  final int? jobCount;
  final VoidCallback? onTap;

  const _LinkedInPill({
    required this.loading,
    required this.jobCount,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    const liBlue = Color(0xFF0A66C2);
    const liBlueDark = Color(0xFF004182);
    return Material(
      color: Colors.transparent,
      child: InkWell(
        borderRadius: BorderRadius.circular(28),
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          padding:
              const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          decoration: BoxDecoration(
            gradient: const LinearGradient(
              colors: [liBlue, liBlueDark],
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
            ),
            borderRadius: BorderRadius.circular(28),
            boxShadow: [
              BoxShadow(
                color: liBlue.withValues(alpha: 0.35),
                blurRadius: 14,
                offset: const Offset(0, 6),
              ),
            ],
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (loading)
                const SizedBox(
                  width: 14, height: 14,
                  child: CircularProgressIndicator(
                      strokeWidth: 2, color: Colors.white),
                )
              else
                Container(
                  width: 22,
                  height: 22,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: const Text(
                    'in',
                    style: TextStyle(
                      color: liBlue,
                      fontWeight: FontWeight.w900,
                      fontSize: 14,
                      height: 1.0,
                    ),
                  ),
                ),
              const SizedBox(width: 10),
              Text(
                loading
                    ? 'Searching LinkedIn\u2026'
                    : (jobCount == null
                        ? 'Search LinkedIn jobs'
                        : 'LinkedIn'),
                style: const TextStyle(
                  color: Colors.white,
                  fontWeight: FontWeight.w800,
                  fontSize: 14,
                  letterSpacing: 0.2,
                ),
              ),
              if (jobCount != null && !loading) ...[
                const SizedBox(width: 8),
                Container(
                  padding: const EdgeInsets.symmetric(
                      horizontal: 8, vertical: 2),
                  decoration: BoxDecoration(
                    color: Colors.white.withValues(alpha: 0.22),
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: Text(
                    '$jobCount',
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 12,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// Banner that checks if the Chrome extension is installed via the
/// `data-autoapply-ext` DOM attribute set by the content script.
/// Shows an install CTA if not detected; auto-hides once detected or
/// dismissed.
class _UpgradeFeatureRow extends StatelessWidget {
  final IconData icon;
  final String text;
  const _UpgradeFeatureRow({required this.icon, required this.text});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          Icon(icon, size: 16, color: const Color(0xFF6366f1)),
          const SizedBox(width: 8),
          Expanded(child: Text(text, style: const TextStyle(fontSize: 13, color: Color(0xFF374151)))),
        ],
      ),
    );
  }
}


/// Rich animated loading state shown while job discovery is running.
class _LoadingHero extends StatefulWidget {
  final int scanned;
  final int total;
  final VoidCallback onStop;

  const _LoadingHero({
    required this.scanned,
    required this.total,
    required this.onStop,
  });

  @override
  State<_LoadingHero> createState() => _LoadingHeroState();
}

class _LoadingHeroState extends State<_LoadingHero> {
  int _tipIndex = 0;
  Timer? _tipTimer;

  static const _tips = [
    'Hand-picking roles that match your skills, experience and goals\u2026',
    'Reading every job description so you don\u2019t have to.',
    'Scanning thousands of openings across top companies for you.',
    'Ranking each role by how well it fits your profile.',
    'Quality matches take a moment \u2014 we\u2019re being thorough so you don\u2019t waste time.',
    'Your shortlist is being built with care \u2014 almost there.',
    'Sit back. We\u2019re doing hours of work in minutes.',
    'Your next career move could be in the results loading right now.',
  ];

  @override
  void initState() {
    super.initState();
    _tipTimer = Timer.periodic(const Duration(seconds: 4), (_) {
      if (mounted) setState(() => _tipIndex = (_tipIndex + 1) % _tips.length);
    });
  }

  @override
  void dispose() {
    _tipTimer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final progress = widget.total > 0
        ? (widget.scanned / widget.total).clamp(0.0, 1.0)
        : 0.0;
    final pct = (progress * 100).toInt();
    final isCompact = MediaQuery.of(context).size.width < 600;
    final tipFontSize = isCompact ? 15.0 : 17.0;
    final headlineFontSize = isCompact ? 17.0 : 20.0;

    return Container(
      padding: EdgeInsets.all(isCompact ? 18 : 24),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            const Color(0xFF6366f1).withValues(alpha: 0.06),
            const Color(0xFF8b5cf6).withValues(alpha: 0.04),
            Colors.white,
          ],
        ),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: const Color(0xFF6366f1).withValues(alpha: 0.15)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header row
          Row(
            children: [
              Container(
                width: isCompact ? 40 : 48,
                height: isCompact ? 40 : 48,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: const LinearGradient(colors: [Color(0xFF6366f1), Color(0xFF8b5cf6)]),
                ),
                child: Icon(Icons.auto_awesome, color: Colors.white, size: isCompact ? 20 : 24),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Curating jobs made for you',
                      style: TextStyle(
                        fontWeight: FontWeight.w800,
                        fontSize: headlineFontSize,
                        color: const Color(0xFF1e1b4b),
                        height: 1.2,
                      ),
                    ),
                    const SizedBox(height: 4),
                    if (widget.total > 0)
                      Text(
                        widget.scanned == 0
                            ? 'Scanning ${widget.total} companies\u2026'
                            : '${widget.scanned} of ${widget.total} companies scanned ($pct%)',
                        style: const TextStyle(fontSize: 12, color: Color(0xFF6b7280)),
                      ),
                  ],
                ),
              ),
              TextButton.icon(
                onPressed: widget.onStop,
                icon: const Icon(Icons.stop_rounded, size: 16),
                label: const Text('Stop'),
                style: TextButton.styleFrom(
                  foregroundColor: AppTheme.error,
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  minimumSize: Size.zero,
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
              ),
            ],
          ),

          SizedBox(height: isCompact ? 14 : 18),

          // Progress bar
          ClipRRect(
            borderRadius: BorderRadius.circular(6),
            child: LinearProgressIndicator(
              value: widget.total > 0 ? progress : null,
              minHeight: 6,
              backgroundColor: const Color(0xFF6366f1).withValues(alpha: 0.1),
              valueColor: const AlwaysStoppedAnimation<Color>(Color(0xFF6366f1)),
            ),
          ),

          SizedBox(height: isCompact ? 16 : 20),

          // Rotating tip text \u2014 deliberately large and prominent so the
          // wait feels intentional and personal, not slow.
          AnimatedSwitcher(
            duration: const Duration(milliseconds: 450),
            child: Container(
              key: ValueKey<int>(_tipIndex),
              padding: EdgeInsets.symmetric(
                horizontal: isCompact ? 12 : 16,
                vertical: isCompact ? 12 : 14,
              ),
              decoration: BoxDecoration(
                color: const Color(0xFF8b5cf6).withValues(alpha: 0.07),
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: const Color(0xFF8b5cf6).withValues(alpha: 0.18)),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Padding(
                    padding: EdgeInsets.only(top: isCompact ? 2 : 3),
                    child: Icon(Icons.auto_awesome,
                        size: isCompact ? 18 : 20, color: const Color(0xFF7c3aed)),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      _tips[_tipIndex],
                      style: TextStyle(
                        fontSize: tipFontSize,
                        color: const Color(0xFF312e81),
                        fontWeight: FontWeight.w600,
                        height: 1.4,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),

          SizedBox(height: isCompact ? 10 : 12),

          // Reassurance footer
          Text(
            'This usually takes a minute or two \u2014 we\u2019re searching across thousands of roles to bring back only the ones worth your time.',
            style: TextStyle(
              fontSize: isCompact ? 12 : 13,
              color: const Color(0xFF6b7280),
              height: 1.4,
            ),
          ),
        ],
      ),
    );
  }
}


class _ExtensionInstallBanner extends StatefulWidget {
  const _ExtensionInstallBanner();

  @override
  State<_ExtensionInstallBanner> createState() => _ExtensionInstallBannerState();
}

class _ExtensionInstallBannerState extends State<_ExtensionInstallBanner> {
  bool _dismissed = false;
  bool _installed = false;
  Timer? _pollTimer;

  static const _kDismissedKey = 'autoapply.ext_banner_dismissed.v1';
  static const _storeUrl =
      'https://chromewebstore.google.com/detail/autoapply-%E2%80%93-job-form-auto/anjgpjhdecnibcbogkclafanemofndea';

  @override
  void initState() {
    super.initState();
    _checkInstalled();
    // Check if previously dismissed this session
    final prev = html.window.sessionStorage[_kDismissedKey];
    if (prev == 'true') _dismissed = true;
    // Poll every 3s in case user installs while on the page
    _pollTimer = Timer.periodic(const Duration(seconds: 3), (_) => _checkInstalled());
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }

  void _checkInstalled() {
    final attr = html.document.documentElement?.getAttribute('data-autoapply-ext') ?? '';
    final nowInstalled = attr == 'installed' || attr == 'connected';
    if (nowInstalled != _installed && mounted) {
      setState(() => _installed = nowInstalled);
      if (nowInstalled) _pollTimer?.cancel();
    }
  }

  void _dismiss() {
    setState(() => _dismissed = true);
    html.window.sessionStorage[_kDismissedKey] = 'true';
  }

  @override
  Widget build(BuildContext context) {
    if (_installed || _dismissed) return const SizedBox.shrink();

    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 12, 24, 0),
      child: Container(
        decoration: BoxDecoration(
          gradient: LinearGradient(
            colors: [
              const Color(0xFF6366f1).withValues(alpha: 0.08),
              const Color(0xFF8b5cf6).withValues(alpha: 0.06),
            ],
          ),
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: const Color(0xFF6366f1).withValues(alpha: 0.25)),
        ),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 14, 10, 14),
          child: Row(
            children: [
              Container(
                width: 40, height: 40,
                decoration: BoxDecoration(
                  color: const Color(0xFF6366f1).withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(10),
                ),
                child: const Icon(Icons.extension, color: Color(0xFF6366f1), size: 22),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'Install Chrome Extension',
                      style: TextStyle(fontWeight: FontWeight.w700, fontSize: 13, color: Color(0xFF1e1b4b)),
                    ),
                    const SizedBox(height: 2),
                    const Text(
                      'Auto-fill job applications with one click on any career site.',
                      style: TextStyle(fontSize: 11, color: Color(0xFF4b5563)),
                    ),
                    const SizedBox(height: 8),
                    SizedBox(
                      height: 32,
                      child: FilledButton.icon(
                        onPressed: () => html.window.open(_storeUrl, '_blank'),
                        icon: const Icon(Icons.download_rounded, size: 15),
                        label: const Text('Get Extension', style: TextStyle(fontSize: 12)),
                        style: FilledButton.styleFrom(
                          backgroundColor: const Color(0xFF6366f1),
                          padding: const EdgeInsets.symmetric(horizontal: 14),
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              IconButton(
                onPressed: _dismiss,
                icon: const Icon(Icons.close, size: 18, color: Color(0xFF9ca3af)),
                splashRadius: 18,
                tooltip: 'Dismiss',
              ),
            ],
          ),
        ),
      ),
    );
  }
}


class _JobCard extends StatelessWidget {
  final Map<String, dynamic> job;
  const _JobCard({required this.job});

  @override
  Widget build(BuildContext context) {
    final title = job['title']?.toString() ?? '';
    final company = job['company']?.toString() ?? '';
    final location = job['location']?.toString() ?? '';
    final matchScore = job['matchScore'] as int? ?? 0;
    final aiScore = job['aiScore'] as int? ?? 0;
    final rawAiReason = job['aiReason']?.toString() ?? '';
    final aiReason = (rawAiReason.toLowerCase().startsWith('rerank') ||
                       rawAiReason.toLowerCase().contains('failed'))
        ? ''
        : rawAiReason;
    final matchReason = job['matchReason']?.toString() ?? '';
    final reasonText = aiReason.isNotEmpty ? aiReason : matchReason;
    final skillScore = job['skillScore'] as int? ?? 0;
    final titleScore = job['titleScore'] as int? ?? 0;
    final recencyScore = job['recencyScore'] as int? ?? 0;
    final expScore = job['experienceScore'] as int? ?? 0;
    final url = job['url']?.toString() ?? '';
    final skills = (job['skills'] as List?)?.cast<String>() ?? [];
    final displayScore = aiScore > 0 ? aiScore : matchScore;

    return Card(
      margin: const EdgeInsets.only(bottom: 6),
      child: InkWell(
        borderRadius: AppTheme.cardRadius,
        onTap: url.isNotEmpty
            ? () => _showJobApplySheet(context, title, company, url)
            : null,
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Text(title, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
                  ),
                  const SizedBox(width: 8),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                    decoration: BoxDecoration(
                      color: _matchColor(displayScore).withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Text(aiScore > 0 ? 'AI: $aiScore%' : '$matchScore%',
                        style: TextStyle(color: _matchColor(displayScore), fontWeight: FontWeight.bold, fontSize: 13)),
                  ),
                ],
              ),
              const SizedBox(height: 4),
              Text(location, style: const TextStyle(color: AppTheme.textSecondary, fontSize: 12)),
              if (reasonText.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 3),
                  child: Text(
                    aiReason.isNotEmpty ? 'AI: $reasonText' : 'Why: $reasonText',
                    style: const TextStyle(color: AppTheme.primary, fontSize: 11, fontStyle: FontStyle.italic),
                  ),
                ),

              if (skills.isNotEmpty) ...[
                const SizedBox(height: 6),
                Wrap(
                  spacing: 4,
                  runSpacing: 2,
                  children: skills.take(4).map((s) => Container(
                        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                        decoration: BoxDecoration(
                          color: Colors.grey[100],
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Text(s, style: const TextStyle(fontSize: 10, color: AppTheme.textPrimary)),
                      )).toList(),
                ),
              ],

              const SizedBox(height: 6),
              Row(
                children: [
                  _ScorePill('Skills', skillScore),
                  const SizedBox(width: 4),
                  _ScorePill('Title', titleScore),
                  const SizedBox(width: 4),
                  _ScorePill('Exp', expScore),
                  const SizedBox(width: 4),
                  _ScorePill('Fresh', recencyScore),
                  const Spacer(),
                  if (url.isNotEmpty)
                    const Icon(Icons.open_in_new, size: 14, color: AppTheme.primary),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Color _matchColor(int score) {
    if (score >= 70) return AppTheme.success;
    if (score >= 40) return AppTheme.warning;
    return AppTheme.error;
  }
}

/// Big, glanceable feature tile that markets the AI resume-tailoring
/// service. Shown right under the search hero on the Discover page.
class _ResumeTailorPromo extends StatelessWidget {
  final bool loading;
  final bool hasJobs;
  final VoidCallback? onTap;

  const _ResumeTailorPromo({
    required this.loading,
    required this.hasJobs,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: AppTheme.cardRadius,
        child: Ink(
          decoration: BoxDecoration(
            borderRadius: AppTheme.cardRadius,
            gradient: LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [
                AppTheme.primary.withValues(alpha: 0.10),
                AppTheme.primarySoft,
                Colors.white,
              ],
              stops: const [0.0, 0.55, 1.0],
            ),
            border: Border.all(color: AppTheme.primary.withValues(alpha: 0.25)),
            boxShadow: [
              BoxShadow(
                color: AppTheme.primary.withValues(alpha: 0.08),
                blurRadius: 18,
                offset: const Offset(0, 6),
              ),
            ],
          ),
          child: ClipRRect(
            borderRadius: AppTheme.cardRadius,
            child: Stack(
              children: [
                // Watermark "AI" in the background, light primary tint.
                Positioned(
                  right: -16,
                  top: -28,
                  child: IgnorePointer(
                    child: Text(
                      'AI',
                      style: TextStyle(
                        fontSize: 180,
                        fontWeight: FontWeight.w900,
                        color: AppTheme.primary.withValues(alpha: 0.06),
                        height: 1,
                        letterSpacing: -8,
                      ),
                    ),
                  ),
                ),
                // Background marketing copy, very low contrast.
                Positioned(
                  left: 24,
                  bottom: 10,
                  right: 180,
                  child: IgnorePointer(
                    child: Text(
                      'BENCHMARKED  ·  KEYWORD-OPTIMISED  ·  ATS-READY  ·  '
                      'DATA-DRIVEN  ·  TOP-OF-PILE',
                      maxLines: 1,
                      overflow: TextOverflow.fade,
                      softWrap: false,
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 2.4,
                        color: AppTheme.primary.withValues(alpha: 0.18),
                      ),
                    ),
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(24, 22, 20, 22),
                  child: LayoutBuilder(
                    builder: (ctx, c) {
                      final compact = c.maxWidth < 560;
                      return Flex(
                        direction: compact ? Axis.vertical : Axis.horizontal,
                        crossAxisAlignment: compact
                            ? CrossAxisAlignment.start
                            : CrossAxisAlignment.center,
                        children: [
                          Container(
                            width: 56,
                            height: 56,
                            decoration: BoxDecoration(
                              color: AppTheme.primary,
                              borderRadius: BorderRadius.circular(16),
                              boxShadow: [
                                BoxShadow(
                                  color: AppTheme.primary.withValues(alpha: 0.35),
                                  blurRadius: 14,
                                  offset: const Offset(0, 4),
                                ),
                              ],
                            ),
                            child: const Icon(Icons.auto_awesome,
                                color: Colors.white, size: 28),
                          ),
                          SizedBox(width: compact ? 0 : 18, height: compact ? 14 : 0),
                          Expanded(
                            flex: compact ? 0 : 1,
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Row(children: [
                                  const Text(
                                    'AI Resume Tailoring',
                                    style: TextStyle(
                                      fontSize: 18,
                                      fontWeight: FontWeight.w800,
                                      color: AppTheme.primary,
                                      letterSpacing: -0.2,
                                    ),
                                  ),
                                  const SizedBox(width: 8),
                                  Container(
                                    padding: const EdgeInsets.symmetric(
                                        horizontal: 8, vertical: 2),
                                    decoration: BoxDecoration(
                                      color: AppTheme.primary,
                                      borderRadius: BorderRadius.circular(20),
                                    ),
                                    child: const Text(
                                      'NEW',
                                      style: TextStyle(
                                        fontSize: 9,
                                        fontWeight: FontWeight.w800,
                                        color: Colors.white,
                                        letterSpacing: 0.8,
                                      ),
                                    ),
                                  ),
                                ]),
                                const SizedBox(height: 6),
                                Text(
                                  hasJobs
                                      ? 'Trained on the resumes pulling the most '
                                        'recruiter callbacks \u2014 we restructure your '
                                        'bullets, surface missing keywords, and '
                                        'sharpen your headline so you outrank the '
                                        'other applicants for the jobs above.'
                                      : 'Run a search first \u2014 then we benchmark '
                                        'your resume against the top-performing '
                                        'profiles in our dataset and rewrite it to '
                                        'land at the top of the recruiter\u2019s pile.',
                                  style: const TextStyle(
                                    fontSize: 13,
                                    height: 1.4,
                                    color: AppTheme.textSecondary,
                                  ),
                                ),
                                const SizedBox(height: 10),
                                Wrap(
                                  spacing: 14,
                                  runSpacing: 4,
                                  children: const [
                                    _PromoBullet(
                                        icon: Icons.emoji_events,
                                        text: 'Outrank other applicants'),
                                    _PromoBullet(
                                        icon: Icons.search,
                                        text: 'Beats ATS filters'),
                                    _PromoBullet(
                                        icon: Icons.trending_up,
                                        text: 'Modeled on top-callback resumes'),
                                  ],
                                ),
                              ],
                            ),
                          ),
                          SizedBox(width: compact ? 0 : 16, height: compact ? 14 : 0),
                          ElevatedButton.icon(
                            onPressed: onTap,
                            icon: loading
                                ? const SizedBox(
                                    width: 16, height: 16,
                                    child: CircularProgressIndicator(
                                        strokeWidth: 2, color: Colors.white))
                                : const Icon(Icons.tips_and_updates, size: 18),
                            label: Text(
                              loading ? 'Tailoring\u2026' : 'Tailor my resume',
                              style: const TextStyle(fontWeight: FontWeight.w700),
                            ),
                            style: ElevatedButton.styleFrom(
                              backgroundColor: AppTheme.primary,
                              foregroundColor: Colors.white,
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 22, vertical: 14),
                              shape: RoundedRectangleBorder(
                                  borderRadius: BorderRadius.circular(12)),
                              elevation: 0,
                            ),
                          ),
                        ],
                      );
                    },
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _PromoBullet extends StatelessWidget {
  final IconData icon;
  final String text;
  const _PromoBullet({required this.icon, required this.text});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 13, color: AppTheme.primary),
        const SizedBox(width: 4),
        Text(text,
            style: const TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w600,
              color: AppTheme.primary,
            )),
      ],
    );
  }
}

class _ScorePill extends StatelessWidget {
  final String label;
  final int value;
  const _ScorePill(this.label, this.value);

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: Colors.grey[50],
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: Colors.grey[200]!),
      ),
      child: Text('$label $value%', style: const TextStyle(fontSize: 9, color: AppTheme.textSecondary)),
    );
  }
}

// ── Apply helpers (accessible from _JobCard) ────────────────────────────

bool _isExtInstalled() {
  final attr = html.document.documentElement?.getAttribute('data-autoapply-ext') ?? '';
  return attr == 'installed' || attr == 'connected';
}

void _showJobApplySheet(BuildContext context, String title, String company, String url) {
  showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
    ),
    builder: (ctx) => Padding(
      padding: const EdgeInsets.fromLTRB(24, 16, 24, 32),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Center(child: Container(width: 40, height: 4,
            decoration: BoxDecoration(color: Colors.grey[300], borderRadius: BorderRadius.circular(2)))),
          const SizedBox(height: 16),
          Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
          if (company.isNotEmpty) Text(company, style: const TextStyle(color: AppTheme.textSecondary, fontSize: 13)),
          const SizedBox(height: 20),

          // Primary: Apply with Autofill (always shown)
          SizedBox(width: double.infinity, child: FilledButton.icon(
            onPressed: () {
              Navigator.pop(ctx);
              final autofillUrl = url.contains('#') ? '$url&__autoapply=1' : '$url#__autoapply';
              html.window.open(autofillUrl, '_blank');
            },
            icon: const Icon(Icons.flash_on, size: 18),
            label: const Text('Apply with Autofill'),
            style: FilledButton.styleFrom(
              backgroundColor: AppTheme.primary,
              padding: const EdgeInsets.symmetric(vertical: 14),
            ),
          )),
          const SizedBox(height: 10),

          // Visit job page (fallback)
          SizedBox(width: double.infinity, child: OutlinedButton.icon(
            onPressed: () {
              Navigator.pop(ctx);
              html.window.open(url, '_blank');
            },
            icon: const Icon(Icons.open_in_new, size: 16),
            label: const Text('Visit Job Page'),
            style: OutlinedButton.styleFrom(
              padding: const EdgeInsets.symmetric(vertical: 14),
            ),
          )),

          const SizedBox(height: 16),
          // How to use guide
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: Colors.grey.shade50,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: Colors.grey.shade200),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Row(children: [
                  Icon(Icons.info_outline, size: 16, color: AppTheme.textSecondary),
                  SizedBox(width: 6),
                  Text('How to use AutoApply', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
                ]),
                const SizedBox(height: 8),
                _guideStep('1', 'Install the Chrome extension (one-time)', Icons.extension),
                _guideStep('2', 'Click "Apply with Autofill" above — opens the job page', Icons.open_in_new),
                _guideStep('3', 'Click the AutoApply icon in Chrome toolbar', Icons.touch_app),
                _guideStep('4', 'Your resume info fills the form automatically', Icons.auto_fix_high),
                const SizedBox(height: 8),
                InkWell(
                  onTap: () => html.window.open(
                    'https://chromewebstore.google.com/detail/autoapply-%E2%80%93-job-form-auto/anjgpjhdecnibcbogkclafanemofndea',
                    '_blank',
                  ),
                  child: const Text(
                    'Get the extension from Chrome Web Store →',
                    style: TextStyle(fontSize: 12, color: AppTheme.primary, decoration: TextDecoration.underline),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    ),
  );
}

Widget _guideStep(String num, String text, IconData icon) {
  return Padding(
    padding: const EdgeInsets.symmetric(vertical: 3),
    child: Row(children: [
      Container(
        width: 20, height: 20,
        decoration: BoxDecoration(color: AppTheme.primary, borderRadius: BorderRadius.circular(10)),
        child: Center(child: Text(num, style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.bold))),
      ),
      const SizedBox(width: 8),
      Icon(icon, size: 14, color: AppTheme.textSecondary),
      const SizedBox(width: 6),
      Expanded(child: Text(text, style: const TextStyle(fontSize: 12))),
    ]),
  );
}
