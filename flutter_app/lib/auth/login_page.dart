import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';

import '../design_system/app_colors.dart';
import '../design_system/app_spacing.dart';
import '../design_system/app_theme.dart';
import '../design_system/app_typography.dart';

/// The login screen.
///
/// The audit's CRITIQUE finding was that the platform served holdings, buy prices
/// and P/L to anyone with the URL. The backend fix (deny-by-default sessions) is
/// only half of it: without this screen the app answers 401 to everything and the
/// owner has no way in, which is why the backend change could not ship alone.
///
/// DESIGN
/// A login screen is where a financial tool either earns trust or looks like a
/// prototype. It is deliberately quiet — one field, one action, no marketing —
/// but it states what it protects and why the session lasts as long as it does.
/// A bare `AlertDialog` asking for a password would undercut everything the rest
/// of the interface is trying to claim.
///
/// The password lives in a `TextEditingController` for the duration of one POST
/// and is disposed with the widget. It is never written to `localStorage`, never
/// logged, and never reaches the compiled bundle — the session is an HttpOnly
/// cookie the app itself cannot read.
class LoginPage extends StatefulWidget {
  const LoginPage({super.key, required this.onSubmit});

  /// Returns null on success, or the message to display.
  final Future<String?> Function(String password) onSubmit;

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final _controller = TextEditingController();
  final _focus = FocusNode();
  bool _busy = false;
  bool _obscured = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    // Focus the field on arrival: there is exactly one thing to do here.
    WidgetsBinding.instance.addPostFrameCallback((_) => _focus.requestFocus());
  }

  @override
  void dispose() {
    _controller.dispose();
    _focus.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final password = _controller.text;
    if (password.isEmpty || _busy) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    final failure = await widget.onSubmit(password);
    if (!mounted) return;
    setState(() {
      _busy = false;
      _error = failure;
    });
    if (failure != null) {
      // Clear on failure so a mistyped password is not silently re-submitted,
      // and announce it for screen readers — a colour change alone is not a
      // notification.
      _controller.clear();
      _focus.requestFocus();
      // Announced as well as displayed: a screen-reader user gets no signal from
      // a field turning red. `errorText` alone is not a notification.
      SemanticsService.announce(failure, Directionality.of(context));
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final width = context.widthOf;

    return Scaffold(
      backgroundColor: palette.ground,
      body: Center(
        child: SingleChildScrollView(
          padding: EdgeInsets.all(AppSpacing.gutter(width)),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 400),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _Mark(palette: palette),
                const SizedBox(height: AppSpacing.xl),
                Text('Bourse de Casablanca',
                    style: context.type.displaySmall, textAlign: TextAlign.center),
                const SizedBox(height: AppSpacing.sm),
                Text(
                  'Plateforme d’analyse privée',
                  style: context.type.bodyMedium?.copyWith(color: palette.textMuted),
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: AppSpacing.xxl),

                Container(
                  padding: const EdgeInsets.all(AppSpacing.xl),
                  decoration: BoxDecoration(
                    color: palette.surface,
                    borderRadius: BorderRadius.circular(AppRadius.lg),
                    border: Border.all(color: palette.line),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Text('MOT DE PASSE',
                          style: AppTypography.eyebrow(palette.textMuted)),
                      const SizedBox(height: AppSpacing.sm),
                      TextField(
                        controller: _controller,
                        focusNode: _focus,
                        obscureText: _obscured,
                        enabled: !_busy,
                        autofillHints: const [AutofillHints.password],
                        onSubmitted: (_) => _submit(),
                        style: context.type.bodyLarge,
                        decoration: InputDecoration(
                          hintText: '••••••••••••',
                          errorText: _error,
                          // Errors wrap rather than truncate: a lockout message
                          // carries the wait time, and truncating it removes the
                          // only actionable part.
                          errorMaxLines: 3,
                          prefixIcon: Icon(Icons.lock_outline,
                              size: 18, color: palette.textMuted),
                          suffixIcon: IconButton(
                            icon: Icon(
                              _obscured
                                  ? Icons.visibility_outlined
                                  : Icons.visibility_off_outlined,
                              size: 18,
                            ),
                            color: palette.textMuted,
                            tooltip: _obscured
                                ? 'Afficher le mot de passe'
                                : 'Masquer le mot de passe',
                            onPressed: () => setState(() => _obscured = !_obscured),
                          ),
                        ),
                      ),
                      const SizedBox(height: AppSpacing.lg),
                      FilledButton(
                        onPressed: _busy ? null : _submit,
                        child: _busy
                            ? const SizedBox(
                                height: 18,
                                width: 18,
                                child: CircularProgressIndicator(
                                    strokeWidth: 2, color: Colors.white),
                              )
                            : const Text('Se connecter'),
                      ),
                    ],
                  ),
                ),

                const SizedBox(height: AppSpacing.xl),
                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(Icons.shield_outlined, size: 13, color: palette.textMuted),
                    const SizedBox(width: AppSpacing.sm),
                    Flexible(
                      child: Text(
                        'Session chiffrée, valable 30 jours sur cet appareil.',
                        style: context.type.bodySmall?.copyWith(color: palette.textMuted),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// The brand mark: a stylised candlestick pair in zellige teal and brass.
///
/// Drawn rather than shipped as an asset — it is eight rectangles, and an SVG or
/// PNG would be one more file to keep in sync with the palette.
class _Mark extends StatelessWidget {
  const _Mark({required this.palette});

  final AppPalette palette;

  @override
  Widget build(BuildContext context) => Center(
        child: Container(
          height: 64,
          width: 64,
          decoration: BoxDecoration(
            color: AppColors.primary.withValues(alpha: 0.12),
            borderRadius: BorderRadius.circular(AppRadius.lg),
            border: Border.all(color: AppColors.primary.withValues(alpha: 0.3)),
          ),
          child: CustomPaint(painter: _CandlePainter(palette: palette)),
        ),
      );
}

class _CandlePainter extends CustomPainter {
  const _CandlePainter({required this.palette});

  final AppPalette palette;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()..style = PaintingStyle.fill;
    final unit = size.width / 12;

    void candle(double centerX, double top, double bottom, double bodyTop,
        double bodyBottom, Color colour) {
      paint.color = colour;
      canvas.drawRect(
        Rect.fromLTRB(centerX - unit * 0.14, top, centerX + unit * 0.14, bottom),
        paint,
      );
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTRB(centerX - unit, bodyTop, centerX + unit, bodyBottom),
          const Radius.circular(1.5),
        ),
        paint,
      );
    }

    candle(size.width * 0.34, size.height * 0.26, size.height * 0.76,
        size.height * 0.36, size.height * 0.66, AppColors.primary);
    candle(size.width * 0.66, size.height * 0.20, size.height * 0.70,
        size.height * 0.28, size.height * 0.56, AppColors.brass);
  }

  @override
  bool shouldRepaint(covariant _CandlePainter oldDelegate) => false;
}

/// Shown while the app is deciding whether an existing session is still valid.
///
/// A distinct state from both "logged out" and "loaded": flashing the login
/// screen for 200ms before swapping to the dashboard, on every launch of an
/// installed PWA, reads as a bug.
class AuthChecking extends StatelessWidget {
  const AuthChecking({super.key});

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Scaffold(
      backgroundColor: palette.ground,
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            _Mark(palette: palette),
            const SizedBox(height: AppSpacing.xl),
            SizedBox(
              width: 120,
              child: LinearProgressIndicator(
                backgroundColor: palette.line,
                minHeight: 2,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
