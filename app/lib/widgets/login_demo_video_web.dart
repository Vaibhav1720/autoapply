import 'dart:html' as html;
import 'dart:ui_web' as ui_web;

import 'package:flutter/material.dart';
import 'package:auto_apply/config/theme.dart';

/// HTML5 video for the public login / landing page (Flutter web only).
Widget buildLoginDemoVideo() {
  return const _LoginDemoVideoWeb();
}

class _LoginDemoVideoWeb extends StatefulWidget {
  const _LoginDemoVideoWeb();

  @override
  State<_LoginDemoVideoWeb> createState() => _LoginDemoVideoWebState();
}

class _LoginDemoVideoWebState extends State<_LoginDemoVideoWeb> {
  static int _viewCount = 0;
  late final String _viewType;

  @override
  void initState() {
    super.initState();
    _viewType = 'applyright-demo-video-${_viewCount++}';
    ui_web.platformViewRegistry.registerViewFactory(_viewType, (int viewId) {
      final video = html.VideoElement()
        ..src = '/applyright-demo.mp4'
        ..controls = true
        ..autoplay = false
        ..loop = false
        ..muted = false
        ..setAttribute('playsinline', 'true')
        ..style.border = 'none'
        ..style.width = '100%'
        ..style.height = '100%'
        ..style.objectFit = 'contain'
        ..style.borderRadius = '12px'
        ..style.backgroundColor = '#0f172a';
      return video;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ShaderMask(
          shaderCallback: (r) => AppTheme.brandGradient.createShader(r),
          child: const Text(
            'Watch ApplyRight Work for You',
            style: TextStyle(
              fontSize: 28,
              fontWeight: FontWeight.w800,
              color: Colors.white,
              letterSpacing: -0.5,
              height: 1.2,
            ),
          ),
        ),
        const SizedBox(height: 10),
        const Text(
          'A quick walkthrough: sign in, find AI-matched roles, and autofill applications in one click.',
          style: TextStyle(
            color: AppTheme.textSecondary,
            fontSize: 15,
            height: 1.45,
          ),
        ),
        const SizedBox(height: 20),
        AspectRatio(
          aspectRatio: 16 / 9,
          child: Container(
            width: double.infinity,
            decoration: BoxDecoration(
              color: const Color(0xFF0F172A),
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: AppTheme.border),
              boxShadow: AppTheme.elevatedShadow,
            ),
            clipBehavior: Clip.antiAlias,
            child: HtmlElementView(viewType: _viewType),
          ),
        ),
      ],
    );
  }
}
