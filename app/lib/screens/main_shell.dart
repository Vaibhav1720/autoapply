import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import 'package:auto_apply/config/theme.dart';
import 'package:auto_apply/providers/auth_provider.dart';
// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;

/// Main shell — top navigation bar with brand on the left, nav pills on the
/// right. Web-first layout. On narrow widths (<720px) collapses to a
/// hamburger drawer so it stays usable on mobile browsers.
class MainShell extends StatelessWidget {
  final StatefulNavigationShell navigationShell;
  const MainShell({super.key, required this.navigationShell});

  static const _items = <_NavItem>[
    _NavItem(label: 'Discover', icon: Icons.auto_awesome_outlined,
        activeIcon: Icons.auto_awesome),
    _NavItem(label: 'Companies', icon: Icons.apartment_outlined,
        activeIcon: Icons.apartment),
    _NavItem(label: 'Profile', icon: Icons.person_outline_rounded,
        activeIcon: Icons.person_rounded),
  ];

  void _go(int i) => navigationShell.goBranch(
        i,
        initialLocation: i == navigationShell.currentIndex,
      );

  void _logout(BuildContext context) {
    html.window.localStorage.remove('auth_token');
    if (context.mounted) context.go('/login');
  }

  @override
  Widget build(BuildContext context) {
    final width = MediaQuery.of(context).size.width;
    final isCompact = width < 720;
    final current = navigationShell.currentIndex;

    return Scaffold(
      drawer: isCompact ? _buildDrawer(context, current) : null,
      body: Container(
        decoration: const BoxDecoration(gradient: AppTheme.backgroundGradient),
        child: Column(
          children: [
            _TopBar(
              currentIndex: current,
              items: _items,
              isCompact: isCompact,
              onSelect: _go,
              onLogout: () => _logout(context),
            ),
            Expanded(child: navigationShell),
          ],
        ),
      ),
    );
  }

  Drawer _buildDrawer(BuildContext context, int current) {
    return Drawer(
      backgroundColor: AppTheme.surface,
      child: SafeArea(
        child: Column(
          children: [
            const Padding(
              padding: EdgeInsets.fromLTRB(20, 20, 20, 12),
              child: BrandMark(showWordmark: true),
            ),
            const Divider(height: 1),
            for (var i = 0; i < _items.length; i++)
              ListTile(
                leading: Icon(
                  i == current ? _items[i].activeIcon : _items[i].icon,
                  color: i == current ? AppTheme.primary : AppTheme.textSecondary,
                ),
                title: Text(_items[i].label,
                    style: TextStyle(
                      fontWeight: i == current ? FontWeight.w700 : FontWeight.w500,
                      color: i == current ? AppTheme.primary : AppTheme.textPrimary,
                    )),
                selected: i == current,
                selectedTileColor: AppTheme.primarySoft,
                onTap: () {
                  Navigator.of(context).pop();
                  _go(i);
                },
              ),
            const Spacer(),
            ListTile(
              leading: const Icon(Icons.logout_rounded,
                  color: AppTheme.textSecondary),
              title: const Text('Sign out'),
              onTap: () => _logout(context),
            ),
          ],
        ),
      ),
    );
  }
}

class _NavItem {
  final String label;
  final IconData icon;
  final IconData activeIcon;
  const _NavItem({
    required this.label,
    required this.icon,
    required this.activeIcon,
  });
}

class _TopBar extends StatelessWidget {
  final int currentIndex;
  final List<_NavItem> items;
  final bool isCompact;
  final ValueChanged<int> onSelect;
  final VoidCallback onLogout;

