import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:auto_apply/config/constants.dart';
import 'package:auto_apply/config/theme.dart';
import 'package:auto_apply/config/routes.dart';
import 'package:auto_apply/services/api_service.dart';
import 'package:auto_apply/services/auth_service.dart';
import 'package:auto_apply/providers/auth_provider.dart';
import 'package:auto_apply/providers/profile_provider.dart';

/// Root application widget.
class AutoApplyApp extends StatelessWidget {
  const AutoApplyApp({super.key});

  @override
  Widget build(BuildContext context) {
    final api = ApiService();

    return MultiProvider(
      providers: [
        Provider<ApiService>.value(value: api),
        ChangeNotifierProvider(create: (_) => AuthProvider(AuthService(api), api)),
        ChangeNotifierProvider(create: (_) => ProfileProvider(api)),
      ],
      child: MaterialApp.router(
        title: AppConstants.appName,
        theme: AppTheme.lightTheme,
        routerConfig: appRouter,
        debugShowCheckedModeBanner: false,
      ),
    );
  }
}
