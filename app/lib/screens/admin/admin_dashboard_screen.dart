import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';
import 'package:provider/provider.dart';
import 'package:auto_apply/services/api_service.dart';
import 'package:auto_apply/providers/auth_provider.dart';
import 'package:auto_apply/config/theme.dart';

/// Super-admin dashboard. Backend enforces email allowlist; this screen
/// also performs a client-side check before rendering so non-admin users
/// see a friendly "not authorized" page instead of an empty error.
class AdminDashboardScreen extends StatefulWidget {
  const AdminDashboardScreen({super.key});

  @override
  State<AdminDashboardScreen> createState() => _AdminDashboardScreenState();
}

class _AdminDashboardScreenState extends State<AdminDashboardScreen>
    with SingleTickerProviderStateMixin {
  late TabController _tabs;
  int _days = 7;

  Map<String, dynamic>? _summary;
  Map<String, dynamic>? _usage;
  Map<String, dynamic>? _users;
  Map<String, dynamic>? _errors;
  Map<String, dynamic>? _funnel;
  Map<String, dynamic>? _costs;
  Map<String, dynamic>? _runs;
  Map<String, dynamic>? _feedback;
  Map<String, dynamic>? _billing;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _tabs = TabController(length: 9, vsync: this);
    _load();
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final api = context.read<ApiService>();
      final futures = await Future.wait([
        api.get('/api/v1/admin/dashboard/summary', queryParameters: {'days': _days}),
        api.get('/api/v1/admin/dashboard/usage', queryParameters: {'days': _days}),
        api.get('/api/v1/admin/dashboard/users', queryParameters: {'days': _days}),
        api.get('/api/v1/admin/dashboard/errors', queryParameters: {'days': _days}),
        api.get('/api/v1/admin/dashboard/funnel', queryParameters: {'days': _days}),
        api.get('/api/v1/admin/dashboard/costs', queryParameters: {'days': _days}),
        api.get('/api/v1/admin/dashboard/runs', queryParameters: {'days': _days}),
        api.get('/api/v1/admin/dashboard/feedback', queryParameters: {'days': _days}),
        api.get('/api/v1/admin/dashboard/subscriptions', queryParameters: {'days': _days < 90 ? 90 : _days}),
      ]);
      if (!mounted) return;
      setState(() {
        _summary = futures[0].data as Map<String, dynamic>;
        _usage = futures[1].data as Map<String, dynamic>;
        _users = futures[2].data as Map<String, dynamic>;
        _errors = futures[3].data as Map<String, dynamic>;
        _funnel = futures[4].data as Map<String, dynamic>;
        _costs = futures[5].data as Map<String, dynamic>;
        _runs = futures[6].data as Map<String, dynamic>;
        _feedback = futures[7].data as Map<String, dynamic>;
        _billing = futures[8].data as Map<String, dynamic>;
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

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthProvider>();
    final email = (auth.email ?? '').toLowerCase().trim();
    // Admin allow-list. Configure via --dart-define=ADMIN_EMAILS=a@example.com,b@example.com
    const adminRaw = String.fromEnvironment('ADMIN_EMAILS', defaultValue: 'vibhuu1720@gmail.com');
    final allowedEmails = adminRaw
        .split(',')
        .map((e) => e.trim().toLowerCase())
        .where((e) => e.isNotEmpty)
        .toSet();
    if (email.isNotEmpty && !allowedEmails.contains(email)) {
      return Scaffold(
        appBar: AppBar(title: const Text('Admin')),
        body: const Center(
          child: Padding(
            padding: EdgeInsets.all(24),
            child: Text(
              'You do not have access to the admin dashboard.',
              style: TextStyle(fontSize: 16),
            ),
          ),
        ),
      );
    }

    return Scaffold(
      backgroundColor: const Color(0xFFF5F7FB),
      appBar: AppBar(
        title: const Text('ApplyRight — Admin'),
        actions: [
          DropdownButton<int>(
            value: _days,
            underline: const SizedBox.shrink(),
            items: const [
              DropdownMenuItem(value: 1, child: Text('  1 day  ')),
              DropdownMenuItem(value: 7, child: Text('  7 days  ')),
              DropdownMenuItem(value: 14, child: Text('  14 days  ')),
              DropdownMenuItem(value: 30, child: Text('  30 days  ')),
              DropdownMenuItem(value: 90, child: Text('  90 days  ')),
            ],
            onChanged: (v) {
              if (v == null) return;
              setState(() => _days = v);
              _load();
            },
          ),
          IconButton(
            onPressed: _load,
            icon: const Icon(Icons.refresh_rounded),
            tooltip: 'Refresh',
          ),
          const SizedBox(width: 8),
        ],
        bottom: TabBar(
          controller: _tabs,
          isScrollable: true,
          tabs: const [
            Tab(text: 'Overview'),
            Tab(text: 'Users'),
            Tab(text: 'Billing'),
            Tab(text: 'Usage'),
            Tab(text: 'Costs'),
            Tab(text: 'Funnel'),
            Tab(text: 'Runs'),
            Tab(text: 'Feedback'),
            Tab(text: 'Errors'),
          ],
        ),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? _ErrorView(message: _error!, onRetry: _load)
              : TabBarView(
                  controller: _tabs,
                  children: [
                    _OverviewTab(summary: _summary, usage: _usage),
                    _UsersTab(users: _users, days: _days),
                    _BillingTab(billing: _billing),
                    _UsageTab(usage: _usage),
                    _CostsTab(costs: _costs),
                    _FunnelTab(funnel: _funnel),
                    _RunsTab(runs: _runs),
                    _FeedbackTab(feedback: _feedback),
                    _ErrorsTab(errors: _errors),
                  ],
                ),
    );
  }
}

// ── Overview Tab ──────────────────────────────────────────────────────────
class _OverviewTab extends StatelessWidget {
  final Map<String, dynamic>? summary;
  final Map<String, dynamic>? usage;
  const _OverviewTab({required this.summary, required this.usage});

  @override
  Widget build(BuildContext context) {
    if (summary == null) return const Center(child: Text('No data'));
    final users = summary!['users'] as Map<String, dynamic>? ?? {};
    final billing = summary!['billing'] as Map<String, dynamic>? ?? {};
    final funnel = summary!['discoveryFunnel'] as Map<String, dynamic>? ?? {};
    final usage24h = summary!['usage24h'] as Map<String, dynamic>? ?? {};
    final series = (usage?['series'] as List?) ?? [];
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Wrap(
            spacing: 16,
            runSpacing: 16,
            children: [
              _StatCard(label: 'Total users', value: '${users['total'] ?? 0}',
                  icon: Icons.group, color: Colors.blue),
              _StatCard(label: 'New users (window)', value: '${users['new'] ?? 0}',
                  icon: Icons.person_add, color: Colors.green),
              _StatCard(label: 'Active users', value: '${users['active'] ?? 0}',
                  icon: Icons.bolt, color: Colors.orange),
              _StatCard(label: 'Pro users', value: '${billing['proUsers'] ?? 0}',
                  icon: Icons.workspace_premium, color: Colors.deepPurple),
              _StatCard(label: 'Active subs', value: '${billing['activeSubscriptions'] ?? 0}',
                  icon: Icons.autorenew, color: Colors.green.shade700),
              _StatCard(label: 'Cancelled subs', value: '${billing['cancelledSubscriptions'] ?? 0}',
                  icon: Icons.cancel_schedule_send, color: Colors.orange.shade800),
              _StatCard(label: 'Razorpay', value: '${billing['razorpayCustomers'] ?? 0}',
                  icon: Icons.currency_rupee, color: Colors.indigo),
              _StatCard(label: 'Lemon Squeezy', value: '${billing['lemonsqueezyCustomers'] ?? 0}',
                  icon: Icons.attach_money, color: Colors.teal.shade700),
              _StatCard(label: 'Discover calls', value: '${funnel['discoverCalls'] ?? 0}',
                  icon: Icons.search, color: Colors.purple),
              _StatCard(label: 'Jobs scraped', value: '${funnel['totalScraped'] ?? 0}',
                  icon: Icons.cloud_download, color: Colors.teal),
              _StatCard(label: 'Jobs surfaced', value: '${funnel['totalReturned'] ?? 0}',
                  icon: Icons.check_circle, color: Colors.indigo),
              _StatCard(label: 'Errors', value: '${funnel['errorEvents'] ?? 0}',
                  icon: Icons.error_outline, color: Colors.red),
              _StatCard(label: 'Avg duration',
                  value: '${((funnel['avgDurationMs'] ?? 0) as int) ~/ 1000}s',
                  icon: Icons.timer, color: Colors.brown),
              _StatCard(label: 'Discover (24h)', value: '${usage24h['discover'] ?? 0}',
                  icon: Icons.explore, color: Colors.blueGrey),
              _StatCard(label: 'LinkedIn (24h)', value: '${usage24h['linkedin'] ?? 0}',
                  icon: Icons.public, color: const Color(0xFF0A66C2)),
              _StatCard(label: 'Autofill (24h)', value: '${usage24h['autofill'] ?? 0}',
                  icon: Icons.auto_fix_high, color: Colors.cyan),
              _StatCard(label: 'Tailor (24h)', value: '${usage24h['tailor'] ?? 0}',
                  icon: Icons.content_cut, color: Colors.pink),
              _StatCard(label: 'Resume upload (24h)', value: '${usage24h['resume_upload'] ?? 0}',
                  icon: Icons.upload_file, color: Colors.deepOrange),
            ],
          ),
          const SizedBox(height: 24),
          _ChartCard(
            title: 'Daily activity',
            child: SizedBox(
              height: 280,
              child: _LineChart(
                series: series.cast<Map<String, dynamic>>(),
                keys: const ['discoverCalls', 'matched', 'errors'],
                colors: const [Colors.blue, Colors.green, Colors.red],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ── Users Tab ─────────────────────────────────────────────────────────────
class _UsersTab extends StatefulWidget {
  final Map<String, dynamic>? users;
  final int days;
  const _UsersTab({required this.users, required this.days});

  @override
  State<_UsersTab> createState() => _UsersTabState();
}

class _UsersTabState extends State<_UsersTab> {
  String _filter = '';

  @override
  Widget build(BuildContext context) {
    final all = (widget.users?['users'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    final q = _filter.trim().toLowerCase();
    final list = q.isEmpty
        ? all
        : all.where((u) {
            final blob = [
              u['email'],
              u['name'],
              u['userId'],
              u['country'],
              u['tier'],
              u['subscriptionStatus'],
              u['paymentProvider'],
              (u['recentQueries'] as List?)?.join(' '),
              (u['locations'] as List?)?.join(' '),
            ].join(' ').toLowerCase();
            return blob.contains(q);
          }).toList();

    if (all.isEmpty) return const Center(child: Text('No users'));

    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          TextField(
            decoration: InputDecoration(
              hintText: 'Filter by email, name, country, tier, queries…',
              prefixIcon: const Icon(Icons.search),
              border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
              isDense: true,
            ),
            onChanged: (v) => setState(() => _filter = v),
          ),
          const SizedBox(height: 12),
          Text(
            'Tap a row for full profile, payments, and discover runs. '
            'Window: ${widget.days} days.',
            style: TextStyle(fontSize: 12, color: Colors.grey.shade700),
          ),
          const SizedBox(height: 12),
          _ChartCard(
            title: 'Users (${list.length} of ${all.length})',
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: DataTable(
                columnSpacing: 14,
                showCheckboxColumn: false,
                columns: const [
                  DataColumn(label: Text('Email')),
                  DataColumn(label: Text('Tier')),
                  DataColumn(label: Text('Sub status')),
                  DataColumn(label: Text('Provider')),
                  DataColumn(label: Text('Type')),
                  DataColumn(label: Text('Paid')),
                  DataColumn(label: Text('Start')),
                  DataColumn(label: Text('Access end')),
                  DataColumn(label: Text('Country')),
                  DataColumn(label: Text('Locations')),
                  DataColumn(label: Text('Queries')),
                  DataColumn(label: Text('Last seen')),
                  DataColumn(label: Text('Calls'), numeric: true),
                  DataColumn(label: Text('Disc 24h'), numeric: true),
                  DataColumn(label: Text('LI 24h'), numeric: true),
                  DataColumn(label: Text('Auto 24h'), numeric: true),
                  DataColumn(label: Text('Tailor 24h'), numeric: true),
                  DataColumn(label: Text('Upload 24h'), numeric: true),
                ],
                rows: [
                  for (final u in list)
                    DataRow(
                      onSelectChanged: (_) => _openUserDetail(context, u),
                      cells: [
                        DataCell(Text(u['email']?.toString() ?? '—',
                            overflow: TextOverflow.ellipsis)),
                        DataCell(_tierChip(u['tier']?.toString() ?? 'free')),
                        DataCell(Text(u['subscriptionStatus']?.toString() ?? '—')),
                        DataCell(Text(u['paymentProvider']?.toString() ?? '—')),
                        DataCell(Text(u['paymentType']?.toString() ?? '—')),
                        DataCell(Text(
                            u['lifetimePaidDisplay']?.toString() ??
                                u['amountPaidDisplay']?.toString() ??
                                '—')),
                        DataCell(Text(_shortDate(
                            u['subscriptionStart'] ?? u['firstPaymentAt']))),
                        DataCell(Text(_shortDate(
                            u['accessEnd'] ?? u['endsAt'] ?? u['renewsAt']))),
                        DataCell(Text(u['country']?.toString() ?? '—')),
                        DataCell(Text(
                          ((u['locations'] as List?) ?? [])
                              .take(2)
                              .join(', '),
                          overflow: TextOverflow.ellipsis,
                        )),
                        DataCell(Text(
                          ((u['recentQueries'] as List?) ?? [])
                              .take(2)
                              .join(' · '),
                          overflow: TextOverflow.ellipsis,
                        )),
                        DataCell(Text(_shortDate(u['lastSeen']))),
                        DataCell(Text('${u['apiCalls'] ?? 0}')),
                        DataCell(Text('${u['discoverUsage24h'] ?? 0}')),
                        DataCell(Text('${u['linkedinUsage24h'] ?? 0}')),
                        DataCell(Text('${u['autofillUsage24h'] ?? 0}')),
                        DataCell(Text('${u['tailorUsage24h'] ?? 0}')),
                        DataCell(Text('${u['resumeUploadUsage24h'] ?? 0}')),
                      ],
                    ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  void _openUserDetail(BuildContext context, Map<String, dynamic> u) {
    final uid = u['userId']?.toString();
    if (uid == null || uid.isEmpty) return;
    showDialog<void>(
      context: context,
      builder: (ctx) => _UserDetailDialog(userId: uid, days: widget.days),
    );
  }
}

// ── Billing Tab ─────────────────────────────────────────────────────────────
class _BillingTab extends StatelessWidget {
  final Map<String, dynamic>? billing;
  const _BillingTab({required this.billing});

  @override
  Widget build(BuildContext context) {
    final list =
        (billing?['subscriptions'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    if (list.isEmpty) {
      return const Center(
        child: Text('No payment records in this window.\n'
            'Payments appear after Razorpay or Lemon Squeezy checkout.'),
      );
    }
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: _ChartCard(
        title: 'Payments (${list.length})',
        child: SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: DataTable(
            columnSpacing: 14,
            columns: const [
              DataColumn(label: Text('Date')),
              DataColumn(label: Text('Email')),
              DataColumn(label: Text('Amount')),
              DataColumn(label: Text('Provider')),
              DataColumn(label: Text('Plan')),
              DataColumn(label: Text('Interval')),
              DataColumn(label: Text('Type')),
              DataColumn(label: Text('Status')),
              DataColumn(label: Text('Access until')),
              DataColumn(label: Text('Payment ID')),
            ],
            rows: [
              for (final r in list)
                DataRow(cells: [
                  DataCell(Text(_shortDate(r['createdAt']))),
                  DataCell(Text(r['email']?.toString() ?? '—',
                      overflow: TextOverflow.ellipsis)),
                  DataCell(Text(r['amountDisplay']?.toString() ?? '—')),
                  DataCell(Text(r['provider']?.toString() ?? '—')),
                  DataCell(Text(r['planId']?.toString() ?? '—')),
                  DataCell(Text(r['interval']?.toString() ?? '—')),
                  DataCell(Text(r['paymentType']?.toString() ?? '—')),
                  DataCell(Text(r['status']?.toString() ?? '—')),
                  DataCell(Text(_shortDate(r['renewsAt']))),
                  DataCell(Text(r['paymentId']?.toString() ?? '—',
                      overflow: TextOverflow.ellipsis)),
                ]),
            ],
          ),
        ),
      ),
    );
  }
}

// ── User detail dialog ──────────────────────────────────────────────────────
class _UserDetailDialog extends StatefulWidget {
  final String userId;
  final int days;
  const _UserDetailDialog({required this.userId, required this.days});

  @override
  State<_UserDetailDialog> createState() => _UserDetailDialogState();
}

class _UserDetailDialogState extends State<_UserDetailDialog> {
  bool _loading = true;
  String? _error;
  Map<String, dynamic>? _data;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final api = context.read<ApiService>();
      final resp = await api.get(
        '/api/v1/admin/dashboard/user/${widget.userId}',
        queryParameters: {'days': widget.days},
      );
      if (!mounted) return;
      setState(() {
        _data = resp.data is Map ? Map<String, dynamic>.from(resp.data as Map) : null;
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

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text('User — ${widget.userId}'),
      content: SizedBox(
        width: 560,
        child: _loading
            ? const SizedBox(
                height: 120, child: Center(child: CircularProgressIndicator()))
            : _error != null
                ? Text(_error!, style: const TextStyle(color: Colors.red))
                : _buildBody(),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Close'),
        ),
      ],
    );
  }

  Widget _buildBody() {
    final d = _data!;
    final billing = (d['billing'] as Map?)?.cast<String, dynamic>() ?? {};
    final profile = (d['profile'] as Map?)?.cast<String, dynamic>() ?? {};
    final payments = (d['payments'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    final runs = (d['discoverRuns'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    final totals = (d['totals'] as Map?)?.cast<String, dynamic>() ?? {};
    final usage24h = (d['usage24h'] as Map?)?.cast<String, dynamic>() ?? {};

    return SingleChildScrollView(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _detailSection('Account', [
            _kv('Email', profile['email']),
            _kv('Name', profile['name']),
            _kv('Tier', billing['tier'] ?? profile['tier']),
            _kv('Country', (profile['applicationDetails'] as Map?)?['country']),
            _kv('Signed up', _shortDate(profile['createdAt'])),
          ]),
          _detailSection('Subscription & payments', [
            _kv('Status', billing['subscriptionStatus']),
            _kv('Provider', billing['paymentProvider']),
            _kv('Payment type', billing['paymentType']),
            _kv('Plan', billing['planId']),
            _kv('Interval', billing['interval']),
            _kv('Last period amount', billing['amountPaidDisplay']),
            _kv('Lifetime paid', billing['amountPaidDisplay'] ?? billing['lifetimePaidDisplay']),
            _kv('Payments count', '${billing['paymentCount'] ?? payments.length}'),
            _kv('Started', _shortDate(billing['subscriptionStart'] ?? billing['firstPaymentAt'])),
            _kv('Access ends', _shortDate(billing['accessEnd'] ?? billing['endsAt'] ?? billing['renewsAt'])),
            _kv('Cancelled', _shortDate(billing['cancelledAt'])),
            _kv('Razorpay payment', billing['rzpPaymentId']),
            _kv('Razorpay sub', billing['rzpSubscriptionId']),
            _kv('LS sub', billing['lsSubscriptionId']),
          ]),
          if (payments.isNotEmpty) ...[
            const SizedBox(height: 12),
            const Text('Payment history',
                style: TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(height: 6),
            for (final p in payments.reversed.take(8))
              Text(
                '${_shortDate(p['createdAt'])} · ${p['provider']} · '
                '${_fmtPayAmount(p)} · ${p['planId']} · ${p['status']}',
                style: const TextStyle(fontSize: 11),
              ),
          ],
          _detailSection('Quota usage (24h rolling)', [
            _kv('Discover searches', '${usage24h['discover'] ?? 0}'),
            _kv('LinkedIn searches', '${usage24h['linkedin'] ?? 0}'),
            _kv('AI autofill', '${usage24h['autofill'] ?? 0}'),
            _kv('Resume tailor', '${usage24h['tailor'] ?? 0}'),
            _kv('Resume uploads', '${usage24h['resume_upload'] ?? 0}'),
          ]),
          _detailSection('Search activity (${widget.days}d)', [
            _kv('Discover calls', '${totals['calls'] ?? 0}'),
            _kv('Jobs scraped', '${totals['scraped'] ?? 0}'),
            _kv('Jobs matched', '${totals['matched'] ?? 0}'),
            _kv('Errors', '${totals['errors'] ?? 0}'),
          ]),
          if (runs.isNotEmpty) ...[
            const SizedBox(height: 12),
            const Text('Recent discover runs',
                style: TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(height: 6),
            for (final r in runs.take(5))
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Text(
                  '${_shortDate(r['timestamp'])} · ${r['runType']} · '
                  'queries: ${((r['queries'] as List?) ?? []).join(", ")} · '
                  'locs: ${((r['locations'] as List?) ?? []).join(", ")}',
                  style: const TextStyle(fontSize: 11),
                ),
              ),
          ],
        ],
      ),
    );
  }

  Widget _detailSection(String title, List<Widget> rows) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14)),
          const SizedBox(height: 6),
          ...rows,
        ],
      ),
    );
  }

  Widget _kv(String k, dynamic v) {
    final s = v?.toString().trim() ?? '';
    if (s.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 130,
            child: Text(k, style: TextStyle(fontSize: 12, color: Colors.grey.shade700)),
          ),
          Expanded(
            child: Text(s, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w500)),
          ),
        ],
      ),
    );
  }

  String _fmtPayAmount(Map<String, dynamic> p) {
    final inr = p['priceInr'];
    final usd = p['priceUsd'];
    if (inr != null && (inr as num) > 0) return '₹$inr';
    if (usd != null && (usd as num) > 0) return '\$$usd';
    return '—';
  }
}

// ── Usage Tab ─────────────────────────────────────────────────────────────
class _UsageTab extends StatelessWidget {
  final Map<String, dynamic>? usage;
  const _UsageTab({required this.usage});

  @override
  Widget build(BuildContext context) {
    if (usage == null) return const Center(child: Text('No data'));
    final series = (usage!['series'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    final topCompanies = (usage!['topCompanies'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    final topUsers = (usage!['topUsers'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _ChartCard(
            title: 'Daily discover funnel',
            child: SizedBox(
              height: 300,
              child: _LineChart(
                series: series,
                keys: const ['scraped', 'filtered', 'matched'],
                colors: const [Colors.blueGrey, Colors.orange, Colors.green],
              ),
            ),
          ),
          const SizedBox(height: 20),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: _ChartCard(
                  title: 'Top companies (by calls)',
                  child: SingleChildScrollView(
                    child: DataTable(columns: const [
                      DataColumn(label: Text('Company')),
                      DataColumn(label: Text('Calls'), numeric: true),
                      DataColumn(label: Text('Matched'), numeric: true),
                    ], rows: [
                      for (final c in topCompanies)
                        DataRow(cells: [
                          DataCell(Text(c['companyId'] ?? '—')),
                          DataCell(Text('${c['calls'] ?? 0}')),
                          DataCell(Text('${c['totalMatched'] ?? 0}')),
                        ]),
                    ]),
                  ),
                ),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: _ChartCard(
                  title: 'Top users (by calls)',
                  child: SingleChildScrollView(
                    child: DataTable(columns: const [
                      DataColumn(label: Text('User')),
                      DataColumn(label: Text('Calls'), numeric: true),
                    ], rows: [
                      for (final u in topUsers)
                        DataRow(cells: [
                          DataCell(Text(u['userId'] ?? '—',
                              overflow: TextOverflow.ellipsis)),
                          DataCell(Text('${u['calls'] ?? 0}')),
                        ]),
                    ]),
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

// ── Costs Tab ─────────────────────────────────────────────────────────────
class _CostsTab extends StatelessWidget {
  final Map<String, dynamic>? costs;
  const _CostsTab({required this.costs});

  @override
  Widget build(BuildContext context) {
    if (costs == null) return const Center(child: Text('No data'));
    final perDay = (costs!['perDay'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    final perService = (costs!['perService'] as Map<String, dynamic>?) ?? {};
    final topUsers = (costs!['topUsers'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    final total = perDay.fold<double>(
        0, (s, e) => s + ((e['total'] as num?)?.toDouble() ?? 0));

    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: Colors.amber.shade50,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: Colors.amber.shade200),
            ),
            child: const Text(
              'These are estimates derived from telemetry × configured token prices. '
              'They are NOT actual Azure billing — use the Azure Portal Cost Analysis for that. '
              'Use this to spot abusive users and runaway spend trends.',
              style: TextStyle(fontSize: 13),
            ),
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              _StatCard(
                  label: 'Total (window)',
                  value: '\$${total.toStringAsFixed(2)}',
                  icon: Icons.attach_money,
                  color: Colors.green),
              const SizedBox(width: 16),
              _StatCard(
                  label: 'LLM rerank',
                  value: '\$${((perService['rerank'] as num?)?.toStringAsFixed(2)) ?? '0.00'}',
                  icon: Icons.psychology,
                  color: Colors.purple),
              const SizedBox(width: 16),
              _StatCard(
                  label: 'Embeddings',
                  value: '\$${((perService['embed'] as num?)?.toStringAsFixed(2)) ?? '0.00'}',
                  icon: Icons.scatter_plot,
                  color: Colors.teal),
            ],
          ),
          const SizedBox(height: 20),
          _ChartCard(
            title: 'Daily spend (USD)',
            child: SizedBox(
              height: 280,
              child: _LineChart(
                series: perDay,
                keys: const ['rerank', 'embed', 'total'],
                colors: const [Colors.purple, Colors.teal, Colors.green],
              ),
            ),
          ),
          const SizedBox(height: 20),
          _ChartCard(
            title: 'Top spenders',
            child: DataTable(columns: const [
              DataColumn(label: Text('User')),
              DataColumn(label: Text('Est. spend (USD)'), numeric: true),
            ], rows: [
              for (final u in topUsers)
                DataRow(cells: [
                  DataCell(Text(u['userId'] ?? '—',
                      overflow: TextOverflow.ellipsis)),
                  DataCell(Text(
                      '\$${((u['estUsd'] as num?)?.toStringAsFixed(4)) ?? '0'}')),
                ]),
            ]),
          ),
          const SizedBox(height: 20),
          _ChartCard(
            title: 'Daily cost breakdown',
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: DataTable(
                columnSpacing: 16,
                columns: const [
                  DataColumn(label: Text('Date', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700))),
                  DataColumn(label: Text('Discovers', style: TextStyle(fontSize: 11)), numeric: true),
                  DataColumn(label: Text('Rerank \$', style: TextStyle(fontSize: 11)), numeric: true),
                  DataColumn(label: Text('Embed \$', style: TextStyle(fontSize: 11)), numeric: true),
                  DataColumn(label: Text('Total \$', style: TextStyle(fontSize: 11)), numeric: true),
                ],
                rows: [
                  for (final d in perDay.reversed)
                    DataRow(cells: [
                      DataCell(Text(d['day']?.toString() ?? '', style: const TextStyle(fontSize: 11))),
                      DataCell(Text('${d['discovers'] ?? 0}', style: const TextStyle(fontSize: 11))),
                      DataCell(Text('\$${((d['rerank'] as num?)?.toStringAsFixed(4)) ?? '0'}', style: const TextStyle(fontSize: 11))),
                      DataCell(Text('\$${((d['embed'] as num?)?.toStringAsFixed(4)) ?? '0'}', style: const TextStyle(fontSize: 11))),
                      DataCell(Text('\$${((d['total'] as num?)?.toStringAsFixed(4)) ?? '0'}', style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w600))),
                    ]),
                ],
              ),
            ),
          ),
          const SizedBox(height: 20),
          _ChartCard(
            title: 'Pricing assumptions',
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  for (final entry in (costs!['pricing'] as Map<String, dynamic>? ?? {}).entries)
                    Text('${entry.key}: ${entry.value}', style: const TextStyle(fontSize: 12, fontFamily: 'monospace')),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ── Funnel Tab ────────────────────────────────────────────────────────────
class _FunnelTab extends StatelessWidget {
  final Map<String, dynamic>? funnel;
  const _FunnelTab({required this.funnel});

  @override
  Widget build(BuildContext context) {
    if (funnel == null) return const Center(child: Text('No data'));
    final totals = (funnel!['totals'] as Map<String, dynamic>?) ?? {};
    final perCo = (funnel!['perCompany'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _ChartCard(
            title: 'Pipeline totals',
            child: Padding(
              padding: const EdgeInsets.all(8),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceAround,
                children: [
                  _funnelStep('Attempts', totals['attempts']),
                  const Icon(Icons.arrow_forward, color: Colors.grey),
                  _funnelStep('Scraped', totals['scraped']),
                  const Icon(Icons.arrow_forward, color: Colors.grey),
                  _funnelStep('Filtered', totals['filtered']),
                  const Icon(Icons.arrow_forward, color: Colors.grey),
                  _funnelStep('Surfaced', totals['matched']),
                ],
              ),
            ),
          ),
          const SizedBox(height: 20),
          _ChartCard(
            title: 'Per-company funnel',
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: DataTable(columns: const [
                DataColumn(label: Text('Company')),
                DataColumn(label: Text('Attempts'), numeric: true),
                DataColumn(label: Text('Scraped'), numeric: true),
                DataColumn(label: Text('Filtered'), numeric: true),
                DataColumn(label: Text('Matched'), numeric: true),
                DataColumn(label: Text('Zero scrape'), numeric: true),
                DataColumn(label: Text('Filter killed'), numeric: true),
                DataColumn(label: Text('Rerank killed'), numeric: true),
                DataColumn(label: Text('OK'), numeric: true),
                DataColumn(label: Text('Errors'), numeric: true),
                DataColumn(label: Text('Avg ms'), numeric: true),
                DataColumn(label: Text('Success'), numeric: true),
              ], rows: [
                for (final r in perCo)
                  DataRow(cells: [
                    DataCell(Text(r['companyId'] ?? '—')),
                    DataCell(Text('${r['attempts'] ?? 0}')),
                    DataCell(Text('${r['totalScraped'] ?? 0}')),
                    DataCell(Text('${r['totalFiltered'] ?? 0}')),
                    DataCell(Text('${r['totalMatched'] ?? 0}')),
                    DataCell(Text('${r['zeroScraped'] ?? 0}')),
                    DataCell(Text('${r['filterKilled'] ?? 0}')),
                    DataCell(Text('${r['rerankKilled'] ?? 0}')),
                    DataCell(Text('${r['withResults'] ?? 0}',
                        style: const TextStyle(color: Colors.green, fontWeight: FontWeight.bold))),
                    DataCell(_errorCell(r['errors'] ?? 0)),
                    DataCell(Text('${r['avgDurationMs'] ?? 0}')),
                    DataCell(Text('${((r['successRate'] as num?) ?? 0) * 100 ~/ 1}%')),
                  ]),
              ]),
            ),
          ),
        ],
      ),
    );
  }

  Widget _funnelStep(String label, dynamic value) => Column(
        children: [
          Text('$value',
              style: const TextStyle(fontSize: 28, fontWeight: FontWeight.bold)),
          Text(label, style: const TextStyle(color: Colors.black54)),
        ],
      );
}

// ── Errors Tab ────────────────────────────────────────────────────────────
class _ErrorsTab extends StatelessWidget {
  final Map<String, dynamic>? errors;
  const _ErrorsTab({required this.errors});

  @override
  Widget build(BuildContext context) {
    if (errors == null) return const Center(child: Text('No data'));
    final list = (errors!['errors'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    final byKind = (errors!['errorsByKind'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    final broken = (errors!['brokenScrapers'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: _ChartCard(
                  title: 'Error types',
                  child: byKind.isEmpty
                      ? const Padding(padding: EdgeInsets.all(16), child: Text('No errors recorded.'))
                      : DataTable(columns: const [
                          DataColumn(label: Text('Kind')),
                          DataColumn(label: Text('Count'), numeric: true),
                        ], rows: [
                          for (final k in byKind)
                            DataRow(cells: [
                              DataCell(Text(k['kind'] ?? '—')),
                              DataCell(Text('${k['count'] ?? 0}')),
                            ]),
                        ]),
                ),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: _ChartCard(
                  title: 'Likely broken scrapers (≥80% zero-scrape)',
                  child: broken.isEmpty
                      ? const Padding(padding: EdgeInsets.all(16), child: Text('All scrapers healthy.'))
                      : DataTable(columns: const [
                          DataColumn(label: Text('Company')),
                          DataColumn(label: Text('Attempts'), numeric: true),
                          DataColumn(label: Text('Zero scrapes'), numeric: true),
                          DataColumn(label: Text('Zero rate'), numeric: true),
                        ], rows: [
                          for (final b in broken)
                            DataRow(cells: [
                              DataCell(Text(b['companyId'] ?? '—')),
                              DataCell(Text('${b['attempts'] ?? 0}')),
                              DataCell(Text('${b['zeroScrapes'] ?? 0}')),
                              DataCell(Text(
                                  '${(((b['zeroRate'] as num?) ?? 0) * 100).toStringAsFixed(0)}%',
                                  style: const TextStyle(
                                      color: Colors.red,
                                      fontWeight: FontWeight.bold))),
                            ]),
                        ]),
                ),
              ),
            ],
          ),
          const SizedBox(height: 20),
          _ChartCard(
            title: 'Recent errors (last 200)',
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: DataTable(columns: const [
                DataColumn(label: Text('Timestamp')),
                DataColumn(label: Text('User')),
                DataColumn(label: Text('Company')),
                DataColumn(label: Text('Kind')),
                DataColumn(label: Text('Duration ms'), numeric: true),
              ], rows: [
                for (final e in list)
                  DataRow(cells: [
                    DataCell(Text(_shortDate(e['timestamp']))),
                    DataCell(Text(e['userId'] ?? '—',
                        overflow: TextOverflow.ellipsis)),
                    DataCell(Text(e['companyId'] ?? '—')),
                    DataCell(Text(e['kind'] ?? '—',
                        style: const TextStyle(color: Colors.red))),
                    DataCell(Text('${e['durationMs'] ?? 0}')),
                  ]),
              ]),
            ),
          ),
        ],
      ),
    );
  }
}

// ── Reusable widgets ──────────────────────────────────────────────────────
class _StatCard extends StatelessWidget {
  final String label;
  final String value;
  final IconData icon;
  final Color color;
  const _StatCard({
    required this.label,
    required this.value,
    required this.icon,
    required this.color,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 220,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        boxShadow: const [
          BoxShadow(color: Color(0x11000000), blurRadius: 8, offset: Offset(0, 2)),
        ],
      ),
      child: Row(
        children: [
          CircleAvatar(
            radius: 22,
            backgroundColor: color.withValues(alpha: 0.12),
            child: Icon(icon, color: color),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(value,
                    style: const TextStyle(
                        fontSize: 22, fontWeight: FontWeight.bold)),
                Text(label,
                    style: const TextStyle(color: Colors.black54, fontSize: 13),
                    overflow: TextOverflow.ellipsis),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _ChartCard extends StatelessWidget {
  final String title;
  final Widget child;
  const _ChartCard({required this.title, required this.child});
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(12),
          boxShadow: const [
            BoxShadow(color: Color(0x11000000), blurRadius: 8, offset: Offset(0, 2)),
          ],
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title,
                style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
            const SizedBox(height: 12),
            child,
          ],
        ),
      );
}

class _LineChart extends StatelessWidget {
  final List<Map<String, dynamic>> series;
  final List<String> keys;
  final List<Color> colors;
  const _LineChart({required this.series, required this.keys, required this.colors});

  @override
  Widget build(BuildContext context) {
    if (series.isEmpty) {
      return const Center(child: Text('No data for window.'));
    }
    final lines = <LineChartBarData>[];
    double maxY = 1;
    for (var ki = 0; ki < keys.length; ki++) {
      final spots = <FlSpot>[];
      for (var i = 0; i < series.length; i++) {
        final v = (series[i][keys[ki]] as num?)?.toDouble() ?? 0;
        if (v > maxY) maxY = v;
        spots.add(FlSpot(i.toDouble(), v));
      }
      lines.add(LineChartBarData(
        spots: spots,
        color: colors[ki % colors.length],
        isCurved: true,
        barWidth: 2.5,
        dotData: const FlDotData(show: false),
      ));
    }
    return Column(
      children: [
        Wrap(
          spacing: 16,
          children: [
            for (var ki = 0; ki < keys.length; ki++)
              Row(mainAxisSize: MainAxisSize.min, children: [
                Container(width: 12, height: 12, color: colors[ki % colors.length]),
                const SizedBox(width: 6),
                Text(keys[ki]),
              ]),
          ],
        ),
        const SizedBox(height: 8),
        Expanded(
          child: LineChart(LineChartData(
            minY: 0,
            maxY: maxY * 1.15,
            gridData: const FlGridData(show: true),
            titlesData: FlTitlesData(
              leftTitles: const AxisTitles(
                  sideTitles: SideTitles(showTitles: true, reservedSize: 40)),
              bottomTitles: AxisTitles(
                sideTitles: SideTitles(
                  showTitles: true,
                  reservedSize: 32,
                  interval: (series.length / 6).ceilToDouble().clamp(1, 999),
                  getTitlesWidget: (value, meta) {
                    final i = value.toInt();
                    if (i < 0 || i >= series.length) return const SizedBox.shrink();
                    final day = (series[i]['day'] ?? '').toString();
                    return Padding(
                      padding: const EdgeInsets.only(top: 6),
                      child: Text(
                        day.length >= 10 ? day.substring(5) : day,
                        style: const TextStyle(fontSize: 10),
                      ),
                    );
                  },
                ),
              ),
              topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
              rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
            ),
            borderData: FlBorderData(show: false),
            lineBarsData: lines,
          )),
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
  Widget build(BuildContext context) => Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.error_outline, size: 64, color: Colors.red),
            const SizedBox(height: 12),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 32),
              child: Text(message, textAlign: TextAlign.center),
            ),
            const SizedBox(height: 12),
            FilledButton(onPressed: onRetry, child: const Text('Retry')),
          ],
        ),
      );
}

Widget _tierChip(String tier) {
  Color c;
  switch (tier) {
    case 'admin':
      c = Colors.deepPurple;
      break;
    case 'pro':
    case 'lifetime':
    case 'career_plus':
      c = Colors.green;
      break;
    default:
      c = Colors.grey;
  }
  return Container(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
    decoration: BoxDecoration(
        color: c.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(12)),
    child: Text(tier, style: TextStyle(color: c, fontSize: 11, fontWeight: FontWeight.w600)),
  );
}

Widget _errorCell(int count) {
  if (count == 0) return const Text('0');
  return Text('$count',
      style: const TextStyle(color: Colors.red, fontWeight: FontWeight.bold));
}

String _shortDate(dynamic ts) {
  if (ts == null) return '—';
  final s = ts.toString();
  if (s.length >= 16) return s.substring(0, 16).replaceFirst('T', ' ');
  return s;
}

// ── Runs Tab ──────────────────────────────────────────────────────────────
class _RunsTab extends StatefulWidget {
  final Map<String, dynamic>? runs;
  const _RunsTab({required this.runs});

  @override
  State<_RunsTab> createState() => _RunsTabState();
}

class _RunsTabState extends State<_RunsTab> {
  String? _expandedRunId;

  @override
  Widget build(BuildContext context) {
    final data = widget.runs;
    if (data == null) return const Center(child: Text('No data'));
    final runsList = (data['runs'] as List?) ?? [];
    if (runsList.isEmpty) {
      return const Center(child: Text('No discover runs recorded yet.\nRun a search and come back.'));
    }
    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('${runsList.length} runs in last ${data['windowDays'] ?? 7} days',
              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
          const SizedBox(height: 12),
          for (final run in runsList)
            _buildRunCard(Map<String, dynamic>.from(run as Map)),
        ],
      ),
    );
  }

  Widget _buildRunCard(Map<String, dynamic> run) {
    final runId = run['runId']?.toString() ?? '';
    final isExpanded = _expandedRunId == runId;
    final runType = run['runType']?.toString() ?? '';
    final email = run['email']?.toString() ?? run['userId']?.toString() ?? '';
    final ts = _shortDate(run['timestamp']);
    final scraped = run['totalScraped'] as int? ?? 0;
    final displayed = run['totalDisplayed'] as int? ?? 0;
    final matched = run['totalMatched'] as int? ?? 0;
    final keepPct = run['keepPct'] as num? ?? 0;
    final durationMs = run['durationMs'] as int? ?? 0;
    final companiesReq = run['companiesRequested'] as int? ?? 0;
    final companiesOk = run['companiesWithResults'] as int? ?? 0;
    final queries = (run['queries'] as List?)?.join(', ') ?? '';
    final locations = (run['locations'] as List?)?.join(', ') ?? '';
    final liPool = run['linkedInPoolSize'] as int? ?? 0;
    final perCompany = (run['perCompany'] as List?) ?? [];

    Color typeColor;
    switch (runType) {
      case 'bulk':
        typeColor = Colors.blue;
        break;
      case 'linkedin':
        typeColor = const Color(0xFF0A66C2);
        break;
      default:
        typeColor = Colors.teal;
    }

    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: Column(
        children: [
          InkWell(
            onTap: () => setState(() => _expandedRunId = isExpanded ? null : runId),
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                        decoration: BoxDecoration(
                          color: typeColor.withValues(alpha: 0.12),
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: Text(runType.toUpperCase(),
                            style: TextStyle(color: typeColor, fontSize: 11, fontWeight: FontWeight.w700)),
                      ),
                      const SizedBox(width: 8),
                      Text(email, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
                      const Spacer(),
                      Text(ts, style: const TextStyle(fontSize: 11, color: Colors.grey)),
                      const SizedBox(width: 6),
                      Icon(isExpanded ? Icons.expand_less : Icons.expand_more, size: 18),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Wrap(
                    spacing: 16,
                    runSpacing: 4,
                    children: [
                      _runStat('Scraped', '$scraped'),
                      _runStat('Matched', '$matched'),
                      _runStat('Displayed', '$displayed'),
                      _runStat('Keep%', '${keepPct.toStringAsFixed(1)}%'),
                      _runStat('Companies', '$companiesOk/$companiesReq'),
                      _runStat('Duration', '${(durationMs / 1000).toStringAsFixed(1)}s'),
                      if (liPool > 0) _runStat('LI Pool', '$liPool'),
                    ],
                  ),
                  if (queries.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Text('Queries: $queries', style: const TextStyle(fontSize: 11, color: Colors.grey)),
                    ),
                  if (locations.isNotEmpty)
                    Text('Locations: $locations', style: const TextStyle(fontSize: 11, color: Colors.grey)),
                ],
              ),
            ),
          ),
          if (isExpanded && perCompany.isNotEmpty) ...[
            const Divider(height: 1),
            Padding(
              padding: const EdgeInsets.all(8),
              child: SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: DataTable(
                  columnSpacing: 14,
                  headingRowHeight: 32,
                  dataRowMinHeight: 28,
                  dataRowMaxHeight: 34,
                  columns: const [
                    DataColumn(label: Text('Company', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700))),
                    DataColumn(label: Text('Scraped', style: TextStyle(fontSize: 11)), numeric: true),
                    DataColumn(label: Text('LocFilter', style: TextStyle(fontSize: 11)), numeric: true),
                    DataColumn(label: Text('Matched', style: TextStyle(fontSize: 11)), numeric: true),
                    DataColumn(label: Text('Vector', style: TextStyle(fontSize: 11)), numeric: true),
                    DataColumn(label: Text('Reranked', style: TextStyle(fontSize: 11)), numeric: true),
                    DataColumn(label: Text('Displayed', style: TextStyle(fontSize: 11)), numeric: true),
                    DataColumn(label: Text('Note', style: TextStyle(fontSize: 11))),
                  ],
                  rows: perCompany.map<DataRow>((c) {
                    final co = Map<String, dynamic>.from(c as Map);
                    final note = co['error']?.toString() ?? co['noResultsReason']?.toString() ?? '';
                    return DataRow(cells: [
                      DataCell(Text(co['company']?.toString() ?? '', style: const TextStyle(fontSize: 11))),
                      DataCell(Text('${co['scraped'] ?? 0}', style: const TextStyle(fontSize: 11))),
                      DataCell(Text('${co['locFiltered'] ?? '-'}', style: const TextStyle(fontSize: 11))),
                      DataCell(Text('${co['matched'] ?? 0}', style: const TextStyle(fontSize: 11))),
                      DataCell(Text('${co['vectorScored'] ?? '-'}', style: const TextStyle(fontSize: 11))),
                      DataCell(Text('${co['reranked'] ?? '-'}', style: const TextStyle(fontSize: 11))),
                      DataCell(Text('${co['displayed'] ?? 0}', style: const TextStyle(fontSize: 11))),
                      DataCell(SizedBox(
                        width: 150,
                        child: Text(note, style: const TextStyle(fontSize: 10, color: Colors.grey), overflow: TextOverflow.ellipsis),
                      )),
                    ]);
                  }).toList(),
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _runStat(String label, String value) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(label, style: const TextStyle(fontSize: 10, color: Colors.grey)),
        Text(value, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700)),
      ],
    );
  }
}

// ── Feedback Tab ──────────────────────────────────────────────────────────
class _FeedbackTab extends StatelessWidget {
  final Map<String, dynamic>? feedback;
  const _FeedbackTab({required this.feedback});

  @override
  Widget build(BuildContext context) {
    final data = feedback;
    if (data == null) return const Center(child: Text('No data'));
    final items = (data['feedback'] as List?) ?? [];
    if (items.isEmpty) {
      return const Center(child: Text('No feedback yet.\nUsers can submit from the Discover page.'));
    }
    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('${items.length} feedback items in last ${data['windowDays'] ?? 90} days',
              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
          const SizedBox(height: 12),
          for (final item in items)
            _buildFeedbackCard(Map<String, dynamic>.from(item as Map)),
        ],
      ),
    );
  }

  Widget _buildFeedbackCard(Map<String, dynamic> item) {
    final category = item['category']?.toString() ?? 'feedback';
    final email = item['email']?.toString() ?? item['userId']?.toString() ?? '';
    final text = item['text']?.toString() ?? '';
    final ts = _shortDate(item['timestamp']);
    final page = item['page']?.toString() ?? '';
    final status = item['status']?.toString() ?? 'new';

    Color catColor;
    IconData catIcon;
    switch (category) {
      case 'bug':
        catColor = Colors.red;
        catIcon = Icons.bug_report;
        break;
      case 'feature':
        catColor = Colors.purple;
        catIcon = Icons.lightbulb_outline;
        break;
      default:
        catColor = Colors.blue;
        catIcon = Icons.feedback_outlined;
    }

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(catIcon, size: 16, color: catColor),
                const SizedBox(width: 6),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                  decoration: BoxDecoration(
                    color: catColor.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Text(category.toUpperCase(),
                      style: TextStyle(color: catColor, fontSize: 10, fontWeight: FontWeight.w700)),
                ),
                const SizedBox(width: 8),
                Text(email, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
                const Spacer(),
                Text(ts, style: const TextStyle(fontSize: 11, color: Colors.grey)),
              ],
            ),
            const SizedBox(height: 8),
            Text(text, style: const TextStyle(fontSize: 13)),
            if (page.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text('Page: $page', style: const TextStyle(fontSize: 10, color: Colors.grey)),
              ),
          ],
        ),
      ),
    );
  }
}
