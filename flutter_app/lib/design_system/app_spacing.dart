import 'package:flutter/widgets.dart';

/// Spacing, radii and breakpoints — a 4pt grid.
///
/// A grid matters more here than in most apps: this interface is dense with
/// numbers, and inconsistent gaps between rows of figures read as accidental
/// grouping. Every gap in the app comes from this scale so the eye can trust that
/// two things spaced alike are related alike.
class AppSpacing {
  const AppSpacing._();

  static const xxs = 2.0;
  static const xs = 4.0;
  static const sm = 8.0;
  static const md = 12.0;
  static const lg = 16.0;
  static const xl = 24.0;
  static const xxl = 32.0;
  static const xxxl = 48.0;

  /// Page gutter, which grows with the viewport so content does not hug the edge
  /// on a wide screen.
  static double gutter(double width) {
    if (width >= Breakpoints.desktop) return xxl;
    if (width >= Breakpoints.tablet) return xl;
    return lg;
  }
}

class AppRadius {
  const AppRadius._();

  /// Chips, badges, small controls.
  static const sm = 6.0;
  /// Cards, inputs, buttons.
  static const md = 10.0;
  /// Sheets, dialogs, hero panels.
  static const lg = 16.0;
  static const pill = 999.0;

  static const cardBorder = BorderRadius.all(Radius.circular(md));
  static const sheetBorder = BorderRadius.vertical(top: Radius.circular(lg));
}

/// Layout breakpoints.
///
/// Chosen from what the CONTENT needs, not from device marketing names: `tablet`
/// is where a two-column metric grid stops being cramped, `desktop` is where a
/// persistent navigation rail becomes cheaper than a bottom bar, and `wide` is
/// where a third column of detail earns its place.
class Breakpoints {
  const Breakpoints._();

  static const tablet = 640.0;
  static const desktop = 1024.0;
  static const wide = 1440.0;

  static bool isMobile(double width) => width < tablet;
  static bool isTablet(double width) => width >= tablet && width < desktop;
  static bool isDesktop(double width) => width >= desktop;

  /// Columns for a metric grid at this width.
  static int metricColumns(double width) {
    if (width >= wide) return 4;
    if (width >= desktop) return 3;
    if (width >= tablet) return 2;
    return 1;
  }
}

/// Durations. Short enough that the interface never feels like it is waiting on
/// itself; every one of them is skipped entirely when the user has asked for
/// reduced motion (see AppTheme).
class AppMotion {
  const AppMotion._();

  static const fast = Duration(milliseconds: 120);
  static const normal = Duration(milliseconds: 200);
  static const slow = Duration(milliseconds: 320);
}
