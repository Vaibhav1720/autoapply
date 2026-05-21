import 'dart:async';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:go_router/go_router.dart';
import 'package:auto_apply/config/theme.dart';
import 'package:auto_apply/providers/profile_provider.dart';
import 'package:auto_apply/services/api_service.dart';

/// Companies tab — pick which employers AutoApply scans for matching roles.
///
/// Selections auto-save (debounced) so users never need to remember to press
/// a button. UI mirrors the Discover screen's hero + search aesthetic.
class CompaniesScreen extends StatefulWidget {
  const CompaniesScreen({super.key});

  @override
  State<CompaniesScreen> createState() => _CompaniesScreenState();
}

class _CompaniesScreenState extends State<CompaniesScreen> {
  List<Map<String, dynamic>> _companies = [];
  List<Map<String, dynamic>> _filtered = [];
  Set<String> _selected = {};
  bool _loading = true;
  bool _saving = false;
  String? _saveError;
  DateTime? _savedAt;
  Timer? _debounce;
  final _searchCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _searchCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final api = context.read<ApiService>();
    try {
      final compResp = await api.get('/api/v1/companies');
      final companies = (compResp.data['companies'] as List?)
              ?.map<Map<String, dynamic>>((c) => Map<String, dynamic>.from(c))
              .toList() ??
          [];

      Set<String> selectedIds = {};
      try {
        final selResp = await api.get('/api/v1/companies/selected');
        final sel = (selResp.data['selected'] as List?) ?? [];
        selectedIds = sel.map<String>((c) => c['id'].toString()).toSet();
      } catch (_) {}

      if (mounted) {
        // Ensure profile is loaded so we can check the tier
        final pp = context.read<ProfileProvider>();
        if (pp.profile == null) {
          try { await pp.loadProfile(); } catch (_) {}
        }
        final profile = pp.profile;
        final tier = ((profile?['subscription'] as Map<String, dynamic>?)?['tier']
            ?? profile?['tier'] ?? 'free').toString().toLowerCase();
        final isPremium = const {'premium','pro','lifetime','career_plus','admin'}.contains(tier);
        final maxAllowed = isPremium ? kMaxSelectedPremium : kMaxSelectedFree;

        if (selectedIds.length > maxAllowed) {
          // Trim to allowed limit and auto-save
          selectedIds = selectedIds.take(maxAllowed).toSet();
          api.put('/api/v1/companies/selected', data: {
            'companyIds': selectedIds.toList(),
          }).catchError((_) {});
        }

        setState(() {
          _companies = companies;
          _filtered = _applyFilter(companies, _searchCtrl.text);
          _selected = selectedIds;
          _loading = false;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _loading = false);
    }
  }

  List<Map<String, dynamic>> _applyFilter(List<Map<String, dynamic>> src, String q) {
    if (q.isEmpty) return src;
    final qq = q.toLowerCase();
    return src.where((c) {
      final name = (c['name'] ?? '').toString().toLowerCase();
      final desc = (c['description'] ?? '').toString().toLowerCase();
      final industry = (c['industry'] ?? '').toString().toLowerCase();
      return name.contains(qq) || desc.contains(qq) || industry.contains(qq);
    }).toList();
  }

  /// Push the current selection to the backend. Debounced from `_toggle()` so
  /// rapid toggles collapse into a single network call.
  Future<void> _saveNow() async {
    if (!mounted) return;
    setState(() {
      _saving = true;
      _saveError = null;
    });
    try {
      final api = context.read<ApiService>();
      await api.post('/api/v1/companies/select',
          data: {'companyIds': _selected.toList()});
      if (mounted) {
        setState(() {
          _saving = false;
          _savedAt = DateTime.now();
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _saving = false;
          _saveError = 'Could not save selection. Tap to retry.';
        });
      }
    }
  }

  void _scheduleSave() {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 600), _saveNow);
  }

  /// Premium users: 200 companies. Free users: 5.
  static const int kMaxSelectedPremium = 200;
  static const int kMaxSelectedFree = 5;

  int get _maxSelected {
    final profile = context.read<ProfileProvider>().profile;
    final tier = ((profile?['subscription'] as Map<String, dynamic>?)?['tier']
        ?? profile?['tier'] ?? 'free').toString().toLowerCase();
    return const {'premium','pro','lifetime','career_plus','admin'}.contains(tier)
        ? kMaxSelectedPremium
        : kMaxSelectedFree;
  }

  bool get _isPremium => _maxSelected > kMaxSelectedFree;

  void _toggle(String id) {
    setState(() {
      if (_selected.contains(id)) {
        _selected.remove(id);
      } else {
        if (_selected.length >= _maxSelected) {
          if (!_isPremium) {
            _showCompanyLimitPopup();
          } else {
            _saveError = 'You can pick at most $_maxSelected companies. '
                'Deselect one first.';
          }
          return;
        }
        _selected.add(id);
      }
    });
    _scheduleSave();
  }

  void _toggleAllFiltered() {
    final filteredIds = _filtered.map((c) => c['id'] as String).toSet();
    setState(() {
      if (filteredIds.every((id) => _selected.contains(id))) {
        _selected.removeAll(filteredIds);
      } else {
        for (final id in filteredIds) {
          if (_selected.length >= _maxSelected) {
            if (!_isPremium) {
              _showCompanyLimitPopup();
            } else {
              _saveError = 'Stopped at $_maxSelected (the maximum). '
                  'Refine the search or deselect to add more.';
            }
            break;
          }
          _selected.add(id);
        }
      }
    });
    _scheduleSave();
  }

  void _showCompanyLimitPopup() {
    showDialog(
      context: context,
      builder: (ctx) => Dialog(
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
              Container(
                width: 56, height: 56,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: const LinearGradient(colors: [Color(0xFF6366f1), Color(0xFF8b5cf6)]),
                  boxShadow: [BoxShadow(color: const Color(0xFF6366f1).withValues(alpha: 0.3), blurRadius: 16)],
                ),
                child: const Icon(Icons.business_rounded, color: Colors.white, size: 28),
              ),
              const SizedBox(height: 16),
              Text(
                'You\u2019ve selected $kMaxSelectedFree companies',
                style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: Color(0xFF1e1b4b)),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 8),
              const Text(
                'Free accounts can track up to 5 companies.\n'
                'Upgrade to Premium to track unlimited companies\nand never miss a job opening.',
                style: TextStyle(fontSize: 13, color: Color(0xFF4b5563), height: 1.5),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 12),
              Builder(builder: (ctx2) {
                final pp = context.read<ProfileProvider>();
                final country = ((pp.profile?['applicationDetails'] as Map?)?['country'] as String? ?? '').toUpperCase();
                final isIndia = country == 'IN' || country == 'INDIA' || country == 'IND';
                return Container(
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                  decoration: BoxDecoration(
                    color: const Color(0xFF6366f1).withValues(alpha: 0.08),
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: const Color(0xFF6366f1).withValues(alpha: 0.2)),
                  ),
                  child: Text(
                    isIndia
                        ? 'Just \u20b9199/month \u2014 less than \u20b97/day'
                        : 'From \$9.99/month \u2014 less than \$0.34/day',
                    style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w800, color: Color(0xFF6366f1)),
                  ),
                );
              }),
              const SizedBox(height: 8),
              const Text(
                'A small step that could help you land your dream job\nand change your career path forever.',
                style: TextStyle(fontSize: 12, color: Color(0xFF6b7280), height: 1.4),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 20),
              SizedBox(
                width: double.infinity, height: 46,
                child: FilledButton(
                  onPressed: () {
                    Navigator.pop(ctx);
                    context.push('/pricing');
                  },
                  style: FilledButton.styleFrom(
                    backgroundColor: const Color(0xFF6366f1),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                  ),
                  child: const Text('See Pro plans', style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700)),
                ),
              ),
              const SizedBox(height: 8),
              TextButton(
                onPressed: () => Navigator.pop(ctx),
                child: const Text('Maybe later', style: TextStyle(color: Color(0xFF9ca3af), fontSize: 13)),
              ),
            ],
          ),
        ),
      ),
    );
  }

  String _statusText() {
    if (_saving) return 'Saving…';
    if (_saveError != null) return _saveError!;
    if (_savedAt != null) return 'All changes saved';
    final max = _maxSelected;
    return _selected.isEmpty
        ? 'Pick the employers you want AutoApply to scan (up to $max)'
        : '${_selected.length} of $max selected';
  }

  IconData _statusIcon() {
    if (_saving) return Icons.sync_rounded;
    if (_saveError != null) return Icons.error_outline_rounded;
    if (_savedAt != null) return Icons.check_circle_rounded;
    return Icons.tips_and_updates_outlined;
  }

  Color _statusColor() {
    if (_saveError != null) return AppTheme.error;
    if (_savedAt != null && !_saving) return AppTheme.success;
    return AppTheme.textSecondary;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: SingleChildScrollView(
                physics: const AlwaysScrollableScrollPhysics(),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _CompaniesHero(
                      searchCtrl: _searchCtrl,
                      onSearch: (q) => setState(() {
                        _filtered = _applyFilter(_companies, q);
                      }),
                      totalCount: _companies.length,
                      selectedCount: _selected.length,
                      statusText: _statusText(),
                      statusIcon: _statusIcon(),
                      statusColor: _statusColor(),
                      onSelectAll: _toggleAllFiltered,
                      allFilteredSelected: _filtered.isNotEmpty &&
                          _filtered.every(
                              (c) => _selected.contains(c['id'] as String)),
                      filteredCount: _filtered.length,
                    ),
                    Padding(
                      padding: const EdgeInsets.fromLTRB(24, 4, 24, 32),
                      child: _filtered.isEmpty
                          ? Padding(
                              padding: const EdgeInsets.symmetric(vertical: 48),
                              child: Center(
                                child: Text(
                                  'No companies match "${_searchCtrl.text}".',
                                  style: const TextStyle(
                                      color: AppTheme.textSecondary),
                                ),
                              ),
                            )
                          : LayoutBuilder(builder: (ctx, cons) {
                              final cols = cons.maxWidth >= 1100
                                  ? 3
                                  : cons.maxWidth >= 720
                                      ? 2
                                      : 1;
                              return GridView.builder(
                                physics: const NeverScrollableScrollPhysics(),
                                shrinkWrap: true,
                                gridDelegate:
                                    SliverGridDelegateWithFixedCrossAxisCount(
                                  crossAxisCount: cols,
                                  mainAxisSpacing: 12,
                                  crossAxisSpacing: 12,
                                  mainAxisExtent: 110,
                                ),
                                itemCount: _filtered.length,
                                itemBuilder: (_, i) {
                                  final c = _filtered[i];
                                  final id = c['id'] as String;
                                  final isSelected = _selected.contains(id);
                                  return _CompanyCard(
                                    name: (c['name'] ?? '').toString(),
                                    industry: (c['industry'] ?? '').toString(),
                                    description:
                                        (c['description'] ?? '').toString(),
                                    selected: isSelected,
                                    onTap: () => _toggle(id),
                                  );
                                },
                              );
                            }),
                    ),
                  ],
                ),
              ),
            ),
    );
  }
}

