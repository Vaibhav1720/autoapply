import 'package:flutter/material.dart';
import 'package:auto_apply/config/theme.dart';

/// Lightweight Markdown renderer for the AI resume-suggestion output.
///
/// Supports the subset our backend actually emits:
///   - `# H1`, `## H2`, `### H3`
///   - `- bullet` / `* bullet` lines (single level)
///   - `**bold**` and `*italic*` inline
///   - Blank lines split paragraphs
///   - Inline `code` is rendered with a monospace face
///
/// Unknown constructs degrade gracefully to plain selectable text. Pulling in
/// the full `flutter_markdown` package would be overkill for a single dialog.
class SimpleMarkdown extends StatelessWidget {
  final String source;
  const SimpleMarkdown(this.source, {super.key});

  @override
  Widget build(BuildContext context) {
    final lines = source.replaceAll('\r\n', '\n').split('\n');
    final widgets = <Widget>[];
    final bulletBuffer = <String>[];

    void flushBullets() {
      if (bulletBuffer.isEmpty) return;
      for (final b in bulletBuffer) {
        widgets.add(Padding(
          padding: const EdgeInsets.only(left: 4, top: 2, bottom: 2),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Padding(
                padding: EdgeInsets.only(top: 6, right: 8),
                child: Icon(Icons.fiber_manual_record,
                    size: 6, color: AppTheme.primary),
              ),
              Expanded(child: SelectableText.rich(
                _renderInline(b, const TextStyle(fontSize: 13, height: 1.45)),
              )),
            ],
          ),
        ));
      }
      bulletBuffer.clear();
    }

    for (final raw in lines) {
      final line = raw.trimRight();
      if (line.trim().isEmpty) {
        flushBullets();
        widgets.add(const SizedBox(height: 8));
        continue;
      }
      // Headings
      if (line.startsWith('### ')) {
        flushBullets();
        widgets.add(Padding(
          padding: const EdgeInsets.only(top: 6, bottom: 2),
          child: SelectableText.rich(_renderInline(
            line.substring(4),
            const TextStyle(fontSize: 13, fontWeight: FontWeight.w700,
                color: AppTheme.primary),
          )),
        ));
        continue;
      }
      if (line.startsWith('## ')) {
        flushBullets();
        widgets.add(Padding(
          padding: const EdgeInsets.only(top: 12, bottom: 4),
          child: SelectableText.rich(_renderInline(
            line.substring(3),
            const TextStyle(fontSize: 15, fontWeight: FontWeight.w800,
                color: AppTheme.primary),
          )),
        ));
        continue;
      }
      if (line.startsWith('# ')) {
        flushBullets();
        widgets.add(Padding(
          padding: const EdgeInsets.only(top: 14, bottom: 6),
          child: SelectableText.rich(_renderInline(
            line.substring(2),
            const TextStyle(fontSize: 17, fontWeight: FontWeight.w800),
          )),
        ));
        continue;
      }
      // Bullets
      final bm = RegExp(r'^\s*[-*]\s+(.*)').firstMatch(line);
      if (bm != null) {
        bulletBuffer.add(bm.group(1) ?? '');
        continue;
      }
      // Plain paragraph line
      flushBullets();
      widgets.add(Padding(
        padding: const EdgeInsets.symmetric(vertical: 2),
        child: SelectableText.rich(_renderInline(
          line,
          const TextStyle(fontSize: 13, height: 1.45),
        )),
      ));
    }
    flushBullets();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: widgets,
    );
  }

  /// Parse `**bold**`, `*italic*`, and `` `code` `` into a TextSpan tree.
  TextSpan _renderInline(String text, TextStyle base) {
    final spans = <TextSpan>[];
    // Pattern matches **bold**, *italic*, or `code` greedily but minimally.
    final re = RegExp(r'(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)');
    int idx = 0;
    for (final m in re.allMatches(text)) {
      if (m.start > idx) {
        spans.add(TextSpan(text: text.substring(idx, m.start), style: base));
      }
      final tok = m.group(0)!;
      if (tok.startsWith('**') && tok.endsWith('**')) {
        spans.add(TextSpan(
          text: tok.substring(2, tok.length - 2),
          style: base.copyWith(fontWeight: FontWeight.w700),
        ));
      } else if (tok.startsWith('`') && tok.endsWith('`')) {
        spans.add(TextSpan(
          text: tok.substring(1, tok.length - 1),
          style: base.copyWith(
            fontFamily: 'monospace',
            fontSize: (base.fontSize ?? 13) - 0.5,
            backgroundColor: AppTheme.primarySoft,
          ),
        ));
      } else if (tok.startsWith('*') && tok.endsWith('*')) {
        spans.add(TextSpan(
          text: tok.substring(1, tok.length - 1),
          style: base.copyWith(fontStyle: FontStyle.italic),
        ));
      } else {
        spans.add(TextSpan(text: tok, style: base));
      }
      idx = m.end;
    }
    if (idx < text.length) {
      spans.add(TextSpan(text: text.substring(idx), style: base));
    }
    return TextSpan(children: spans, style: base);
  }
}
