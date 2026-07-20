import 'package:flutter/material.dart';

import '../design_system/app_colors.dart';
import '../design_system/app_spacing.dart';
import '../design_system/app_theme.dart';
import '../design_system/app_typography.dart';

/// One destination in the app's navigation.
class NavItem {
  const NavItem({
    required this.label,
    required this.icon,
    required this.selectedIcon,
    this.tooltip,
  });

  final String label;
  final IconData icon;
  final IconData selectedIcon;
  final String? tooltip;
}

/// The responsive frame around every page.
///
/// WHY THREE LAYOUTS AND NOT A SHRINKING BOTTOM BAR
/// ------------------------------------------------
/// The app has eight destinations. A bottom bar with eight items is unusable on a
/// phone (targets below the 48dp minimum) and wasteful on a 27-inch monitor,
/// where a third of the chrome is empty space that a persistent rail would fill
/// with permanently-visible labels.
///
///   < 640   phone     bottom bar, 5 primary destinations + "Plus" sheet
///   640-1024 tablet   collapsed rail, icons with tooltips
///   >= 1024  desktop  extended rail, icons + labels + live status
///
/// Breakpoints come from what the CONTENT needs (see Breakpoints), not from
/// device marketing names.
class AppShell extends StatelessWidget {
  const AppShell({
    super.key,
    required this.items,
    required this.index,
    required this.onSelect,
    required this.child,
    this.header,
    this.primaryCount = 5,
  });

  final List<NavItem> items;
  final int index;
  final ValueChanged<int> onSelect;
  final Widget child;

  /// Rendered above the content on every layout — the status strip.
  final Widget? header;

  /// How many destinations get a slot in the phone bottom bar.
  final int primaryCount;

  @override
  Widget build(BuildContext context) {
    final width = context.widthOf;
    if (Breakpoints.isDesktop(width)) return _desktop(context, extended: true);
    if (Breakpoints.isTablet(width)) return _desktop(context, extended: false);
    return _mobile(context);
  }

  // ------------------------------------------------------------------ desktop
  Widget _desktop(BuildContext context, {required bool extended}) {
    final palette = context.palette;
    return Scaffold(
      backgroundColor: palette.ground,
      body: Row(
        children: [
          _Rail(
            items: items,
            index: index,
            onSelect: onSelect,
            extended: extended,
          ),
          VerticalDivider(width: 1, color: palette.line),
          Expanded(
            child: Column(
              children: [
                if (header != null) header!,
                Expanded(child: child),
              ],
            ),
          ),
        ],
      ),
    );
  }

  // ------------------------------------------------------------------- mobile
  Widget _mobile(BuildContext context) {
    final palette = context.palette;
    final primary = items.take(primaryCount).toList();
    final overflow = items.skip(primaryCount).toList();
    final inOverflow = index >= primaryCount;

    return Scaffold(
      backgroundColor: palette.ground,
      body: Column(
        children: [
          if (header != null) header!,
          Expanded(child: child),
        ],
      ),
      bottomNavigationBar: DecoratedBox(
        decoration: BoxDecoration(
          border: Border(top: BorderSide(color: palette.line)),
        ),
        child: NavigationBar(
          selectedIndex: inOverflow ? primary.length : index,
          onDestinationSelected: (selected) {
            if (selected < primary.length) {
              onSelect(selected);
            } else {
              _showOverflow(context, overflow);
            }
          },
          destinations: [
            for (final item in primary)
              NavigationDestination(
                icon: Icon(item.icon),
                selectedIcon: Icon(item.selectedIcon),
                label: item.label,
                tooltip: item.tooltip ?? item.label,
              ),
            NavigationDestination(
              icon: const Icon(Icons.more_horiz),
              selectedIcon: const Icon(Icons.more_horiz),
              label: 'Plus',
              tooltip: 'Autres sections',
            ),
          ],
        ),
      ),
    );
  }

  void _showOverflow(BuildContext context, List<NavItem> overflow) {
    final palette = context.palette;
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: palette.surface,
      showDragHandle: true,
      builder: (sheetContext) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            for (var offset = 0; offset < overflow.length; offset++)
              ListTile(
                leading: Icon(
                  overflow[offset].icon,
                  color: index == primaryCount + offset
                      ? AppColors.primary
                      : palette.textMuted,
                ),
                title: Text(overflow[offset].label, style: context.type.titleMedium),
                selected: index == primaryCount + offset,
                onTap: () {
                  Navigator.of(sheetContext).pop();
                  onSelect(primaryCount + offset);
                },
              ),
            const SizedBox(height: AppSpacing.sm),
          ],
        ),
      ),
    );
  }
}

class _Rail extends StatelessWidget {
  const _Rail({
    required this.items,
    required this.index,
    required this.onSelect,
    required this.extended,
  });

