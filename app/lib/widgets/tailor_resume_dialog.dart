import 'package:flutter/material.dart';
import 'package:auto_apply/config/theme.dart';
import 'package:auto_apply/data/search_taxonomy.dart';

/// User's industry + role choices before calling resume tailoring API.
class TailorResumeSelection {
  final String industry;
  final List<String> titles;

  const TailorResumeSelection({
    required this.industry,
    required this.titles,
  });
}

/// Industry picker + role focus dialog (shared by Discover and Profile).
Future<TailorResumeSelection?> showTailorResumeDialog(
  BuildContext context, {
  String? initialIndustryId,
  List<String> initialTitles = const [],
}) async {
  String selectedIndustry = kIndustries.any((i) => i.id == initialIndustryId)
      ? initialIndustryId!
      : 'tech';
  final selectedTitles = List<String>.from(initialTitles);
  final roleCtrl = TextEditingController();

  final picked = await showDialog<TailorResumeSelection>(
    context: context,
    useRootNavigator: true,
    barrierDismissible: true,
    builder: (ctx) {
      return StatefulBuilder(builder: (ctx, setLocal) {
        final rolesForIndustry = kRolesByIndustry[selectedIndustry] ?? const [];
        final suggestions = rolesForIndustry
            .where((r) => !selectedTitles.contains(r))
            .take(8)
            .toList();

        void addRole(String r) {
          final v = r.trim();
          if (v.isEmpty || selectedTitles.contains(v)) return;
          setLocal(() => selectedTitles.add(v));
        }

        return AlertDialog(
          title: const Text('Tailor my resume'),
          content: SizedBox(
            width: 520,
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text(
                    "We'll pull your best-matched jobs and rewrite "
                    "keywords, bullets, and your headline against the "
                    "industry and roles you pick here.",
                    style: TextStyle(fontSize: 13, color: AppTheme.textSecondary),
                  ),
                  const SizedBox(height: 14),
                  const Text(
                    'Industry',
                    style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: 6),
                  SizedBox(
                    height: 36,
                    child: ListView.separated(
                      scrollDirection: Axis.horizontal,
                      itemCount: kIndustries.length,
                      separatorBuilder: (_, __) => const SizedBox(width: 6),
                      itemBuilder: (_, i) {
                        final ind = kIndustries[i];
                        final selected = ind.id == selectedIndustry;
                        return InkWell(
                          borderRadius: BorderRadius.circular(18),
                          onTap: () => setLocal(() => selectedIndustry = ind.id),
                          child: Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 12, vertical: 7),
                            decoration: BoxDecoration(
                              color: selected
                                  ? AppTheme.primary
                                  : AppTheme.surface,
                              borderRadius: BorderRadius.circular(18),
                              border: Border.all(
                                color: selected
                                    ? AppTheme.primary
                                    : AppTheme.border,
                              ),
                            ),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Text(ind.emoji,
                                    style: const TextStyle(fontSize: 13)),
                                const SizedBox(width: 6),
                                Text(
                                  ind.label,
                                  style: TextStyle(
                                    fontSize: 12,
                                    fontWeight: FontWeight.w600,
                                    color: selected
                                        ? Colors.white
                                        : AppTheme.textPrimary,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        );
                      },
                    ),
                  ),
                  const SizedBox(height: 14),
                  Row(
                    children: [
                      const Text(
                        'Role focus',
                        style: TextStyle(
                            fontSize: 12, fontWeight: FontWeight.w700),
                      ),
                      const SizedBox(width: 6),
                      const Icon(Icons.info_outline,
                          size: 13, color: AppTheme.textSecondary),
                      const SizedBox(width: 4),
                      const Expanded(
                        child: Text(
                          'Tells the model which titles to prioritise '
                          'when picking keywords + bullets.',
                          style: TextStyle(
                              fontSize: 11, color: AppTheme.textSecondary),
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Wrap(
                    spacing: 6,
                    runSpacing: 6,
                    children: [
                      for (final t in selectedTitles)
                        InputChip(
                          label: Text(t, style: const TextStyle(fontSize: 12)),
                          onDeleted: () =>
                              setLocal(() => selectedTitles.remove(t)),
                        ),
                    ],
                  ),
                  const SizedBox(height: 6),
                  TextField(
                    controller: roleCtrl,
                    decoration: InputDecoration(
                      isDense: true,
                      hintText:
                          'Add a role (e.g. Data Engineer) and press Enter',
                      suffixIcon: IconButton(
                        icon: const Icon(Icons.add, size: 18),
                        onPressed: () {
                          addRole(roleCtrl.text);
                          roleCtrl.clear();
                        },
                      ),
                    ),
                    onSubmitted: (v) {
                      addRole(v);
                      roleCtrl.clear();
                    },
                  ),
                  if (suggestions.isNotEmpty) ...[
                    const SizedBox(height: 8),
                    const Text(
                      'Suggestions',
                      style: TextStyle(
                          fontSize: 11, color: AppTheme.textSecondary),
                    ),
                    const SizedBox(height: 4),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        for (final s in suggestions)
                          ActionChip(
                            label:
                                Text(s, style: const TextStyle(fontSize: 11)),
                            onPressed: () => addRole(s),
                          ),
                      ],
                    ),
                  ],
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Cancel'),
            ),
            ElevatedButton.icon(
              icon: const Icon(Icons.auto_awesome, size: 16),
              label: const Text('Tailor my resume'),
              onPressed: () => Navigator.pop(
                ctx,
                TailorResumeSelection(
                  industry: selectedIndustry,
                  titles: List<String>.from(selectedTitles),
                ),
              ),
            ),
          ],
        );
      });
    },
  );

  roleCtrl.dispose();
  return picked;
}
