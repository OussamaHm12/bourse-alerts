import 'package:flutter/material.dart';

import 'app_colors.dart';
import 'app_spacing.dart';
import 'app_theme.dart';
import 'app_typography.dart';

/// Shared building blocks.
///
/// Everything here exists because the same shape appeared on three or more
/// screens. Nothing here is decorative: each component encodes a rule the
/// interface has to obey consistently — most importantly that **state is never
/// carried by colour alone** (see [DeltaText], [SignalBadge], [RiskMeter]).

// --------------------------------------------------------------------------- //
// Containers                                                                   //
// --------------------------------------------------------------------------- //

/// The standard panel. A hairline border rather than a shadow: on a dark ground
/// shadows are invisible, and on a light one a page of drop-shadowed cards reads
/// as a pile of receipts.
class AppCard extends StatelessWidget {
  const AppCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(AppSpacing.lg),
    this.onTap,
    this.accent,
    this.semanticLabel,
  });

  final Widget child;
  final EdgeInsets padding;
  final VoidCallback? onTap;

  /// Draws a 3px stripe down the leading edge. Used to mark severity on a card
  /// whose content is otherwise neutral — a second, non-colour cue always
  /// accompanies it in the body.
  final Color? accent;
  final String? semanticLabel;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    // The accent stripe is a thick left BORDER rather than a sibling in a
    // stretched Row. The Row version needed CrossAxisAlignment.stretch to make
    // the stripe full-height, which demands a bounded height — and inside a Wrap
    // or a scroll view the height is unbounded, so it asserted. A border is
    // laid out by the decoration, costs no extra render object, and cannot
    // constrain-fight with its parent.
    final content = Container(
      decoration: BoxDecoration(
        color: palette.surface,
        borderRadius: AppRadius.cardBorder,
        border: Border(
          top: BorderSide(color: palette.line),
          right: BorderSide(color: palette.line),
          bottom: BorderSide(color: palette.line),
          left: accent == null
              ? BorderSide(color: palette.line)
              : BorderSide(color: accent!, width: 3),
        ),
      ),
      child: Padding(padding: padding, child: child),
    );

    final wrapped = onTap == null
        ? content
        : InkWell(
            onTap: onTap,
            borderRadius: AppRadius.cardBorder,
            child: content,
          );

    return semanticLabel == null
        ? wrapped
        : Semantics(label: semanticLabel, container: true, child: wrapped);
  }
}

/// Section heading with an optional eyebrow and trailing action.
class SectionHeader extends StatelessWidget {
  const SectionHeader({super.key, required this.title, this.eyebrow, this.trailing, this.subtitle});

  final String title;
  final String? eyebrow;
  final String? subtitle;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.md),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (eyebrow != null) ...[
                  Text(eyebrow!.toUpperCase(), style: AppTypography.eyebrow(AppColors.primary)),
                  const SizedBox(height: AppSpacing.xs),
                ],
                Text(title, style: context.type.headlineSmall),
                if (subtitle != null) ...[
                  const SizedBox(height: AppSpacing.xxs),
                  Text(subtitle!, style: context.type.bodySmall?.copyWith(color: palette.textMuted)),
                ],
              ],
            ),
          ),
          if (trailing != null) trailing!,
        ],
      ),
    );
  }
}

// --------------------------------------------------------------------------- //
// Numbers and state                                                            //
// --------------------------------------------------------------------------- //

/// A signed figure, carrying its meaning three ways: colour, an explicit sign,
/// and a direction glyph.
///
/// The redundancy is the point. Roughly 8% of men cannot reliably separate the
/// green from the red, and this app's entire portfolio screen is signed numbers.
/// The arrow and the sign make it readable without colour at all.
class DeltaText extends StatelessWidget {
  const DeltaText(
    this.value, {
    super.key,
    this.suffix = '%',
    this.size = 15,
    this.showArrow = true,
    this.decimals = 2,
  });

  final double? value;
  final String suffix;
  final double size;
  final bool showArrow;
  final int decimals;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    if (value == null) {
      return Text('—',
          style: AppTypography.number(palette.textMuted, size: size),
          semanticsLabel: 'non disponible');
    }
    final positive = value! > 0;
    final flat = value! == 0;
    final colour = palette.delta(value);
    final glyph = flat ? '' : (positive ? '▲' : '▼');
    final formatted = '${positive ? '+' : ''}${value!.toStringAsFixed(decimals)}$suffix';

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (showArrow && !flat) ...[
          Text(glyph, style: TextStyle(color: colour, fontSize: size * 0.62)),
          const SizedBox(width: AppSpacing.xs),
        ],
        Text(
          formatted,
          style: AppTypography.number(colour, size: size),
          semanticsLabel:
              '$formatted, ${flat ? 'stable' : (positive ? 'en hausse' : 'en baisse')}',
        ),
      ],
    );
  }
}