  const _TopBar({
    required this.currentIndex,
    required this.items,
    required this.isCompact,
    required this.onSelect,
    required this.onLogout,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: AppTheme.surface.withOpacity(0.92),
        border: const Border(
          bottom: BorderSide(color: AppTheme.border, width: 1),
        ),
        boxShadow: [
          BoxShadow(
            color: AppTheme.primary.withOpacity(0.04),
            blurRadius: 20,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: SafeArea(
        bottom: false,
        child: SizedBox(
          height: 64,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 20),
            child: Row(
              children: [
                if (isCompact)
                  Builder(
                    builder: (ctx) => IconButton(
                      icon: const Icon(Icons.menu_rounded),
                      color: AppTheme.textPrimary,
                      onPressed: () => Scaffold.of(ctx).openDrawer(),
                    ),
                  ),
                const SizedBox(width: 4),
                const BrandMark(showWordmark: true),
                const Spacer(),
                if (!isCompact) ...[
                  for (var i = 0; i < items.length; i++)
                    Padding(
                      padding: const EdgeInsets.only(left: 6),
                      child: _NavPill(
                        item: items[i],
                        selected: i == currentIndex,
                        onTap: () => onSelect(i),
                      ),
                    ),
                  const SizedBox(width: 16),
                  Container(width: 1, height: 28, color: AppTheme.border),
                  const SizedBox(width: 12),
                  Builder(builder: (ctx) {
                    final email = (context.watch<AuthProvider>().email ?? '').toLowerCase().trim();
                    // Admin allow-list. Configure via dart-define at build time:
                    //   flutter build web --dart-define=ADMIN_EMAILS=a@example.com,b@example.com
                    const adminRaw = String.fromEnvironment('ADMIN_EMAILS', defaultValue: 'vibhuu1720@gmail.com');
                    final adminEmails = adminRaw
                        .split(',')
                        .map((e) => e.trim().toLowerCase())
                        .where((e) => e.isNotEmpty)
                        .toSet();
                    if (!adminEmails.contains(email)) return const SizedBox.shrink();
                    return IconButton(
                      tooltip: 'Admin dashboard',
                      icon: const Icon(Icons.admin_panel_settings_outlined, size: 22),
                      color: AppTheme.textSecondary,
                      onPressed: () => ctx.go('/admin'),
                    );
                  }),
                  IconButton(
                    tooltip: 'Sign out',
                    icon: const Icon(Icons.logout_rounded, size: 20),
                    color: AppTheme.textSecondary,
                    onPressed: onLogout,
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _NavPill extends StatefulWidget {
  final _NavItem item;
  final bool selected;
  final VoidCallback onTap;
  const _NavPill({
    required this.item,
    required this.selected,
    required this.onTap,
  });

  @override
  State<_NavPill> createState() => _NavPillState();
}

class _NavPillState extends State<_NavPill> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    final selected = widget.selected;
    final bg = selected
        ? AppTheme.primarySoft
        : (_hover ? AppTheme.surfaceAlt : Colors.transparent);
    final fg = selected ? AppTheme.primary : AppTheme.textSecondary;

    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 160),
          curve: Curves.easeOut,
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 9),
          decoration: BoxDecoration(
            color: bg,
            borderRadius: AppTheme.pillRadius,
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(selected ? widget.item.activeIcon : widget.item.icon,
                  size: 18, color: fg),
              const SizedBox(width: 8),
              Text(
                widget.item.label,
                style: TextStyle(
                  fontSize: 13.5,
                  fontWeight: selected ? FontWeight.w700 : FontWeight.w600,
                  color: fg,
                  letterSpacing: 0.1,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// ApplyRight circular mark + optional wordmark (no banner on page body).
class BrandMark extends StatelessWidget {
  final bool showWordmark;
  const BrandMark({super.key, this.showWordmark = true});

  static const _markAsset = 'assets/images/applyright_mark.png';

  @override
  Widget build(BuildContext context) {
    final size = showWordmark ? 36.0 : 40.0;
    final mark = ClipOval(
      child: Image.asset(
        _markAsset,
        width: size,
        height: size,
        fit: BoxFit.cover,
        filterQuality: FilterQuality.high,
      ),
    );
    if (!showWordmark) return mark;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        mark,
        const SizedBox(width: 10),
        ShaderMask(
          shaderCallback: (r) => AppTheme.brandGradient.createShader(r),
          child: const Text(
            'ApplyRight',
            style: TextStyle(
              fontSize: 18,
              fontWeight: FontWeight.w800,
              letterSpacing: -0.4,
              color: Colors.white,
            ),
          ),
        ),
      ],
    );
  }
}
