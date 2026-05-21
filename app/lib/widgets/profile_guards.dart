// Shared guards used by features that REQUIRE a complete profile (resume,
// at minimum). Each guard returns `true` if the action may proceed, or
// `false` after showing a blocking dialog that nudges the user back to
// the Profile tab to fix the missing piece.
//
// Keep these tiny and pure-UI. Business rules (what counts as "complete")
// live here so every entry-point in the app stays in sync.

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import 'package:auto_apply/config/theme.dart';
import 'package:auto_apply/providers/profile_provider.dart';

/// Returns `true` if the user has uploaded a resume.
/// If not, shows a blocking dialog explaining why the resume is needed and
/// offering a "Go to Profile" button that switches the active tab.
///
/// The dialog is intentionally non-dismissible by tapping outside — the
/// action cannot proceed without a resume, so silently dismissing would
/// just leave the user wondering why the click did nothing.
Future<bool> ensureResumeUploaded(
  BuildContext context, {
  required String action,
}) async {
  final pp = context.read<ProfileProvider>();
  final docs = (pp.profile?['documents'] as Map?) ?? const {};
  final resumeUrl = (docs['resumeUrl'] ?? '').toString().trim();
  if (resumeUrl.isNotEmpty) return true;

  if (!context.mounted) return false;
  await showDialog<void>(
    context: context,
    barrierDismissible: false,
    builder: (ctx) => AlertDialog(
      title: const Row(
        children: [
          Icon(Icons.upload_file_rounded, color: AppTheme.primary, size: 24),
          SizedBox(width: 10),
          Expanded(child: Text('Upload your resume first')),
        ],
      ),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'We need your resume to $action. It powers the AI matching, '
            'skills extraction and personalised suggestions \u2014 without it '
            'we can\'t tailor anything to you.',
            style: const TextStyle(fontSize: 14, height: 1.4),
          ),
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: AppTheme.primary.withValues(alpha: 0.06),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: AppTheme.primary.withValues(alpha: 0.18)),
            ),
            child: const Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.lock_open_rounded, size: 18, color: AppTheme.primary),
                SizedBox(width: 8),
                Expanded(
                  child: Text(
                    'Takes under 30 seconds. We accept PDF resumes.',
                    style: TextStyle(fontSize: 13, height: 1.3),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(ctx).pop(),
          child: const Text('Maybe later'),
        ),
        FilledButton.icon(
          onPressed: () {
            Navigator.of(ctx).pop();
            try {
              context.go('/profile');
            } catch (_) {/* swallow \u2014 best-effort nav */}
          },
          icon: const Icon(Icons.person_rounded, size: 18),
          label: const Text('Go to Profile'),
          style: FilledButton.styleFrom(backgroundColor: AppTheme.primary),
        ),
      ],
    ),
  );
  return false;
}