/// A recommendation badge: icon + word + colour, never colour alone.
class SignalBadge extends StatelessWidget {
  const SignalBadge(this.label, {super.key, this.recommendation, this.compact = false});

  final String label;

  /// The engine's code (STRONG_OPPORTUNITY, WATCH, …) or the tab's short label.
  final String? recommendation;
  final bool compact;

  static (Color, IconData) _style(String key, AppPalette palette) => switch (key) {
        'STRONG_OPPORTUNITY' || 'ACHETER' => (palette.bull, Icons.trending_up_rounded),
        'WATCH' || 'SURVEILLER' => (AppColors.primary, Icons.visibility_outlined),
        'HOLD' || 'CONSERVER' => (palette.info, Icons.pause_circle_outline),
        'TAKE_PROFIT' => (palette.brass, Icons.savings_outlined),
        'RISKY' || 'RISQUÉ' => (palette.warn, Icons.warning_amber_rounded),
        'AVOID' || 'ÉVITER' => (palette.bear, Icons.block_rounded),
        _ => (palette.textMuted, Icons.remove_rounded),
      };

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final (colour, icon) = _style((recommendation ?? label).toUpperCase(), palette);
    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: compact ? AppSpacing.sm : AppSpacing.md,
        vertical: compact ? AppSpacing.xxs : AppSpacing.xs,
      ),
      decoration: BoxDecoration(
        color: colour.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(AppRadius.pill),
        border: Border.all(color: colour.withValues(alpha: 0.35)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: compact ? 12 : 14, color: colour),
          const SizedBox(width: AppSpacing.xs),
          Text(
            label,
            style: TextStyle(
              color: colour,
              fontSize: compact ? 10.5 : 12,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.3,
            ),
          ),
        ],
      ),
    );
  }
}

/// A 0-100 meter with its value spelled out. Used for score, confidence, risk.
class ScoreMeter extends StatelessWidget {
  const ScoreMeter({
    super.key,
    required this.label,
    required this.value,
    this.colour,
    this.inverted = false,
    this.hint,
  });

  final String label;
  final double? value;
  final Color? colour;

  /// True when a HIGH value is bad (risk). Only changes the default colour ramp.
  final bool inverted;
  final String? hint;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final resolved = colour ??
        (inverted
            ? AppColors.risk(value ?? 50)
            : (value == null ? palette.textMuted : AppColors.primary));
    final fraction = ((value ?? 0) / 100).clamp(0.0, 1.0);