  final List<NavItem> items;
  final int index;
  final ValueChanged<int> onSelect;
  final bool extended;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return SizedBox(
      width: extended ? 216 : 76,
      child: Column(
        children: [
          Padding(
            padding: EdgeInsets.symmetric(
              horizontal: extended ? AppSpacing.lg : AppSpacing.sm,
              vertical: AppSpacing.xl,
            ),
            child: _Wordmark(extended: extended),
          ),
          Expanded(
            child: ListView.builder(
              padding: EdgeInsets.symmetric(
                  horizontal: extended ? AppSpacing.sm : AppSpacing.xs),
              itemCount: items.length,
              itemBuilder: (context, position) => _RailTile(
                item: items[position],
                selected: position == index,
                extended: extended,
                onTap: () => onSelect(position),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(AppSpacing.md),
            child: Text(
              extended ? 'Cours différés ~15 min' : '~15′',
              style: context.type.labelSmall?.copyWith(color: palette.textMuted),
              textAlign: TextAlign.center,
            ),
          ),
        ],
      ),
    );
  }
}

class _RailTile extends StatelessWidget {
  const _RailTile({
    required this.item,
    required this.selected,
    required this.extended,
    required this.onTap,
  });

  final NavItem item;
  final bool selected;
  final bool extended;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final colour = selected ? AppColors.primary : palette.textMuted;

    final tile = Semantics(
      selected: selected,
      button: true,
      label: item.label,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(AppRadius.md),
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 2),
          padding: EdgeInsets.symmetric(
            horizontal: extended ? AppSpacing.md : 0,
            vertical: AppSpacing.md,
          ),
          decoration: BoxDecoration(
            color: selected
                ? AppColors.primary.withValues(alpha: 0.12)
                : Colors.transparent,
            borderRadius: BorderRadius.circular(AppRadius.md),
            // A left stripe as well as a tint: selection must survive being
            // rendered in greyscale or by a colour-blind reader.
            border: Border(
              left: BorderSide(
                color: selected ? AppColors.primary : Colors.transparent,
                width: 3,
              ),
            ),
          ),
          child: Row(
            mainAxisAlignment:
                extended ? MainAxisAlignment.start : MainAxisAlignment.center,
            children: [
              Icon(selected ? item.selectedIcon : item.icon, size: 20, color: colour),
              if (extended) ...[
                const SizedBox(width: AppSpacing.md),
                Expanded(
                  child: Text(
                    item.label,
                    style: context.type.titleSmall?.copyWith(
                      color: selected ? palette.text : palette.textMuted,
                      fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );

    return extended ? tile : Tooltip(message: item.tooltip ?? item.label, child: tile);
  }
}

class _Wordmark extends StatelessWidget {
  const _Wordmark({required this.extended});

  final bool extended;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final badge = Container(
      height: 32,
      width: 32,
      decoration: BoxDecoration(
        color: AppColors.primary.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(AppRadius.sm),
        border: Border.all(color: AppColors.primary.withValues(alpha: 0.32)),
      ),
      child: const Icon(Icons.candlestick_chart_rounded,
          size: 18, color: AppColors.primary),
    );

    if (!extended) return Center(child: badge);

    return Row(
      children: [
        badge,
        const SizedBox(width: AppSpacing.md),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('CASABLANCA', style: AppTypography.eyebrow(palette.textMuted)),
              Text('Bourse',
                  style: context.type.titleMedium?.copyWith(height: 1.1)),
            ],
          ),
        ),
      ],
    );
  }
}

/// The status strip above the content: freshness, refresh action, sign out.
///
/// Data age is shown on every screen rather than tucked into a settings page. On
/// a platform whose numbers come from a scraper that can silently fail, "when was
/// this last true?" is not a detail — it is the precondition for trusting
/// anything else on screen.
class StatusStrip extends StatelessWidget {
  const StatusStrip({
    super.key,
    required this.title,
    this.asOf,
    this.stale = false,
    this.refreshing = false,
    this.onRefresh,
    this.onSignOut,
    this.actions = const [],
  });

  final String title;
  final String? asOf;
  final bool stale;
  final bool refreshing;
  final VoidCallback? onRefresh;
  final VoidCallback? onSignOut;
  final List<Widget> actions;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final compact = Breakpoints.isMobile(context.widthOf);

    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: AppSpacing.gutter(context.widthOf),
        vertical: AppSpacing.md,
      ),
      decoration: BoxDecoration(
        color: palette.surface,
        border: Border(bottom: BorderSide(color: palette.line)),
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(title,
                    style: context.type.headlineSmall,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis),
                if (asOf != null)
                  Row(
                    children: [
                      Container(
                        height: 6,
                        width: 6,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: stale ? palette.warn : palette.bull,
                        ),
                      ),
                      const SizedBox(width: AppSpacing.sm),
                      Flexible(
                        child: Text(
                          stale ? 'Données à rafraîchir · $asOf' : 'À jour · $asOf',
                          style: context.type.bodySmall
                              ?.copyWith(color: palette.textMuted),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                  ),
              ],
            ),
          ),
          ...actions,
          if (onRefresh != null)
            IconButton(
              onPressed: refreshing ? null : onRefresh,
              tooltip: 'Actualiser les données',
              icon: refreshing
                  ? const SizedBox(
                      height: 16,
                      width: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.refresh_rounded, size: 20),
            ),
          if (onSignOut != null && !compact)
            IconButton(
              onPressed: onSignOut,
              tooltip: 'Se déconnecter',
              icon: const Icon(Icons.logout_rounded, size: 18),
            ),
        ],
      ),
    );
  }
}
