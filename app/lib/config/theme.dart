import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// ApplyRight design system theme.
///
/// Palette: a deep, modern royal-indigo blue (not the flat Material default).
/// Inspired by Linear / Notion / Vercel — saturated but not childish.
class AppTheme {
  AppTheme._();

  // ── Colors ──────────────────────────────────────────────────────────────────
  // Brand blues — deeper, richer, with violet undertones.
  static const Color primary = Color(0xFF1E3A8A);       // royal indigo
  static const Color primaryBright = Color(0xFF3B5BFD); // electric blue accent
  static const Color primarySoft = Color(0xFFEEF2FF);   // soft tint background
  static const Color secondary = Color(0xFF6D28D9);     // deep violet
  static const Color accent = Color(0xFF06B6D4);        // cyan highlight

  static const Color success = Color(0xFF059669);
  static const Color warning = Color(0xFFF59E0B);
  static const Color error = Color(0xFFDC2626);

  // Neutrals — slightly cooler with a touch of blue.
  static const Color background = Color(0xFFF6F8FC);
  static const Color surface = Color(0xFFFFFFFF);
  static const Color surfaceAlt = Color(0xFFF1F4FA);
  static const Color border = Color(0xFFE2E8F0);
  static const Color textPrimary = Color(0xFF0B1220);
  static const Color textSecondary = Color(0xFF5B6577);

  // Brand gradient — used for hero sections, buttons, top bar accent.
  static const LinearGradient brandGradient = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF1E3A8A), Color(0xFF3B5BFD), Color(0xFF6D28D9)],
    stops: [0.0, 0.55, 1.0],
  );

  // Subtle background gradient for app shell.
  static const LinearGradient backgroundGradient = LinearGradient(
    begin: Alignment.topCenter,
    end: Alignment.bottomCenter,
    colors: [Color(0xFFF7F9FE), Color(0xFFF1F4FA)],
  );

  // ── Spacing ─────────────────────────────────────────────────────────────────
  static const double spacing4 = 4;
  static const double spacing8 = 8;
  static const double spacing12 = 12;
  static const double spacing16 = 16;
  static const double spacing24 = 24;
  static const double spacing32 = 32;
  static const double spacing48 = 48;

  // ── Border Radius ───────────────────────────────────────────────────────────
  static final BorderRadius cardRadius = BorderRadius.circular(14);
  static final BorderRadius buttonRadius = BorderRadius.circular(12);
  static final BorderRadius chipRadius = BorderRadius.circular(24);
  static final BorderRadius pillRadius = BorderRadius.circular(999);

  // ── Shadows ─────────────────────────────────────────────────────────────────
  static List<BoxShadow> softShadow = [
    BoxShadow(
      color: const Color(0xFF1E3A8A).withOpacity(0.06),
      blurRadius: 18,
      spreadRadius: -2,
      offset: const Offset(0, 6),
    ),
  ];
  static List<BoxShadow> elevatedShadow = [
    BoxShadow(
      color: const Color(0xFF1E3A8A).withOpacity(0.10),
      blurRadius: 28,
      spreadRadius: -4,
      offset: const Offset(0, 12),
    ),
  ];

  // ── ThemeData ───────────────────────────────────────────────────────────────
  static ThemeData get lightTheme {
    final textTheme = GoogleFonts.interTextTheme();

    return ThemeData(
      useMaterial3: true,
      colorScheme: ColorScheme.fromSeed(
        seedColor: primary,
        primary: primary,
        secondary: secondary,
        error: error,
        surface: surface,
        brightness: Brightness.light,
      ).copyWith(
        primary: primary,
        secondary: secondary,
        tertiary: accent,
      ),
      scaffoldBackgroundColor: background,
      dividerColor: border,
      textTheme: textTheme.copyWith(
        displayLarge: textTheme.displayLarge?.copyWith(
          fontSize: 40, fontWeight: FontWeight.w800,
          color: textPrimary, letterSpacing: -0.8,
        ),
        headlineLarge: textTheme.headlineLarge?.copyWith(
          fontSize: 30, fontWeight: FontWeight.w800,
          color: textPrimary, letterSpacing: -0.4,
        ),
        headlineMedium: textTheme.headlineMedium?.copyWith(
          fontSize: 22, fontWeight: FontWeight.w700,
          color: textPrimary, letterSpacing: -0.2,
        ),
        headlineSmall: textTheme.headlineSmall?.copyWith(
          fontSize: 18, fontWeight: FontWeight.w600,
          color: textPrimary,
        ),
        titleMedium: textTheme.titleMedium?.copyWith(
          fontSize: 15, fontWeight: FontWeight.w600,
          color: textPrimary,
        ),
        bodyLarge: textTheme.bodyLarge?.copyWith(
          fontSize: 15, color: textPrimary, height: 1.5,
        ),
        bodyMedium: textTheme.bodyMedium?.copyWith(
          fontSize: 14, color: textSecondary, height: 1.5,
        ),
        labelLarge: textTheme.labelLarge?.copyWith(
          fontSize: 14, fontWeight: FontWeight.w600,
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: primary,
          foregroundColor: Colors.white,
          shape: RoundedRectangleBorder(borderRadius: buttonRadius),
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
          textStyle: GoogleFonts.inter(
            fontSize: 14, fontWeight: FontWeight.w600, letterSpacing: 0.1,
          ),
          elevation: 0,
        ).copyWith(
          overlayColor: WidgetStateProperty.resolveWith(
            (s) => s.contains(WidgetState.hovered)
                ? Colors.white.withOpacity(0.08) : null,
          ),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: primary,
          side: const BorderSide(color: border, width: 1.2),
          shape: RoundedRectangleBorder(borderRadius: buttonRadius),
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 13),
          textStyle: GoogleFonts.inter(
            fontSize: 14, fontWeight: FontWeight.w600,
          ),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: primary,
          textStyle: GoogleFonts.inter(
            fontSize: 14, fontWeight: FontWeight.w600,
          ),
        ),
      ),
      cardTheme: CardTheme(
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: cardRadius,
          side: const BorderSide(color: border, width: 1),
        ),
        color: surface,
        margin: EdgeInsets.zero,
      ),
      chipTheme: ChipThemeData(
        shape: RoundedRectangleBorder(borderRadius: chipRadius),
        backgroundColor: primarySoft,
        labelStyle: GoogleFonts.inter(
          fontSize: 12, fontWeight: FontWeight.w600, color: primary,
        ),
        side: BorderSide.none,
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: surface,
        border: OutlineInputBorder(
          borderRadius: buttonRadius,
          borderSide: const BorderSide(color: border),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: buttonRadius,
          borderSide: const BorderSide(color: border),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: buttonRadius,
          borderSide: const BorderSide(color: primaryBright, width: 1.6),
        ),
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: surface,
        foregroundColor: textPrimary,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        titleTextStyle: GoogleFonts.inter(
          fontSize: 18, fontWeight: FontWeight.w700,
          color: textPrimary, letterSpacing: -0.2,
        ),
      ),
      bottomNavigationBarTheme: const BottomNavigationBarThemeData(
        selectedItemColor: primary,
        unselectedItemColor: textSecondary,
        type: BottomNavigationBarType.fixed,
        backgroundColor: surface,
      ),
    );
  }
}

/// Flutter 3.27+ adds [Color.withValues]; older SDKs only have [Color.withOpacity].
/// Screens that import this file get `withValues(alpha: …)` on [Color] for free.
extension ColorWithValuesCompat on Color {
  Color withValues({double? alpha, double? red, double? green, double? blue}) {
    if (alpha != null) {
      return withOpacity(alpha);
    }
    return this;
  }
}
