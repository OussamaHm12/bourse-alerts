import 'package:flutter/material.dart';

/// The type system.
///
/// NO WEBFONT, ON PURPOSE
/// ----------------------
/// A financial dashboard is read every day, often for a few seconds at a time.
/// The system UI stack renders instantly, is already hinted for the reader's own
/// display, and never produces the flash-of-unstyled-text that a downloaded face
/// causes on a cold load. Shipping a 200 KB variable font to gain a slightly
/// warmer 'g' is a bad trade here — and this app is a PWA whose whole point is
/// opening fast.
///
/// The personality therefore has to come from the SCALE and the WEIGHTS rather
/// than from a bought typeface: tight, heavy display sizes against light,
/// generous body text, with a hard rule that numbers are tabular everywhere.
///
/// TABULAR FIGURES ARE NOT COSMETIC
/// --------------------------------
/// Proportional digits make a column of prices ragged, and a ragged column of
/// prices is genuinely harder to scan for the outlier — which is the single most
/// common thing a user does on these screens. Every numeric style below sets
/// `FontFeature.tabularFigures`.
class AppTypography {
  const AppTypography._();

  static const _sans = <String>[
    'system-ui',
    '-apple-system',
    'Segoe UI',
    'Roboto',
    'Helvetica Neue',
    'Arial',
  ];

  /// Monospace, for identifiers and raw values — a symbol is a code, not a word.
  static const _mono = <String>[
    'ui-monospace',
    'SF Mono',
    'Cascadia Mono',
    'Consolas',
    'Liberation Mono',
  ];

  static const _tabular = <FontFeature>[FontFeature.tabularFigures()];

  static TextTheme textTheme(Color text, Color muted) => TextTheme(
        // Display — the one number a screen exists to show (portfolio value, score).
        displayLarge: TextStyle(
          fontFamilyFallback: _sans,
          fontSize: 44,
          height: 1.04,
          fontWeight: FontWeight.w700,
          letterSpacing: -1.4,
          color: text,
          fontFeatures: _tabular,
        ),
        displayMedium: TextStyle(
          fontFamilyFallback: _sans,
          fontSize: 32,
          height: 1.08,
          fontWeight: FontWeight.w700,
          letterSpacing: -0.9,
          color: text,
          fontFeatures: _tabular,
        ),
        displaySmall: TextStyle(
          fontFamilyFallback: _sans,
          fontSize: 24,
          height: 1.12,
          fontWeight: FontWeight.w700,
          letterSpacing: -0.5,
          color: text,
          fontFeatures: _tabular,
        ),

        // Headlines — section and card titles.
        headlineMedium: TextStyle(
          fontFamilyFallback: _sans,
          fontSize: 20,
          height: 1.2,
          fontWeight: FontWeight.w700,
          letterSpacing: -0.3,
          color: text,
        ),
        headlineSmall: TextStyle(
          fontFamilyFallback: _sans,
          fontSize: 17,
          height: 1.25,
          fontWeight: FontWeight.w600,
          letterSpacing: -0.2,
          color: text,
        ),
        titleMedium: TextStyle(
          fontFamilyFallback: _sans,
          fontSize: 15,
          height: 1.3,
          fontWeight: FontWeight.w600,
          color: text,
        ),
        titleSmall: TextStyle(
          fontFamilyFallback: _sans,
          fontSize: 13.5,
          height: 1.3,
          fontWeight: FontWeight.w600,
          color: text,
        ),

        // Body — 1.5 line height, because these screens carry real explanatory
        // prose (the "why this recommendation" sections) and not just labels.
        bodyLarge: TextStyle(
          fontFamilyFallback: _sans,
          fontSize: 15,
          height: 1.5,
          color: text,
        ),
        bodyMedium: TextStyle(
          fontFamilyFallback: _sans,
          fontSize: 13.5,
          height: 1.5,
          color: text,
        ),
        bodySmall: TextStyle(
          fontFamilyFallback: _sans,
          fontSize: 12.5,
          height: 1.45,
          color: muted,
        ),

        // Labels — uppercase eyebrows get letter-spacing, which caps need to stay
        // readable at small sizes.
        labelLarge: TextStyle(
          fontFamilyFallback: _sans,
          fontSize: 13,
          fontWeight: FontWeight.w600,
          letterSpacing: 0.1,
          color: text,
        ),
        labelMedium: TextStyle(
          fontFamilyFallback: _sans,
          fontSize: 11,
          fontWeight: FontWeight.w600,
          letterSpacing: 0.9,
          color: muted,
        ),
        labelSmall: TextStyle(
          fontFamilyFallback: _sans,
          fontSize: 10,
          fontWeight: FontWeight.w600,
          letterSpacing: 0.8,
          color: muted,
        ),
      );

  /// A figure meant to be compared down a column.
  static TextStyle number(
    Color color, {
    double size = 15,
    FontWeight weight = FontWeight.w600,
  }) =>
      TextStyle(
        fontFamilyFallback: _sans,
        fontSize: size,
        fontWeight: weight,
        color: color,
        letterSpacing: -0.2,
        fontFeatures: _tabular,
      );

  /// A ticker, an event code, a raw identifier.
  static TextStyle code(Color color, {double size = 12}) => TextStyle(
        fontFamilyFallback: _mono,
        fontSize: size,
        fontWeight: FontWeight.w600,
        letterSpacing: 0.2,
        color: color,
      );

  /// Small caps eyebrow above a section.
  static TextStyle eyebrow(Color color) => TextStyle(
        fontFamilyFallback: _sans,
        fontSize: 10.5,
        fontWeight: FontWeight.w700,
        letterSpacing: 1.1,
        color: color,
      );
}
