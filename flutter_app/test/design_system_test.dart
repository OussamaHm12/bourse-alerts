import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:bourse_casablanca/design_system/app_colors.dart';
import 'package:bourse_casablanca/design_system/app_spacing.dart';
import 'package:bourse_casablanca/design_system/app_theme.dart';
import 'package:bourse_casablanca/design_system/components.dart';

/// The frontend had no tests at all (AUDIT_2026-07-18.md §17).
///
/// These cover the rules the design system exists to enforce — the ones a
/// reviewer cannot check by eye and that break silently:
///
///   * state is never carried by colour alone
///   * both themes are real, not one inverted
///   * layout reflows rather than overflowing
///
/// Not pixel goldens: those fail on every font-rendering change and teach people
/// to regenerate them without looking, which is worse than no test.

Widget host(Widget child, {Brightness brightness = Brightness.dark, Size? size}) {
  final app = MaterialApp(
    theme: brightness == Brightness.dark ? AppTheme.dark() : AppTheme.light(),
    home: Scaffold(body: Center(child: child)),
  );
  if (size == null) return app;
  return MediaQuery(
    data: MediaQueryData(size: size),
    child: app,
  );
}

void main() {
  group('palette', () {
    test('both themes are defined and genuinely differ', () {
      expect(AppPalette.dark.isDark, isTrue);
      expect(AppPalette.light.isDark, isFalse);
      expect(AppPalette.dark.ground, isNot(AppPalette.light.ground));
      expect(AppPalette.dark.text, isNot(AppPalette.light.text));
    });

    test('semantic colours are darkened for the light theme, not reused', () {
      // The dark-theme bull/bear are too pale on white to reach AA contrast.
      expect(AppPalette.light.bull, isNot(AppPalette.dark.bull));
      expect(AppPalette.light.bear, isNot(AppPalette.dark.bear));
    });

    test('the accent is not a semantic colour', () {
      // Spending bull-green on a button teaches the eye to ignore green exactly
      // where it means "up".
      expect(AppColors.primary, isNot(AppColors.bull));
      expect(AppColors.primary, isNot(AppColors.bear));
      expect(AppColors.primary, isNot(AppColors.warn));
    });

    test('a flat or unknown change is neutral, not green', () {
      for (final palette in [AppPalette.dark, AppPalette.light]) {
        expect(palette.delta(0), palette.textMuted);
        expect(palette.delta(null), palette.textMuted);
        expect(palette.delta(1.5), palette.bull);
        expect(palette.delta(-1.5), palette.bear);
      }
    });

    test('the risk ramp is ordered and covers the whole scale', () {
      expect(AppColors.risk(0), AppColors.riskRamp.first);
      expect(AppColors.risk(100), AppColors.riskRamp.last);
      expect(AppColors.risk(50), isNot(AppColors.risk(0)));
    });
  });

  group('breakpoints', () {
    test('classify by width, exclusively', () {
      expect(Breakpoints.isMobile(500), isTrue);
      expect(Breakpoints.isTablet(500), isFalse);
      expect(Breakpoints.isTablet(800), isTrue);
      expect(Breakpoints.isDesktop(800), isFalse);
      expect(Breakpoints.isDesktop(1200), isTrue);
    });

    test('metric columns grow with width and never reach zero', () {
      expect(Breakpoints.metricColumns(400), 1);
      expect(Breakpoints.metricColumns(800), 2);
      expect(Breakpoints.metricColumns(1100), 3);
      expect(Breakpoints.metricColumns(1600), 4);
    });
  });

  group('DeltaText', () {
    testWidgets('carries direction without relying on colour', (tester) async {
      await tester.pumpWidget(host(const DeltaText(2.5)));
      // Sign and arrow are both present, so the value survives greyscale.
      expect(find.textContaining('+2.50'), findsOneWidget);
      expect(find.textContaining('▲'), findsOneWidget);
    });

    testWidgets('a negative value shows a down arrow', (tester) async {
      await tester.pumpWidget(host(const DeltaText(-3.25)));
      expect(find.textContaining('-3.25'), findsOneWidget);
      expect(find.textContaining('▼'), findsOneWidget);
    });

    testWidgets('a flat value shows no arrow at all', (tester) async {
      await tester.pumpWidget(host(const DeltaText(0)));
      expect(find.textContaining('▲'), findsNothing);
      expect(find.textContaining('▼'), findsNothing);
    });

    testWidgets('a missing value is an em dash, not a zero', (tester) async {
      await tester.pumpWidget(host(const DeltaText(null)));
      expect(find.text('—'), findsOneWidget);
      expect(find.textContaining('0.00'), findsNothing);
    });
  });

  group('SignalBadge', () {
    testWidgets('pairs every recommendation with an icon', (tester) async {
      for (final code in [
        'STRONG_OPPORTUNITY',
        'WATCH',
        'HOLD',
        'TAKE_PROFIT',
        'RISKY',
        'AVOID',
      ]) {
        await tester.pumpWidget(host(SignalBadge(code, recommendation: code)));
        expect(find.byType(Icon), findsOneWidget, reason: '$code needs a non-colour cue');
      }
    });

    testWidgets('an unknown code degrades to neutral rather than crashing',
        (tester) async {
      await tester.pumpWidget(host(const SignalBadge('SOMETHING_NEW')));
      expect(find.text('SOMETHING_NEW'), findsOneWidget);
    });
  });

  group('ScoreMeter', () {
    testWidgets('spells the number out beside the bar', (tester) async {
      await tester.pumpWidget(host(const ScoreMeter(label: 'Score', value: 72)));
      expect(find.text('72'), findsOneWidget);
      expect(find.text('SCORE'), findsOneWidget);
    });

    testWidgets('an absent score is a dash, never a bar at zero', (tester) async {
      await tester.pumpWidget(host(const ScoreMeter(label: 'Score', value: null)));
      expect(find.text('—'), findsOneWidget);
    });

    testWidgets('an out-of-range value cannot overflow the track', (tester) async {
      await tester.pumpWidget(host(const ScoreMeter(label: 'Risque', value: 500)));
      expect(tester.takeException(), isNull);
    });
  });

  group('CoverageChip', () {
    testWidgets('states the coverage in words as well as a percentage',
        (tester) async {
      await tester.pumpWidget(host(const CoverageChip(0.35)));
      expect(find.textContaining('35%'), findsOneWidget);
      expect(find.textContaining('lacunaires'), findsOneWidget);
    });

    testWidgets('renders nothing when coverage is unknown', (tester) async {
      await tester.pumpWidget(host(const CoverageChip(null)));
      expect(find.byType(Tooltip), findsNothing);
    });
  });

  group('KindTag', () {
    testWidgets('distinguishes fact from inference from opinion', (tester) async {
      for (final entry in {
        'fact': 'FAIT',
        'inference': 'DÉDUCTION',
        'opinion': 'AVIS',
      }.entries) {
        await tester.pumpWidget(host(KindTag(entry.key)));
        expect(find.text(entry.value), findsOneWidget);
      }
    });
  });

  group('EmptyState', () {
    testWidgets('explains and offers an action', (tester) async {
      var tapped = false;
      await tester.pumpWidget(host(EmptyState(
        title: 'Aucune position',
        message: 'Renseignez PORTFOLIO_JSON côté serveur.',
        actionLabel: 'Actualiser',
        onAction: () => tapped = true,
      )));
      expect(find.text('Aucune position'), findsOneWidget);
      expect(find.textContaining('PORTFOLIO_JSON'), findsOneWidget);
      await tester.tap(find.text('Actualiser'));
      expect(tapped, isTrue);
    });
  });

  group('layout', () {
    testWidgets('a metric tile does not overflow a narrow phone', (tester) async {
      tester.view.physicalSize = const Size(320, 640);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(host(const SizedBox(
        width: 150,
        child: MetricTile(
          label: 'Valeur totale du portefeuille',
          value: '1 234 567,89',
          delta: -2.5,
          footnote: 'Net de frais aller-retour',
        ),
      )));
      expect(tester.takeException(), isNull);
    });

    testWidgets('the responsive grid reflows instead of overflowing',
        (tester) async {
      addTearDown(tester.view.reset);
      for (final width in [320.0, 700.0, 1200.0]) {
        // The surface itself has to be resized, not just the child: the default
        // 800x600 test window makes a 1200px SizedBox overflow the *window*,
        // which is a fact about the harness rather than about the widget.
        tester.view.physicalSize = Size(width, 900);
        tester.view.devicePixelRatio = 1.0;

        await tester.pumpWidget(host(
          ResponsiveGrid(
            children: List.generate(
              6,
              (i) => MetricTile(label: 'M$i', value: '$i'),
            ),
          ),
        ));
        expect(tester.takeException(), isNull, reason: 'overflow at ${width}px');
      }
    });

    testWidgets('cards render in both themes', (tester) async {
      for (final brightness in Brightness.values) {
        await tester.pumpWidget(host(
          const AppCard(child: Text('contenu')),
          brightness: brightness,
        ));
        expect(find.text('contenu'), findsOneWidget);
        expect(tester.takeException(), isNull);
      }
    });
  });
}
