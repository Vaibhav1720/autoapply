import 'package:flutter/material.dart';

import 'login_demo_video_stub.dart'
    if (dart.library.html) 'login_demo_video_web.dart' as impl;

/// Landing-page product demo video (web: HTML5; other platforms: no-op).
Widget buildLoginDemoVideo() => impl.buildLoginDemoVideo();