class _CompaniesHero extends StatelessWidget {
  final TextEditingController searchCtrl;
  final ValueChanged<String> onSearch;
  final int totalCount;
  final int selectedCount;
  final int filteredCount;
  final String statusText;
  final IconData statusIcon;
  final Color statusColor;
  final VoidCallback onSelectAll;
  final bool allFilteredSelected;

  const _CompaniesHero({
    required this.searchCtrl,
    required this.onSearch,
    required this.totalCount,
    required this.selectedCount,
    required this.filteredCount,
    required this.statusText,
    required this.statusIcon,
    required this.statusColor,
    required this.onSelectAll,
    required this.allFilteredSelected,
  });

  @override
  Widget build(BuildContext context) {
    final isCompact = MediaQuery.of(context).size.width < 600;

    return Container(
      width: double.infinity,
      padding: EdgeInsets.fromLTRB(
          24, isCompact ? 18 : 28, 24, isCompact ? 16 : 22),
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
                    Icon(Icons.business_rounded,
                        size: 12, color: AppTheme.primary),
                    const SizedBox(width: 5),
                    Text(
                      '$totalCount  EMPLOYERS  \u00B7  AUTO-SAVED',
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
              ShaderMask(
                shaderCallback: (r) => AppTheme.brandGradient.createShader(r),
                child: Text(
                  'Pick where you want to work.',
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
              Text(
                'Tap a company to add or remove it. We save automatically.',
                textAlign: TextAlign.center,
                style: TextStyle(
                  fontSize: isCompact ? 13 : 14,
                  color: AppTheme.textSecondary,
                ),
              ),
              SizedBox(height: isCompact ? 14 : 18),

              // Search box — same border + soft shadow as the Discover hero.
              Container(
                decoration: BoxDecoration(
                  color: AppTheme.surface,
                  borderRadius: AppTheme.cardRadius,
                  border: Border.all(color: AppTheme.border),
                  boxShadow: AppTheme.softShadow,
                ),
                padding: const EdgeInsets.symmetric(horizontal: 14),
                child: TextField(
                  controller: searchCtrl,
                  onChanged: onSearch,
                  decoration: InputDecoration(
                    hintText: 'Search by name, industry, or keyword',
                    hintStyle: const TextStyle(color: AppTheme.textSecondary),
                    border: InputBorder.none,
                    icon: const Icon(Icons.search_rounded,
                        color: AppTheme.textSecondary),
                    isDense: true,
                    contentPadding:
                        const EdgeInsets.symmetric(vertical: 14),
                    suffixIcon: searchCtrl.text.isEmpty
                        ? null
                        : IconButton(
                            icon: const Icon(Icons.close, size: 18),
                            onPressed: () {
                              searchCtrl.clear();
                              onSearch('');
                            },
                          ),
                  ),
                ),
              ),

              const SizedBox(height: 12),

              // Status row + select-all action.
              Row(
                children: [
                  Icon(statusIcon, size: 16, color: statusColor),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      statusText,
                      style: TextStyle(
                          fontSize: 12.5,
                          fontWeight: FontWeight.w600,
                          color: statusColor),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  if (filteredCount > 0)
                    TextButton(
                      onPressed: onSelectAll,
                      style: TextButton.styleFrom(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 10, vertical: 4),
                        minimumSize: const Size(0, 30),
                        foregroundColor: AppTheme.primary,
                      ),
                      child: Text(
                        allFilteredSelected
                            ? 'Deselect all'
                            : 'Select all ($filteredCount)',
                        style: const TextStyle(
                            fontSize: 12.5, fontWeight: FontWeight.w700),
                      ),
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _CompanyCard extends StatelessWidget {
  final String name;
  final String industry;
  final String description;
  final bool selected;
  final VoidCallback onTap;

  const _CompanyCard({
    required this.name,
    required this.industry,
    required this.description,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final initial = name.isEmpty ? '?' : name[0].toUpperCase();
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: AppTheme.cardRadius,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          curve: Curves.easeOut,
          padding: const EdgeInsets.fromLTRB(14, 14, 14, 14),
          decoration: BoxDecoration(
            color: selected
                ? AppTheme.primarySoft.withValues(alpha: 0.55)
                : AppTheme.surface,
            borderRadius: AppTheme.cardRadius,
            border: Border.all(
              color: selected ? AppTheme.primary : AppTheme.border,
              width: selected ? 1.5 : 1,
            ),
            boxShadow: selected ? AppTheme.softShadow : null,
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 42,
                height: 42,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  gradient: selected
                      ? AppTheme.brandGradient
                      : LinearGradient(colors: [
                          AppTheme.surfaceAlt,
                          AppTheme.surfaceAlt,
                        ]),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(
                      color: selected
                          ? Colors.transparent
                          : AppTheme.border),
                ),
                child: Text(
                  initial,
                  style: TextStyle(
                    color: selected ? Colors.white : AppTheme.textPrimary,
                    fontWeight: FontWeight.w800,
                    fontSize: 18,
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            name,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontSize: 15,
                              fontWeight: FontWeight.w700,
                              color: AppTheme.textPrimary,
                              letterSpacing: -0.2,
                            ),
                          ),
                        ),
                        const SizedBox(width: 6),
                        Icon(
                          selected
                              ? Icons.check_circle_rounded
                              : Icons.add_circle_outline_rounded,
                          size: 20,
                          color: selected
                              ? AppTheme.primary
                              : AppTheme.textSecondary.withValues(alpha: 0.6),
                        ),
                      ],
                    ),
                    if (industry.isNotEmpty) ...[
                      const SizedBox(height: 2),
                      Text(
                        industry,
                        style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w700,
                            letterSpacing: 0.4,
                            color: AppTheme.primary.withValues(alpha: 0.85)),
                      ),
                    ],
                    if (description.isNotEmpty) ...[
                      const SizedBox(height: 4),
                      Text(
                        description,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 12,
                          color: AppTheme.textSecondary,
                          height: 1.3,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
