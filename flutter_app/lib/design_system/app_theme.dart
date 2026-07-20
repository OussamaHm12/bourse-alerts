import 'package:flutter/material.dart';

import 'app_colors.dart';
import 'app_spacing.dart';
import 'app_typography.dart';

/// Semantic colours resolved for the active brightness.
///
/// Widgets read these instead of reaching for [AppColors] directly, so a single
/// widget body serves both themes and neither can drift. Attached to [ThemeData]
/// as an extension rather than passed down, because a chart deep in a sheet needs
/// the same bull colour as a row at the top of a list.
@immutable
class AppPalette extends ThemeExtension<AppPalette> {
  const AppPalette({
    required this.ground,
    required this.surface,
    required this.surfaceRaised,
    required this.line,
    required this.text,
    required this.textMuted,
    required this.bull,
    required this.bear,
    required this.warn,
    required this.info,
    required this.brass,
    required this.isDark,
  });

  final Color ground;
  final Color surface;
  final Color surfaceRaised;
  final Color line;
  final Color text;
  final Color textMuted;
  final Color bull;
  final Color bear;
  final Color warn;
  final Color info;
  final Color brass;
  final bool isDark;

  static const dark = AppPalette(
    ground: AppColors.darkGround,
    surface: AppColors.darkSurface,
    surfaceRaised: AppColors.darkSurfaceRaised,
    line: AppColors.darkLine,
    text: AppColors.darkText,
    textMuted: AppColors.darkTextMuted,
    bull: AppColors.bull,
    bear: AppColors.bear,
    warn: AppColors.warn,
    info: AppColors.info,
    brass: AppColors.brass,
    isDark: true,
  );

  static const light = AppPalette(
    ground: AppColors.lightGround,
    surface: AppColors.lightSurface,
    surfaceRaised: AppColors.lightSurfaceRaised,
    line: AppColors.lightLine,
    text: AppColors.lightText,
    textMuted: AppColors.lightTextMuted,
    bull: AppColors.bullOnLight,
    bear: AppColors.bearOnLight,
    warn: AppColors.warnOnLight,
    info: AppColors.info,
    brass: AppColors.brass,
    isDark: false,
  );

  /// Signed-change colour. Zero and null are neutral — a flat day is not a win.
  Color delta(double? value) {
    if (value == null || value == 0) return textMuted;
    return value > 0 ? bull : bear;
  }

  @override
  AppPalette copyWith() => this;

  @override
  AppPalette lerp(ThemeExtension<AppPalette>? other, double t) =>
      t < 0.5 ? this : (other as AppPalette? ?? this);
}

/// Convenience: `context.palette.bull`.
extension PaletteContext on BuildContext {
  AppPalette get palette =>
      Theme.of(this).extension<AppPalette>() ?? AppPalette.dark;
  TextTheme get type => Theme.of(this).textTheme;
  double get widthOf => MediaQuery.sizeOf(this).width;

  /// True when the reader has asked the OS to reduce motion. Every animation in
  /// the app checks this — vestibular disorders are not an edge case, and a
  /// dashboard that lurches on every data refresh is genuinely unusable for them.
  bool get reducedMotion => MediaQuery.maybeOf(this)?.disableAnimations ?? false;
}

class AppTheme {
  const AppTheme._();

  static ThemeData dark() => _build(AppPalette.dark, Brightness.dark);
  static ThemeData light() => _build(AppPalette.light, Brightness.light);

  static ThemeData _build(AppPalette palette, Brightness brightness) {
    final scheme = ColorScheme.fromSeed(
      seedColor: AppColors.primary,
      brightness: brightness,
    ).copyWith(
      primary: AppColors.primary,
      onPrimary: brightness == Brightness.dark
          ? const Color(0xFF04231F)
          : Colors.white,
      surface: palette.surface,
      onSurface: palette.text,
      error: palette.bear,
    );

    final text = AppTypography.textTheme(palette.text, palette.textMuted);

    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: scheme,
      scaffoldBackgroundColor: palette.ground,
      canvasColor: palette.ground,
      textTheme: text,
      extensions: [palette],

      // Ink splashes on a data-dense surface read as noise; a quiet highlight is
      // enough feedback for a row tap.
      splashFactory: NoSplash.splashFactory,
      highlightColor: AppColors.primary.withValues(alpha: 0.06),
      hoverColor: AppColors.primary.withValues(alpha: 0.04),

      dividerTheme: DividerThemeData(
        color: palette.line,
        thickness: 1,
        space: 1,
      ),

      cardTheme: CardThemeData(
        color: palette.surface,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: AppRadius.cardBorder,
          side: BorderSide(color: palette.line),
        ),
      ),

      // A visible focus ring on every control: keyboard users must be able to see
      // where they are, and the browser default is removed by Flutter's canvas.
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: palette.surfaceRaised,
        contentPadding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AppRadius.md),
          borderSide: BorderSide(color: palette.line),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AppRadius.md),
          borderSide: BorderSide(color: palette.line),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AppRadius.md),
          borderSide: const BorderSide(color: AppColors.primary, width: 2),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AppRadius.md),
          borderSide: BorderSide(color: palette.bear, width: 1.5),
        ),
        hintStyle: text.bodyMedium?.copyWith(color: palette.textMuted),
      ),

      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          // 48dp minimum: below that a target is genuinely hard to hit on a phone.
          minimumSize: const Size(0, 48),
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AppRadius.md),
          ),
          textStyle: text.labelLarge,
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          minimumSize: const Size(0, 44),
          foregroundColor: AppColors.primary,
          textStyle: text.labelLarge,
        ),
      ),

      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: palette.surface,
        indicatorColor: AppColors.primary.withValues(alpha: 0.16),
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        height: 68,
        labelTextStyle: WidgetStatePropertyAll(text.labelSmall),
      ),
      navigationRailTheme: NavigationRailThemeData(
        backgroundColor: palette.surface,
        indicatorColor: AppColors.primary.withValues(alpha: 0.16),
        selectedIconTheme: const IconThemeData(color: AppColors.primary),
        unselectedIconTheme: IconThemeData(color: palette.textMuted),
        selectedLabelTextStyle: text.labelLarge?.copyWith(color: AppColors.primary),
        unselectedLabelTextStyle: text.labelMedium,
      ),

      snackBarTheme: SnackBarThemeData(
        backgroundColor: palette.surfaceRaised,
        contentTextStyle: text.bodyMedium,
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.md),
        ),
      ),

      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: palette.surface,
        surfaceTintColor: Colors.transparent,
        shape: const RoundedRectangleBorder(borderRadius: AppRadius.sheetBorder),
      ),

      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: AppColors.primary,
        linearMinHeight: 3,
      ),

      tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
          color: palette.surfaceRaised,
          borderRadius: BorderRadius.circular(AppRadius.sm),
          border: Border.all(color: palette.line),
        ),
        textStyle: text.bodySmall?.copyWith(color: palette.text),
        waitDuration: const Duration(milliseconds: 400),
      ),
    );
  }
}
