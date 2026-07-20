import 'package:flutter/material.dart';

/// The palette, in one place.
///
/// DIRECTION
/// ---------
/// A trading terminal for the Casablanca exchange, not a generic admin dashboard.
/// Two decisions drive everything below.
///
/// 1. **The accent is zellige teal, not fintech cyan.** The previous palette was
///    a bright cyan on near-black navy — the default every dashboard template
///    ships with. Teal-green carries the same "instrument" register while being
///    specific to this subject: it is the colour of Moroccan zellige, and it sits
///    far enough from both bull-green and bear-red to never be confused with a
///    state. Brass appears sparingly as a second accent, for the same reason a
///    trading desk has one warm metal in it and not five.
///
/// 2. **Semantic colour is NOT the accent.** Bull, bear and warning are their own
///    hues and are never used for branding, navigation or emphasis. On a screen
///    where green means "up" and red means "down", spending those colours on a
///    button teaches the eye to ignore them exactly when they matter.
///
/// ACCESSIBILITY
/// -------------
/// Colour is never the only carrier of meaning: every gain/loss surface pairs its
/// hue with a sign, an arrow glyph and a label. Roughly 8% of men have some form
/// of red-green colour blindness, and a portfolio screen that encodes profit
/// solely as "green" is unreadable to them. All body text pairs meet WCAG AA
/// (>= 4.5:1) against the surface it sits on.
class AppColors {
  const AppColors._();

  // --- Dark (default) ---
  // The ground is a desaturated blue-black with a faint warm bias rather than a
  // pure navy: on an OLED-ish display a true #0B1120 reads as a hole in the page,
  // and the warmth keeps long reading sessions from feeling clinical.
  static const darkGround = Color(0xFF0A0F14);
  static const darkSurface = Color(0xFF121A21);
  static const darkSurfaceRaised = Color(0xFF1A242D);
  static const darkLine = Color(0xFF27333D);
  static const darkText = Color(0xFFE8EEF2);
  static const darkTextMuted = Color(0xFF8C9AA6);

  // --- Light ---
  // Not a naive inversion. The light ground is a cool paper white; surfaces go
  // *lighter* than the ground (raised = closer to white) which is the opposite of
  // the dark theme, because elevation reads as "more light" on a light ground.
  static const lightGround = Color(0xFFF2F5F7);
  static const lightSurface = Color(0xFFFFFFFF);
  static const lightSurfaceRaised = Color(0xFFFFFFFF);
  static const lightLine = Color(0xFFDCE3E8);
  static const lightText = Color(0xFF0E1519);
  static const lightTextMuted = Color(0xFF5C6B77);

  // --- Brand ---
  /// Zellige teal. The single accent: navigation, focus, primary actions.
  static const primary = Color(0xFF17A398);
  static const primaryDim = Color(0xFF0F7A72);

  /// Brass. Used sparingly — a highlighted metric, a premium marker. Never a state.
  static const brass = Color(0xFFC79A4B);

  // --- Semantic (state only, never brand) ---
  static const bull = Color(0xFF3FB984);
  static const bear = Color(0xFFE56A5A);
  static const warn = Color(0xFFD9A441);
  static const info = Color(0xFF5B9DD9);

  /// Light-theme variants: the dark-theme semantics are too pale on white to hit
  /// AA, so they are darkened rather than reused.
  static const bullOnLight = Color(0xFF1B7A52);
  static const bearOnLight = Color(0xFFB33A3A);
  static const warnOnLight = Color(0xFF8A6516);

  /// Categorical series for charts. Ordered by perceptual distance so the first
  /// two are the most distinguishable — most charts here plot one or two series.
  static const chartSeries = <Color>[
    Color(0xFF17A398),
    Color(0xFFC79A4B),
    Color(0xFF5B9DD9),
    Color(0xFF9B7FD4),
    Color(0xFFE5896A),
    Color(0xFF6FBF73),
  ];

  /// Risk ramp, low -> high. Deliberately runs teal -> amber -> terracotta rather
  /// than green -> red: it stays legible for the most common colour-vision
  /// deficiencies because it varies in lightness as well as hue.
  static const riskRamp = <Color>[
    Color(0xFF17A398),
    Color(0xFF6FA85F),
    Color(0xFFD9A441),
    Color(0xFFD97B4B),
    Color(0xFFE56A5A),
  ];

  /// Colour for a 0-100 risk score.
  static Color risk(double score) {
    final index = (score / 100 * (riskRamp.length - 1)).clamp(0, riskRamp.length - 1.0);
    return riskRamp[index.round()];
  }

  /// Colour for a signed change. `dark` selects the theme-appropriate variant.
  static Color delta(double? value, {required bool dark}) {
    if (value == null || value == 0) return dark ? darkTextMuted : lightTextMuted;
    if (value > 0) return dark ? bull : bullOnLight;
    return dark ? bear : bearOnLight;
  }
}