    return Semantics(
      label: '$label ${value == null ? 'non disponible' : '${value!.round()} sur 100'}',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(label.toUpperCase(),
                    style: AppTypography.eyebrow(palette.textMuted)),
              ),
              if (hint != null) ...[
                Tooltip(message: hint!, child: Icon(Icons.info_outline, size: 13, color: palette.textMuted)),
                const SizedBox(width: AppSpacing.xs),
              ],
              Text(
                value == null ? '—' : value!.round().toString(),
                style: AppTypography.number(resolved, size: 16, weight: FontWeight.w700),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          ClipRRect(
            borderRadius: BorderRadius.circular(AppRadius.pill),
            child: Stack(
              children: [
                Container(height: 6, color: palette.line),
                FractionallySizedBox(
                  widthFactor: fraction,
                  child: Container(height: 6, color: resolved),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// One headline metric. The unit of the overview grid.
class MetricTile extends StatelessWidget {
  const MetricTile({
    super.key,
    required this.label,
    required this.value,
    this.delta,
    this.deltaSuffix = '%',
    this.icon,
    this.accent,
    this.footnote,
  });

  final String label;
  final String value;
  final double? delta;
  final String deltaSuffix;
  final IconData? icon;
  final Color? accent;
  final String? footnote;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return AppCard(
      accent: accent,
      padding: const EdgeInsets.all(AppSpacing.lg),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              if (icon != null) ...[
                Icon(icon, size: 14, color: palette.textMuted),
                const SizedBox(width: AppSpacing.xs),
              ],
              Expanded(
                child: Text(label.toUpperCase(),
                    style: AppTypography.eyebrow(palette.textMuted),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.centerLeft,
            child: Text(value, style: context.type.displaySmall),
          ),
          if (delta != null) ...[
            const SizedBox(height: AppSpacing.xs),
            DeltaText(delta, suffix: deltaSuffix, size: 13),
          ],
          if (footnote != null) ...[
            const SizedBox(height: AppSpacing.xs),
            Text(footnote!,
                style: context.type.bodySmall?.copyWith(color: palette.textMuted),
                maxLines: 2,
                overflow: TextOverflow.ellipsis),
          ],
        ],
      ),
    );
  }
}

/// Data-coverage indicator.
///
/// This is the visual counterpart of the engine's central honesty rule: a score
/// built on 40% of its inputs is not the same claim as one built on all of them.
/// The backend has always known this; the interface did not show it.
class CoverageChip extends StatelessWidget {
  const CoverageChip(this.coverage, {super.key, this.missingCount});

  final double? coverage;
  final int? missingCount;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    if (coverage == null) return const SizedBox.shrink();
    final percent = (coverage! * 100).round();
    final (colour, word) = switch (percent) {
      >= 80 => (palette.bull, 'complètes'),
      >= 60 => (AppColors.primary, 'partielles'),
      >= 40 => (palette.warn, 'lacunaires'),
      _ => (palette.bear, 'très lacunaires'),
    };
    return Tooltip(
      message: missingCount == null
          ? 'Part des indicateurs disponibles pour cet horizon'
          : '$missingCount indicateur(s) manquant(s) sur cet horizon',
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm, vertical: 3),
        decoration: BoxDecoration(
          color: colour.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(AppRadius.sm),
          border: Border.all(color: colour.withValues(alpha: 0.3)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.data_usage_rounded, size: 11, color: colour),
            const SizedBox(width: AppSpacing.xs),
            Text('Données $word · $percent%',
                style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w600, color: colour)),
          ],
        ),
      ),
    );
  }
}

// --------------------------------------------------------------------------- //
// States — every one of them, not just the happy path                          //
// --------------------------------------------------------------------------- //

/// Skeleton placeholder. Shaped like the content it replaces so the layout does
/// not jump when data lands.
class SkeletonBox extends StatefulWidget {
  const SkeletonBox({super.key, this.height = 16, this.width, this.radius = AppRadius.sm});

  final double height;
  final double? width;
  final double radius;

  @override
  State<SkeletonBox> createState() => _SkeletonBoxState();
}

class _SkeletonBoxState extends State<SkeletonBox> with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1100),
  );

  @override
  void initState() {
    super.initState();
    // Respect the OS "reduce motion" preference: a pulsing page is exactly the
    // kind of ambient movement that setting exists to stop.
    if (!(WidgetsBinding.instance.platformDispatcher.accessibilityFeatures.disableAnimations)) {
      _controller.repeat(reverse: true);
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) => Container(
        height: widget.height,
        width: widget.width,
        decoration: BoxDecoration(
          color: Color.lerp(palette.line, palette.surfaceRaised, _controller.value),
          borderRadius: BorderRadius.circular(widget.radius),
        ),
      ),
    );
  }
}

/// Loading placeholder for a list of cards.
class SkeletonList extends StatelessWidget {
  const SkeletonList({super.key, this.count = 4});

  final int count;

  @override
  Widget build(BuildContext context) => Column(
        children: List.generate(
          count,
          (_) => const Padding(
            padding: EdgeInsets.only(bottom: AppSpacing.md),
            child: AppCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SkeletonBox(height: 14, width: 120),
                  SizedBox(height: AppSpacing.md),
                  SkeletonBox(height: 24, width: 180),
                  SizedBox(height: AppSpacing.sm),
                  SkeletonBox(height: 12),
                ],
              ),
            ),
          ),
        ),
      );
}

/// The generic non-happy state: empty, error, offline, degraded, not-yet-computed.
///
/// One component for all of them because they share a shape — say what happened,
/// say why, and offer the action that resolves it. A bare "Aucune donnée" leaves
/// the reader unable to tell a broken scraper from an empty watchlist.
class EmptyState extends StatelessWidget {
  const EmptyState({
    super.key,
    required this.title,
    required this.message,
    this.icon = Icons.inbox_outlined,
    this.actionLabel,
    this.onAction,
    this.tone = EmptyTone.neutral,
  });

  final String title;
  final String message;
  final IconData icon;
  final String? actionLabel;
  final VoidCallback? onAction;
  final EmptyTone tone;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final colour = switch (tone) {
      EmptyTone.neutral => palette.textMuted,
      EmptyTone.warning => palette.warn,
      EmptyTone.error => palette.bear,
    };
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 420),
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.xl),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                padding: const EdgeInsets.all(AppSpacing.lg),
                decoration: BoxDecoration(
                  color: colour.withValues(alpha: 0.1),
                  shape: BoxShape.circle,
                ),
                child: Icon(icon, size: 28, color: colour),
              ),
              const SizedBox(height: AppSpacing.lg),
              Text(title, style: context.type.headlineSmall, textAlign: TextAlign.center),
              const SizedBox(height: AppSpacing.sm),
              Text(
                message,
                style: context.type.bodyMedium?.copyWith(color: palette.textMuted),
                textAlign: TextAlign.center,
              ),
              if (actionLabel != null && onAction != null) ...[
                const SizedBox(height: AppSpacing.lg),
                FilledButton(onPressed: onAction, child: Text(actionLabel!)),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

enum EmptyTone { neutral, warning, error }

/// Banner for data that is present but stale or degraded — the case between
/// "fine" and "broken", which is the one this platform is in most often.
class NoticeBanner extends StatelessWidget {
  const NoticeBanner({
    super.key,
    required this.message,
    this.icon = Icons.info_outline,
    this.tone = EmptyTone.neutral,
    this.onAction,
    this.actionLabel,
  });

  final String message;
  final IconData icon;
  final EmptyTone tone;
  final VoidCallback? onAction;
  final String? actionLabel;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final colour = switch (tone) {
      EmptyTone.neutral => AppColors.primary,
      EmptyTone.warning => palette.warn,
      EmptyTone.error => palette.bear,
    };
    return Container(
      padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg, vertical: AppSpacing.md),
      decoration: BoxDecoration(
        color: colour.withValues(alpha: 0.09),
        borderRadius: BorderRadius.circular(AppRadius.md),
        border: Border.all(color: colour.withValues(alpha: 0.28)),
      ),
      child: Row(
        children: [
          Icon(icon, size: 16, color: colour),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Text(message,
                style: context.type.bodySmall?.copyWith(color: palette.text)),
          ),
          if (onAction != null && actionLabel != null) ...[
            const SizedBox(width: AppSpacing.sm),
            TextButton(onPressed: onAction, child: Text(actionLabel!)),
          ],
        ],
      ),
    );
  }
}

/// Fact / inference / opinion marker.
///
/// The engine labels every statement with its epistemic status and the interface
/// never showed it. Displaying it is the difference between "the platform says
/// X" and "the platform observed X" — which is the whole claim this product
/// makes about itself.
class KindTag extends StatelessWidget {
  const KindTag(this.kind, {super.key});

  final String kind;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final (colour, label, hint) = switch (kind.toLowerCase()) {
      'fact' => (palette.info, 'FAIT', 'Donnée observée, non interprétée'),
      'inference' => (AppColors.primary, 'DÉDUCTION', 'Déduite de données observées'),
      'opinion' => (palette.brass, 'AVIS', 'Jugement du moteur, pas une donnée'),
      _ => (palette.textMuted, kind.toUpperCase(), ''),
    };
    return Tooltip(
      message: hint,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(3),
          border: Border.all(color: colour.withValues(alpha: 0.45)),
        ),
        child: Text(label,
            style: TextStyle(
                fontSize: 8.5, fontWeight: FontWeight.w700, letterSpacing: 0.6, color: colour)),
      ),
    );
  }
}

/// Responsive grid that reflows by available width rather than by device class.
class ResponsiveGrid extends StatelessWidget {
  const ResponsiveGrid({
    super.key,
    required this.children,
    this.spacing = AppSpacing.md,
    this.minTileWidth = 220,
  });

  final List<Widget> children;
  final double spacing;
  final double minTileWidth;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
        builder: (context, constraints) {
          final columns =
              (constraints.maxWidth / minTileWidth).floor().clamp(1, 4);
          final tileWidth =
              (constraints.maxWidth - spacing * (columns - 1)) / columns;
          return Wrap(
            spacing: spacing,
            runSpacing: spacing,
            children: [
              for (final child in children)
                SizedBox(width: tileWidth, child: child),
            ],
          );
        },
      );
}
