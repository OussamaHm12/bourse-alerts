import 'dart:convert';
import 'dart:html' as html;
import 'dart:js_interop';
import 'dart:math' as math;

import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';

// --- minimal JS bridge to the proven web-push code living in web/push.js ---
@JS('appEnablePush')
external JSPromise<JSString> appEnablePush();
@JS('appTestPush')
external JSPromise<JSString> appTestPush();
@JS('appRunNow')
external JSPromise<JSString> appRunNow();

// ------------------------------- palette ---------------------------------- //
const bg = Color(0xFF0B1120);
const surface = Color(0xFF131C2E);
const surface2 = Color(0xFF1B2740);
const line = Color(0xFF26324A);
const text = Color(0xFFE6EDF7);
const muted = Color(0xFF8A9BB5);
const accent = Color(0xFF38BDF8);
const green = Color(0xFF34D399);
const red = Color(0xFFFB7185);
const amber = Color(0xFFFBBF24);

void main() => runApp(const BourseApp());

class BourseApp extends StatelessWidget {
  const BourseApp({super.key});
  @override
  Widget build(BuildContext context) {
    final scheme = ColorScheme.fromSeed(
      seedColor: accent,
      brightness: Brightness.dark,
    ).copyWith(surface: surface, primary: accent, onPrimary: const Color(0xFF06283D));
    return MaterialApp(
      title: 'Bourse Casablanca',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: scheme,
        scaffoldBackgroundColor: bg,
        textTheme: Typography.whiteMountainView.apply(
          bodyColor: text,
          displayColor: text,
        ),
        splashFactory: InkSparkle.splashFactory,
      ),
      home: const HomeShell(),
    );
  }
}

// ------------------------------- api -------------------------------------- //
Future<dynamic> api(String path) async {
  final uri = Uri.base.resolve(path).toString();
  final txt = await html.HttpRequest.getString(uri);
  return jsonDecode(txt);
}

// getString() is GET-only, so favoriting needs its own verb-aware call.
Future<dynamic> apiSend(String method, String path) async {
  final uri = Uri.base.resolve(path).toString();
  final req = await html.HttpRequest.request(uri, method: method);
  final body = req.responseText;
  return (body == null || body.isEmpty) ? <String, dynamic>{} : jsonDecode(body);
}

/// The starred symbols, shared by every page.
///
/// The pages live inside an IndexedStack, so they are built once and kept alive —
/// `initState` does NOT re-run when you switch tabs. Without a shared notifier,
/// starring a stock in Marché would leave the Favoris tab showing a stale list until
/// a manual pull-to-refresh. Everything that renders a star reads this instead of its
/// own copy, so the three surfaces (Marché, Favoris, fiche détail) can never disagree.
final favoriteSymbols = ValueNotifier<Set<String>>(<String>{});

Future<void> refreshFavorites() async {
  try {
    final d = await api('api/favorites');
    favoriteSymbols.value = ((d['symbols'] as List?) ?? []).map((s) => '$s').toSet();
  } catch (_) {
    // Non-fatal: the stars stay as they were rather than blanking out.
  }
}

/// Star / un-star a symbol. The new state comes from the server's response, so the
/// stars never drift from the source of truth that actually drives the alerts.
///
/// Never throws: a failed call leaves the star as it was rather than lying about a
/// favorite that was not saved. Returns the state now in effect.
Future<bool> toggleFavorite(String symbol, bool isFavorite) async {
  try {
    final r = await apiSend(isFavorite ? 'DELETE' : 'POST', 'api/favorites/$symbol');
    final now = (r['is_favorite'] as bool?) ?? !isFavorite;
    final next = Set<String>.from(favoriteSymbols.value);
    now ? next.add(symbol) : next.remove(symbol);
    favoriteSymbols.value = next; // notifies every star and page at once
    return now;
  } catch (_) {
    return isFavorite; // unchanged: the server did not accept the toggle
  }
}

/// The star. Drop it next to any symbol, in any tab.
///
/// It reads and writes the shared `favoriteSymbols` notifier, so it needs no state
/// from its parent and stays in sync with every other star in the app: starring in
/// Opportunités lights up the same stock in Marché and adds it to Favoris, live.
/// Each star listens for itself, so a toggle rebuilds one icon, not the whole list.
class SymbolStar extends StatelessWidget {
  const SymbolStar({super.key, required this.symbol, this.size = 20});
  final String symbol;
  final double size;

  @override
  Widget build(BuildContext context) => ValueListenableBuilder<Set<String>>(
        valueListenable: favoriteSymbols,
        builder: (context, starred, _) {
          final active = starred.contains(symbol);
          return IconButton(
            onPressed: () => toggleFavorite(symbol, active),
            visualDensity: VisualDensity.compact,
            padding: EdgeInsets.zero,
            constraints: const BoxConstraints(minWidth: 34, minHeight: 34),
            tooltip: active ? 'Retirer des favoris' : 'Suivre cette action',
            icon: Icon(
              active ? Icons.star : Icons.star_border,
              color: active ? amber : muted,
              size: size,
            ),
          );
        },
      );
}

String fmt(num? v, [int d = 2]) {
  if (v == null) return 'n/a';
  return v.toStringAsFixed(d).replaceAllMapped(RegExp(r'\B(?=(\d{3})+(?!\d))'), (m) => ' ');
}

String signed(num? v, [int d = 2]) => v == null ? 'n/a' : (v >= 0 ? '+' : '') + fmt(v, d);
Color plc(num? v) => (v ?? 0) >= 0 ? green : red;

Color labelColor(String label) => switch (label) {
      'ACHETER' => green,
      'ÉVITER' => red,
      'SURVEILLER' => amber,
      _ => muted,
    };

Widget badge(String label, [Color? color]) {
  final c = color ?? labelColor(label);
  return Container(
    padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
    decoration: BoxDecoration(
      color: c.withOpacity(0.14),
      borderRadius: BorderRadius.circular(999),
      border: Border.all(color: c.withOpacity(0.55)),
    ),
    child: Text(label, style: TextStyle(color: c, fontSize: 11, fontWeight: FontWeight.w700, letterSpacing: 0.3)),
  );
}

Widget glassCard({required Widget child, VoidCallback? onTap, EdgeInsets? padding}) => Container(
      margin: const EdgeInsets.only(bottom: 10),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          colors: [surface, surface2],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: line),
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(18),
          child: Padding(padding: padding ?? const EdgeInsets.all(16), child: child),
        ),
      ),
    );

Widget sectionTitle(String t) => Padding(
      padding: const EdgeInsets.fromLTRB(4, 14, 4, 8),
      child: Text(t.toUpperCase(),
          style: const TextStyle(color: muted, fontSize: 12.5, letterSpacing: 1.2, fontWeight: FontWeight.w700)),
    );

extension _Enter on Widget {
  Widget enter(int i) => animate().fadeIn(duration: 260.ms, delay: (i * 35).ms).slideY(begin: 0.08, end: 0, curve: Curves.easeOut);
}

// ------------------------------- shell ------------------------------------ //
class HomeShell extends StatefulWidget {
  const HomeShell({super.key});
  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _idx = 0;
  final _pages = const [
    PortfolioPage(),
    FavoritesPage(),
    MarketPage(),
    OppsPage(),
    AnalysisPage(),
    NewsPage(),
    NotificationsPage(),
  ];

  @override
  void initState() {
    super.initState();
    refreshFavorites(); // seed the stars once, before any page renders one
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Column(children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
            child: Row(children: [
              Container(
                width: 30,
                height: 30,
                decoration: BoxDecoration(
                  gradient: const LinearGradient(colors: [accent, green]),
                  borderRadius: BorderRadius.circular(9),
                ),
                child: const Icon(Icons.trending_up, size: 18, color: Color(0xFF06283D)),
              ),
              const SizedBox(width: 10),
              const Text('Bourse Casablanca',
                  style: TextStyle(fontSize: 19, fontWeight: FontWeight.w700, letterSpacing: -0.2)),
            ]),
          ),
          Expanded(child: IndexedStack(index: _idx, children: _pages)),
        ]),
      ),
      bottomNavigationBar: NavigationBarTheme(
        data: NavigationBarThemeData(
          backgroundColor: surface,
          indicatorColor: accent.withOpacity(0.18),
          labelTextStyle: WidgetStateProperty.all(const TextStyle(fontSize: 11, fontWeight: FontWeight.w600)),
        ),
        child: NavigationBar(
          selectedIndex: _idx,
          height: 64,
          labelBehavior: NavigationDestinationLabelBehavior.onlyShowSelected,
          onDestinationSelected: (i) => setState(() => _idx = i),
          destinations: const [
            NavigationDestination(icon: Icon(Icons.account_balance_wallet_outlined), selectedIcon: Icon(Icons.account_balance_wallet), label: 'Portefeuille'),
            NavigationDestination(icon: Icon(Icons.star_border), selectedIcon: Icon(Icons.star), label: 'Favoris'),
            NavigationDestination(icon: Icon(Icons.show_chart_outlined), selectedIcon: Icon(Icons.show_chart), label: 'Marché'),
            NavigationDestination(icon: Icon(Icons.bolt_outlined), selectedIcon: Icon(Icons.bolt), label: 'Opportunités'),
            NavigationDestination(icon: Icon(Icons.psychology_outlined), selectedIcon: Icon(Icons.psychology), label: 'Analyse'),
            NavigationDestination(icon: Icon(Icons.article_outlined), selectedIcon: Icon(Icons.article), label: 'Actus'),
            NavigationDestination(icon: Icon(Icons.notifications_outlined), selectedIcon: Icon(Icons.notifications), label: 'Notifs'),
          ],
        ),
      ),
    );
  }
}

// ------------------------------ portfolio --------------------------------- //
class PortfolioPage extends StatefulWidget {
  const PortfolioPage({super.key});
  @override
  State<PortfolioPage> createState() => _PortfolioPageState();
}

class _PortfolioPageState extends State<PortfolioPage> {
  Map<String, dynamic>? _data;
  String? _error;
  String _notif = 'Mises à jour toutes les 2h (9h–17h, jours ouvrés) et alertes.';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final d = await api('api/overview');
      setState(() {
        _data = d as Map<String, dynamic>;
        _error = null;
      });
    } catch (e) {
      setState(() => _error = e.toString());
    }
  }

  Future<void> _runNow() async {
    setState(() => _notif = '⏳ Collecte en cours… (~30 s)');
    try {
      final r = await appRunNow().toDart;
      setState(() => _notif = r.toDart);
      await Future.delayed(const Duration(seconds: 33));
      await _load();
      setState(() => _notif = '✅ Données actualisées.');
    } catch (e) {
      setState(() => _notif = 'Erreur : $e');
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = _data?['portfolio'] as Map<String, dynamic>?;
    final holdings = (p?['holdings'] as List?) ?? [];
    return RefreshIndicator(
      onRefresh: _load,
      color: accent,
      backgroundColor: surface2,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(14, 6, 14, 24),
        children: [
          _notifCard().enter(0),
          sectionTitle('Mon portefeuille'),
          if (_error != null)
            glassCard(child: Text('Erreur : $_error', style: const TextStyle(color: red)))
          else if (p == null)
            glassCard(child: const _Skeleton())
          else if (holdings.isEmpty)
            glassCard(child: const Text('Aucune position. Renseignez PORTFOLIO_JSON côté serveur.', style: TextStyle(color: muted)))
          else ...[
            _summaryCard(p).enter(1),
            ...holdings.asMap().entries.map((e) => _holdingCard(context, e.value as Map<String, dynamic>).enter(e.key + 2)),
          ],
        ],
      ),
    );
  }

  Widget _notifCard() => glassCard(
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
            const Expanded(
              child: Row(children: [
                Icon(Icons.notifications_active_outlined, size: 18, color: accent),
                SizedBox(width: 8),
                Text('Notifications', style: TextStyle(fontWeight: FontWeight.w700)),
              ]),
            ),
            FilledButton(
              style: FilledButton.styleFrom(backgroundColor: accent, foregroundColor: const Color(0xFF06283D), visualDensity: VisualDensity.compact),
              onPressed: () async {
                final r = await appEnablePush().toDart;
                setState(() => _notif = r.toDart);
              },
              child: const Text('Activer'),
            ),
          ]),
          const SizedBox(height: 6),
          Text(_notif, style: const TextStyle(color: muted, fontSize: 12.5)),
          const SizedBox(height: 12),
          Row(children: [
            Expanded(
              child: OutlinedButton.icon(
                style: OutlinedButton.styleFrom(foregroundColor: text, side: const BorderSide(color: line)),
                onPressed: _runNow,
                icon: const Icon(Icons.refresh, size: 18),
                label: const Text('Actualiser'),
              ),
            ),
            const SizedBox(width: 10),
            OutlinedButton(
              style: OutlinedButton.styleFrom(foregroundColor: muted, side: const BorderSide(color: line)),
              onPressed: () async {
                final r = await appTestPush().toDart;
                setState(() => _notif = r.toDart);
              },
              child: const Text('Tester'),
            ),
          ]),
        ]),
      );

  Widget _summaryCard(Map<String, dynamic> p) => glassCard(
        child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
          Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text('Valeur du portefeuille', style: TextStyle(color: muted, fontSize: 12.5)),
            const SizedBox(height: 4),
            Text('${fmt(p['total_value'], 0)} MAD', style: const TextStyle(fontSize: 27, fontWeight: FontWeight.w800, letterSpacing: -0.5)),
          ]),
          Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
            Text('P/L net · frais ${fmt((p['fee_rate'] ?? 0) * 100, 2)}%', style: const TextStyle(color: muted, fontSize: 11.5)),
            const SizedBox(height: 4),
            Text(signed(p['total_net_pl'], 0), style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800, color: plc(p['total_net_pl']))),
            Container(
              margin: const EdgeInsets.only(top: 2),
              padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
              decoration: BoxDecoration(color: plc(p['total_pl_pct']).withOpacity(0.14), borderRadius: BorderRadius.circular(6)),
              child: Text('${signed(p['total_pl_pct'], 1)}%', style: TextStyle(color: plc(p['total_pl_pct']), fontWeight: FontWeight.w700, fontSize: 12)),
            ),
          ]),
        ]),
      );
}

Widget _holdingCard(BuildContext context, Map<String, dynamic> h) {
  final sell = h['advice'] == 'SELL';
  return glassCard(
    onTap: () => showStockDetail(context, h['symbol'] as String),
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Expanded(
          child: Row(children: [
            Text(h['symbol'] ?? '', style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 16)),
            const SizedBox(width: 8),
            Expanded(child: Text(h['company_name'] ?? '', style: const TextStyle(color: muted, fontSize: 12), overflow: TextOverflow.ellipsis)),
          ]),
        ),
        badge(sell ? 'ÉVITER' : 'ACHETER'),
        // Holding it does not watch it: the two lists are independent, so a held
        // stock can still be starred (or not) like any other.
        SymbolStar(symbol: '${h['symbol']}', size: 18),
      ]),
      const SizedBox(height: 10),
      Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Text('${fmt(h['quantity'], 0)} × ${fmt(h['current_price'])}', style: const TextStyle(color: muted, fontSize: 13)),
        Text('${signed(h['net_pl'], 0)} (${signed(h['net_pl_pct'], 1)}%)', style: TextStyle(color: plc(h['net_pl']), fontSize: 13, fontWeight: FontWeight.w700)),
      ]),
      const SizedBox(height: 8),
      Text(h['advice_reason'] ?? '', style: const TextStyle(color: muted, fontSize: 12)),
    ]),
  );
}

// ------------------------------ favorites --------------------------------- //
// The watchlist. Deliberately NOT the portfolio: no quantity, no buy price, so no
// P/L and no SELL/HOLD advice. What a favorite buys is attention — the crash alert,
// priority on the thesis pushes, and its own digest section.
class FavoritesPage extends StatefulWidget {
  const FavoritesPage({super.key});
  @override
  State<FavoritesPage> createState() => _FavoritesPageState();
}

class _FavoritesPageState extends State<FavoritesPage> {
  List _favorites = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    // The page is kept alive by the IndexedStack, so initState never runs again.
    // Listening is what makes a stock starred from the Marché tab show up here.
    favoriteSymbols.addListener(_load);
    _load();
  }

  @override
  void dispose() {
    favoriteSymbols.removeListener(_load);
    super.dispose();
  }

  Future<void> _load() async {
    if (!mounted) return;
    setState(() => _loading = true);
    try {
      final d = await api('api/favorites');
      if (!mounted) return;
      setState(() {
        _favorites = (d['favorites'] as List?) ?? [];
        _error = null;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }


  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator(color: accent));
    return RefreshIndicator(
      onRefresh: _load,
      color: accent,
      backgroundColor: surface2,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(14, 6, 14, 24),
        children: [
          sectionTitle('Mes favoris'),
          if (_error != null)
            glassCard(child: Text('Erreur : $_error', style: const TextStyle(color: red)))
          else if (_favorites.isEmpty)
            glassCard(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: const [
                Row(children: [
                  Icon(Icons.star_border, color: muted, size: 18),
                  SizedBox(width: 8),
                  Text('Aucun favori', style: TextStyle(fontWeight: FontWeight.w700)),
                ]),
                SizedBox(height: 8),
                Text(
                  'Touchez l\'étoile sur une action, dans l\'onglet Marché, pour la suivre. '
                  'Un favori est surveillé comme votre portefeuille : alerte immédiate en cas '
                  'de chute, priorité sur les notifications, et une section dédiée dans le '
                  'résumé quotidien.',
                  style: TextStyle(color: muted, fontSize: 12.5, height: 1.45),
                ),
              ]),
            )
          else
            ..._favorites.asMap().entries.map(
                  (e) => _favoriteCard(context, e.value as Map<String, dynamic>).enter(e.key),
                ),
        ],
      ),
    );
  }
}

Widget _favoriteCard(BuildContext context, Map<String, dynamic> f) {
  final variation = f['daily_variation'] as num?;
  final score = f['buy_score'] as num?;
  final risks = (f['risks'] as List?) ?? [];
  return glassCard(
    onTap: () => showStockDetail(context, f['symbol'] as String),
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Text(f['symbol'] ?? '', style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 16)),
              const SizedBox(width: 8),
              badge(f['label'] ?? 'NEUTRE'),
            ]),
            const SizedBox(height: 2),
            Text(f['company_name'] ?? '',
                style: const TextStyle(color: muted, fontSize: 12), overflow: TextOverflow.ellipsis),
          ]),
        ),
        // Un-starring here empties the shared set, which reloads this very list.
        SymbolStar(symbol: '${f['symbol']}'),
      ]),
      const SizedBox(height: 10),
      Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
          Text('${fmt(f['price'])}', style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
          const SizedBox(width: 4),
          const Padding(
            padding: EdgeInsets.only(bottom: 2),
            child: Text('MAD', style: TextStyle(color: muted, fontSize: 11)),
          ),
          const SizedBox(width: 8),
          Padding(
            padding: const EdgeInsets.only(bottom: 2),
            child: Text('${signed(variation)}%',
                style: TextStyle(color: plc(variation), fontSize: 13, fontWeight: FontWeight.w700)),
          ),
        ]),
        Text(score == null ? 'n/a' : '${score.round()}/100',
            style: const TextStyle(color: muted, fontSize: 12, fontWeight: FontWeight.w700)),
      ]),
      const SizedBox(height: 8),
      Text(f['headline'] ?? '', style: const TextStyle(fontSize: 12.5, height: 1.4)),
      if (risks.isNotEmpty) ...[
        const SizedBox(height: 6),
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Icon(Icons.warning_amber_rounded, color: amber, size: 14),
          const SizedBox(width: 6),
          Expanded(
            child: Text('${risks.first}', style: const TextStyle(color: amber, fontSize: 11.5, height: 1.35)),
          ),
        ]),
      ],
    ]),
  );
}

// ------------------------------- market ----------------------------------- //
class MarketPage extends StatefulWidget {
  const MarketPage({super.key});
  @override
  State<MarketPage> createState() => _MarketPageState();
}

class _MarketPageState extends State<MarketPage> {
  List _stocks = [];
  String _sort = 'score';
  String _q = '';
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final params = {'sort': _sort, if (_q.isNotEmpty) 'q': _q};
      final qs = params.entries.map((e) => '${e.key}=${Uri.encodeComponent(e.value)}').join('&');
      final d = await api('api/stocks?$qs');
      setState(() {
        _stocks = (d['stocks'] as List?) ?? [];
        _loading = false;
      });
    } catch (_) {
      setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(14, 8, 14, 6),
        child: Row(children: [
          Expanded(
            child: TextField(
              style: const TextStyle(fontSize: 14),
              onChanged: (v) {
                _q = v.trim();
                _load();
              },
              decoration: InputDecoration(
                hintText: 'Rechercher une action…',
                hintStyle: const TextStyle(color: muted),
                prefixIcon: const Icon(Icons.search, color: muted, size: 20),
                filled: true,
                fillColor: surface,
                isDense: true,
                enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: line)),
                focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: accent)),
              ),
            ),
          ),
          const SizedBox(width: 8),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10),
            decoration: BoxDecoration(color: surface, borderRadius: BorderRadius.circular(12), border: Border.all(color: line)),
            child: DropdownButtonHideUnderline(
              child: DropdownButton<String>(
                value: _sort,
                dropdownColor: surface2,
                style: const TextStyle(fontSize: 13, color: text),
                icon: const Icon(Icons.sort, color: muted, size: 18),
                items: const [
                  DropdownMenuItem(value: 'score', child: Text('Score')),
                  DropdownMenuItem(value: 'variation', child: Text('Variation')),
                  DropdownMenuItem(value: 'volume', child: Text('Volume')),
                  DropdownMenuItem(value: 'name', child: Text('Nom')),
                ],
                onChanged: (v) {
                  if (v != null) {
                    _sort = v;
                    _load();
                  }
                },
              ),
            ),
          ),
        ]),
      ),
      Expanded(
        child: _loading
            ? const Center(child: CircularProgressIndicator(color: accent))
            : ListView.builder(
                padding: const EdgeInsets.fromLTRB(14, 6, 14, 24),
                itemCount: _stocks.length,
                itemBuilder: (c, i) => _stockRow(context, _stocks[i] as Map<String, dynamic>).enter(i),
              ),
      ),
    ]);
  }
}

Widget _stockRow(BuildContext context, Map<String, dynamic> s) {
  final trend = s['trend'];
  final tcol = trend == 'haussier' ? green : (trend == 'baissier' ? red : muted);
  final icon = trend == 'haussier' ? Icons.trending_up : (trend == 'baissier' ? Icons.trending_down : Icons.trending_flat);
  return glassCard(
    padding: const EdgeInsets.fromLTRB(14, 12, 6, 12),
    onTap: () => showStockDetail(context, s['symbol'] as String),
    child: Row(children: [
      Container(
        width: 36,
        height: 36,
        decoration: BoxDecoration(color: tcol.withOpacity(0.14), borderRadius: BorderRadius.circular(10)),
        child: Icon(icon, color: tcol, size: 20),
      ),
      const SizedBox(width: 12),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(s['symbol'] ?? '', style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 14.5)),
          Text(s['company_name'] ?? '', style: const TextStyle(color: muted, fontSize: 11.5), overflow: TextOverflow.ellipsis),
        ]),
      ),
      Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
        Text('${fmt(s['price'])}', style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w700)),
        Text('${signed(s['daily_variation'])}%', style: TextStyle(color: plc(s['daily_variation']), fontSize: 12, fontWeight: FontWeight.w600)),
      ]),
      const SizedBox(width: 10),
      Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
        badge(s['label'] ?? 'NEUTRE'),
        const SizedBox(height: 2),
        Text('${s['buy_score'] == null ? 'n/a' : (s['buy_score'] as num).round()}', style: const TextStyle(color: muted, fontSize: 11, fontWeight: FontWeight.w700)),
      ]),
      SymbolStar(symbol: '${s['symbol']}', size: 19),
    ]),
  );
}

// ---------------------------- opportunities ------------------------------- //
class OppsPage extends StatefulWidget {
  const OppsPage({super.key});
  @override
  State<OppsPage> createState() => _OppsPageState();
}

class _OppsPageState extends State<OppsPage> {
  List _opps = [];
  int _min = 0;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final d = await api('api/opportunities?min_score=$_min');
      setState(() {
        _opps = (d['opportunities'] as List?) ?? [];
        _loading = false;
      });
    } catch (_) {
      setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(14, 10, 14, 2),
        child: Row(
          children: [0, 50, 60, 70].map((m) {
            final active = _min == m;
            return Padding(
              padding: const EdgeInsets.only(right: 8),
              child: GestureDetector(
                onTap: () {
                  _min = m;
                  _load();
                },
                child: AnimatedContainer(
                  duration: 180.ms,
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                  decoration: BoxDecoration(
                    color: active ? accent : surface,
                    borderRadius: BorderRadius.circular(999),
                    border: Border.all(color: active ? accent : line),
                  ),
                  child: Text(m == 0 ? 'Toutes' : '≥ $m',
                      style: TextStyle(color: active ? const Color(0xFF06283D) : muted, fontWeight: FontWeight.w700, fontSize: 12.5)),
                ),
              ),
            );
          }).toList(),
        ),
      ),
      Expanded(
        child: _loading
            ? const Center(child: CircularProgressIndicator(color: accent))
            : _opps.isEmpty
                ? const Center(child: Text('Aucune opportunité pour ce seuil.', style: TextStyle(color: muted)))
                : ListView.builder(
                    padding: const EdgeInsets.fromLTRB(14, 8, 14, 24),
                    itemCount: _opps.length,
                    itemBuilder: (c, i) => _oppCard(context, _opps[i] as Map<String, dynamic>).enter(i),
                  ),
      ),
    ]);
  }
}

Widget _oppCard(BuildContext context, Map<String, dynamic> o) {
  final reasons = ((o['reasons'] as List?) ?? []).take(2);
  final score = (o['buy_score'] as num).toDouble();
  return glassCard(
    onTap: () => showStockDetail(context, o['symbol'] as String),
    child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _scoreRing(score),
      const SizedBox(width: 14),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Text(o['symbol'] ?? '', style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 15)),
            const SizedBox(width: 8),
            badge(o['label'] ?? 'NEUTRE'),
            const Spacer(),
            Text('${signed(o['daily_variation'])}%', style: TextStyle(color: plc(o['daily_variation']), fontSize: 12, fontWeight: FontWeight.w600)),
            SymbolStar(symbol: '${o['symbol']}', size: 18),
          ]),
          Text(o['company_name'] ?? '', style: const TextStyle(color: muted, fontSize: 12), overflow: TextOverflow.ellipsis),
          const SizedBox(height: 6),
          ...reasons.map((r) => Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  const Text('• ', style: TextStyle(color: accent)),
                  Expanded(child: Text('$r', style: const TextStyle(color: muted, fontSize: 12))),
                ]),
              )),
        ]),
      ),
    ]),
  );
}

Widget _scoreRing(double score) => SizedBox(
      width: 52,
      height: 52,
      child: Stack(alignment: Alignment.center, children: [
        SizedBox(
          width: 52,
          height: 52,
          child: CircularProgressIndicator(
            value: (score.clamp(0, 100)) / 100,
            strokeWidth: 5,
            backgroundColor: line,
            valueColor: AlwaysStoppedAnimation(score >= 65 ? green : (score >= 50 ? accent : muted)),
          ),
        ),
        Text('${score.round()}', style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 15)),
      ]),
    );

// ------------------------------ analyse IA -------------------------------- //
const _horizonLabels = {'short': 'Court terme', 'medium': 'Moyen terme', 'long': 'Long terme'};

Color recColor(String key) => switch (key) {
      'STRONG_OPPORTUNITY' => green,
      'WATCH' => amber,
      'HOLD' => accent,
      'TAKE_PROFIT' => amber,
      'AVOID' || 'RISKY' => red,
      _ => muted,
    };

class AnalysisPage extends StatefulWidget {
  const AnalysisPage({super.key});
  @override
  State<AnalysisPage> createState() => _AnalysisPageState();
}

class _AnalysisPageState extends State<AnalysisPage> {
  String _horizon = 'short';
  List _opps = [];
  List _holdings = [];
  List _sectors = [];
  String? _note;
  Map<String, dynamic>? _market;
  Map<String, dynamic>? _perf;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final results = await Future.wait([
        api('api/analysis/opportunities?horizon=$_horizon&min_score=0&limit=15'),
        api('api/analysis/portfolio'),
        api('api/analysis/market-summary'),
        api('api/sectors'),
        api('api/performance'),
      ]);
      final pf = results[1] as Map<String, dynamic>;
      setState(() {
        _opps = (results[0]['opportunities'] as List?) ?? [];
        _holdings = (pf['holdings'] as List?) ?? [];
        _note = pf['note'] as String?;
        _market = results[2] as Map<String, dynamic>?;
        _sectors = (results[3]['sectors'] as List?) ?? [];
        _perf = results[4] as Map<String, dynamic>?;
        _loading = false;
      });
    } catch (_) {
      setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(14, 10, 14, 2),
        child: Row(
          children: _horizonLabels.entries.map((e) {
            final active = _horizon == e.key;
            return Padding(
              padding: const EdgeInsets.only(right: 8),
              child: GestureDetector(
                onTap: () {
                  _horizon = e.key;
                  _load();
                },
                child: AnimatedContainer(
                  duration: 180.ms,
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                  decoration: BoxDecoration(
                    color: active ? accent : surface,
                    borderRadius: BorderRadius.circular(999),
                    border: Border.all(color: active ? accent : line),
                  ),
                  child: Text(e.value,
                      style: TextStyle(
                          color: active ? const Color(0xFF06283D) : muted,
                          fontWeight: FontWeight.w700,
                          fontSize: 12.5)),
                ),
              ),
            );
          }).toList(),
        ),
      ),
      Expanded(
        child: _loading
            ? const Center(child: CircularProgressIndicator(color: accent))
            : RefreshIndicator(
                onRefresh: _load,
                color: accent,
                backgroundColor: surface2,
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(14, 6, 14, 24),
                  children: [
                    if (_market != null) ...[
                      sectionTitle('Humeur du marché'),
                      _marketMoodCard(_market!).enter(0),
                    ],
                    if (_sectors.isNotEmpty) ...[
                      sectionTitle('Carte des secteurs'),
                      _sectorHeatmap(_sectors).enter(1),
                    ],
                    if (_holdings.isNotEmpty) ...[
                      sectionTitle('Santé du portefeuille'),
                      if (_note != null)
                        Padding(
                          padding: const EdgeInsets.fromLTRB(4, 0, 4, 8),
                          child: Text(_note!, style: const TextStyle(color: muted, fontSize: 12, height: 1.4)),
                        ),
                      ..._holdings.asMap().entries.map(
                          (e) => _aHoldingCard(context, e.value as Map<String, dynamic>).enter(e.key + 2)),
                    ],
                    sectionTitle('Meilleures opportunités — ${_horizonLabels[_horizon]!.toLowerCase()}'),
                    if (_opps.isEmpty)
                      glassCard(
                          child: const Text('Aucune opportunité analysable pour cet horizon.',
                              style: TextStyle(color: muted)))
                    else
                      ..._opps.asMap().entries.map((e) =>
                          _aOppCard(context, e.value as Map<String, dynamic>, _horizon).enter(e.key + 3)),
                    if (_perf != null) ...[
                      sectionTitle('Précision des prédictions'),
                      _accuracyCard(_perf!),
                    ],
                  ],
                ),
              ),
      ),
    ]);
  }
}

Widget _aHoldingCard(BuildContext context, Map<String, dynamic> h) {
  final risk = h['risk_score'] as num?;
  return glassCard(
    onTap: () => showAnalysisSheet(context, h['symbol'] as String, 'medium'),
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Text(h['symbol'] ?? '', style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 15)),
        const SizedBox(width: 8),
        badge(h['recommendation_label'] ?? '', recColor('${h['recommendation']}')),
        const Spacer(),
        if (risk != null)
          Text('Risque ${risk.round()}',
              style: TextStyle(
                  color: risk >= 60 ? red : muted, fontSize: 11, fontWeight: FontWeight.w700)),
        SymbolStar(symbol: '${h['symbol']}', size: 18),
      ]),
      const SizedBox(height: 6),
      Text(h['suggested_action'] ?? '',
          style: const TextStyle(color: muted, fontSize: 12, height: 1.4)),
    ]),
  );
}

Widget _aOppCard(BuildContext context, Map<String, dynamic> o, String horizon) {
  final score = (o['score'] as num?)?.toDouble() ?? 0;
  return glassCard(
    onTap: () => showAnalysisSheet(context, o['symbol'] as String, horizon),
    child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _scoreRing(score),
      const SizedBox(width: 14),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Text(o['symbol'] ?? '', style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 15)),
            const SizedBox(width: 8),
            badge(o['recommendation_label'] ?? '', recColor('${o['recommendation']}')),
            const Spacer(),
            Text('Confiance ${(o['confidence'] as num?)?.round() ?? 0}',
                style: const TextStyle(color: muted, fontSize: 11, fontWeight: FontWeight.w600)),
            SymbolStar(symbol: '${o['symbol']}', size: 18),
          ]),
          Text(o['company_name'] ?? '',
              style: const TextStyle(color: muted, fontSize: 12), overflow: TextOverflow.ellipsis),
          const SizedBox(height: 6),
          if (o['top_bullish'] != null) _argLine('${o['top_bullish']}', green),
          if (o['top_bearish'] != null) _argLine('${o['top_bearish']}', red),
        ]),
      ),
    ]),
  );
}

Widget _argLine(String text, Color color) => Padding(
      padding: const EdgeInsets.only(top: 2),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('• ', style: TextStyle(color: color)),
        Expanded(child: Text(text, style: const TextStyle(color: muted, fontSize: 12, height: 1.35))),
      ]),
    );

// ---- market mood ----
Color _regimeColor(String r) => switch (r) {
      'haussier' => green,
      'baissier' => red,
      'neutre' => amber,
      _ => muted,
    };

Widget _marketMoodCard(Map<String, dynamic> m) {
  final regime = '${m['regime'] ?? 'indéterminé'}';
  final breadth = (m['breadth_above_ma50_pct'] as num?)?.toDouble();
  final adv = (m['advancers'] as num?)?.toInt() ?? 0;
  final dec = (m['decliners'] as num?)?.toInt() ?? 0;
  final total = (adv + dec) == 0 ? 1 : adv + dec;
  return glassCard(
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Container(
          width: 10,
          height: 10,
          decoration: BoxDecoration(color: _regimeColor(regime), shape: BoxShape.circle),
        ),
        const SizedBox(width: 8),
        Text(regime.toUpperCase(),
            style: TextStyle(
                fontWeight: FontWeight.w800,
                fontSize: 14,
                letterSpacing: 0.5,
                color: _regimeColor(regime))),
        const Spacer(),
        if (breadth != null)
          Text('${breadth.round()}% > MM50',
              style: const TextStyle(color: muted, fontSize: 11, fontWeight: FontWeight.w600)),
      ]),
      const SizedBox(height: 10),
      // advancers vs decliners
      ClipRRect(
        borderRadius: BorderRadius.circular(6),
        child: Row(children: [
          Expanded(flex: adv, child: Container(height: 8, color: green)),
          Expanded(flex: dec, child: Container(height: 8, color: red)),
        ]),
      ),
      const SizedBox(height: 5),
      Row(children: [
        Text('$adv en hausse',
            style: const TextStyle(color: green, fontSize: 11, fontWeight: FontWeight.w700)),
        const Spacer(),
        Text('$dec en baisse',
            style: const TextStyle(color: red, fontSize: 11, fontWeight: FontWeight.w700)),
      ]),
      const SizedBox(height: 8),
      Text('${m['summary'] ?? ''}',
          style: const TextStyle(color: muted, fontSize: 12, height: 1.45)),
      if (total == 1 && adv == 0 && dec == 0)
        const Padding(
          padding: EdgeInsets.only(top: 4),
          child: Text('Aucune variation collectée aujourd\'hui.',
              style: TextStyle(color: muted, fontSize: 11)),
        ),
    ]),
  );
}

// ---- sector heatmap ----
Widget _sectorHeatmap(List sectors) {
  final rated = sectors
      .where((s) => (s as Map)['avg_momentum_30d'] != null)
      .toList();
  if (rated.isEmpty) {
    return glassCard(
        child: const Text('Momentum sectoriel indisponible : historique encore trop court.',
            style: TextStyle(color: muted, fontSize: 12)));
  }
  return glassCard(
    child: Wrap(
      spacing: 6,
      runSpacing: 6,
      children: rated.map((s) {
        final m = s as Map<String, dynamic>;
        final mom = (m['avg_momentum_30d'] as num).toDouble();
        // Intensity scales with the size of the move, colour with its direction.
        final strength = (mom.abs() / 8).clamp(0.12, 0.85);
        final color = mom > 0.5 ? green : (mom < -0.5 ? red : muted);
        return Container(
          padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 7),
          decoration: BoxDecoration(
            color: color.withValues(alpha: strength * 0.32),
            borderRadius: BorderRadius.circular(9),
            border: Border.all(color: color.withValues(alpha: strength)),
          ),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('${m['sector']}',
                style: const TextStyle(fontSize: 10.5, fontWeight: FontWeight.w700)),
            Text('${mom >= 0 ? '+' : ''}${mom.toStringAsFixed(1)}%',
                style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800, color: color)),
          ]),
        );
      }).toList(),
    ),
  );
}

// ---- historical prediction accuracy (the learning engine, made visible) ----
Widget _accuracyCard(Map<String, dynamic> p) {
  final analysts = (p['analysts'] as Map<String, dynamic>?) ?? {};
  final evaluated = (p['total_evaluated'] as num?)?.toInt() ?? 0;
  return glassCard(
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text('${p['note'] ?? ''}',
          style: const TextStyle(color: muted, fontSize: 12, height: 1.45)),
      if (evaluated > 0) ...[
        const SizedBox(height: 10),
        ...analysts.entries.map((e) {
          final horizons = e.value as Map<String, dynamic>;
          final short = (horizons['short'] as Map<String, dynamic>?) ?? {};
          final hit = (short['hit_rate'] as num?)?.toDouble();
          final n = (short['sample_size'] as num?)?.toInt() ?? 0;
          if (hit == null || n == 0) return const SizedBox.shrink();
          return _gaugeRow(_analystNames[e.key] ?? e.key, hit * 100,
              hit >= 0.55 ? green : (hit >= 0.45 ? amber : red));
        }),
      ],
      const SizedBox(height: 6),
      Text('Méthode : ${p['method'] ?? ''}',
          style: const TextStyle(color: muted, fontSize: 10, height: 1.4)),
    ]),
  );
}

// ------------------------ AI research report sheet ------------------------- //
// Driven by /api/report/{symbol}: the full institutional note — verdict by
// horizon, thesis, bull vs bear, the analyst debate, risk radar, scenarios,
// confidence breakdown, company knowledge and the history of the thesis itself.

void showAnalysisSheet(BuildContext context, String symbol, String horizon) {
  showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: bg,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(22))),
    builder: (c) => DraggableScrollableSheet(
      initialChildSize: 0.94,
      maxChildSize: 0.97,
      expand: false,
      builder: (c, ctrl) => FutureBuilder(
        future: api('api/report/$symbol?horizon=$horizon'),
        builder: (c, snap) {
          if (snap.hasError) {
            return Center(
                child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Text('Rapport indisponible pour $symbol.',
                        style: const TextStyle(color: muted))));
          }
          if (!snap.hasData) return const Center(child: CircularProgressIndicator(color: accent));
          return _reportBody(snap.data as Map<String, dynamic>, ctrl, horizon);
        },
      ),
    ),
  );
}

Color _hCol(num? v) => v == null ? muted : (v >= 65 ? green : (v >= 50 ? accent : muted));

Color _kindColor(String kind) => switch (kind) {
      'fact' => accent,
      'inference' => amber,
      'opinion' => muted,
      _ => muted,
    };

Widget _reportBody(Map<String, dynamic> d, ScrollController ctrl, String horizon) {
  final cio = (d['cio'] as Map<String, dynamic>?) ?? {};
  final risk = (d['risk'] as Map<String, dynamic>?) ?? {};
  final analysts = (d['analysts'] as Map<String, dynamic>?) ?? {};
  final verdicts = (cio['verdicts'] as Map<String, dynamic>?) ?? {};
  final focus = (verdicts[horizon] as Map<String, dynamic>?) ?? {};
  final bull = (cio['bull_case'] as List?) ?? [];
  final bear = (cio['bear_case'] as List?) ?? [];
  final debate = (cio['debate'] as List?) ?? [];
  final contradictions = (cio['contradictions'] as List?) ?? [];
  final scenarios = (d['scenarios_by_horizon'] as Map<String, dynamic>?) ?? {};
  final knowledge = (d['knowledge'] as Map<String, dynamic>?) ?? {};
  final thesisHistory = (d['thesis_history'] as List?) ?? [];
  final dims = (risk['dimensions'] as Map<String, dynamic>?) ?? {};
  final drivers = (risk['drivers'] as List?) ?? [];
  final cached = d['cached'] == true;

  return ListView(controller: ctrl, padding: const EdgeInsets.fromLTRB(16, 10, 16, 30), children: [
    Center(
        child: Container(
            width: 40,
            height: 4,
            margin: const EdgeInsets.only(bottom: 14),
            decoration: BoxDecoration(color: line, borderRadius: BorderRadius.circular(2)))),

    // ---- header ----
    Row(children: [
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('${d['symbol']}', style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
          Text('${d['company_name'] ?? ''}${d['sector'] != null ? ' · ${d['sector']}' : ''}',
              style: const TextStyle(color: muted, fontSize: 12.5)),
        ]),
      ),
      SymbolStar(symbol: '${d['symbol']}', size: 24),
      badge('${focus['recommendation_label'] ?? ''}', recColor('${focus['recommendation']}')),
    ]),
    const SizedBox(height: 4),
    Text(cached ? 'Rapport enregistré · reproductible' : 'Rapport généré à l\'instant',
        style: const TextStyle(color: muted, fontSize: 10.5)),
    const SizedBox(height: 14),

    // ---- executive summary ----
    glassCard(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        _label('SYNTHÈSE'),
        const SizedBox(height: 6),
        Text('${cio['executive_summary'] ?? ''}',
            style: const TextStyle(fontSize: 13, height: 1.5)),
      ]),
    ),

    // ---- recommendation by horizon ----
    sectionTitle('Recommandation par horizon'),
    glassCard(
      child: Column(
        children: _horizonLabels.entries.map((e) {
          final v = (verdicts[e.key] as Map<String, dynamic>?) ?? {};
          final active = e.key == horizon;
          return Container(
            padding: const EdgeInsets.symmetric(vertical: 7),
            decoration: BoxDecoration(
              border: Border(
                  left: BorderSide(color: active ? accent : Colors.transparent, width: 2.5)),
            ),
            child: Padding(
              padding: EdgeInsets.only(left: active ? 8 : 10.5),
              child: Row(children: [
                SizedBox(
                    width: 84,
                    child: Text(e.value,
                        style: TextStyle(
                            fontSize: 12.5,
                            fontWeight: active ? FontWeight.w800 : FontWeight.w600,
                            color: active ? text : muted))),
                badge('${v['recommendation_label'] ?? '—'}', recColor('${v['recommendation']}')),
                const Spacer(),
                Text('${(v['score'] as num?)?.round() ?? 0}',
                    style: TextStyle(
                        fontWeight: FontWeight.w800,
                        fontSize: 14,
                        color: _hCol(v['score'] as num?))),
                Text('  conf ${(v['confidence'] as num?)?.round() ?? 0}',
                    style: const TextStyle(color: muted, fontSize: 11)),
              ]),
            ),
          );
        }).toList(),
      ),
    ),

    // ---- bull vs bear ----
    sectionTitle('Thèse haussière vs baissière'),
    Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Expanded(child: _casePanel('HAUSSIER', bull, green)),
      const SizedBox(width: 10),
      Expanded(child: _casePanel('BAISSIER', bear, red)),
    ]),

    // ---- the debate ----
    if (debate.isNotEmpty) ...[
      sectionTitle('Débat des analystes'),
      ...debate.take(4).map((e) => _debateCard(e as Map<String, dynamic>)),
    ] else if (contradictions.isEmpty)
      Padding(
        padding: const EdgeInsets.fromLTRB(4, 10, 4, 0),
        child: Text('Les analystes sont alignés : aucun désaccord à arbitrer.',
            style: const TextStyle(color: muted, fontSize: 12)),
      ),
    if (cio['calibration_note'] != null && '${cio['calibration_note']}'.isNotEmpty)
      Padding(
        padding: const EdgeInsets.fromLTRB(4, 8, 4, 0),
        child: Text('${cio['calibration_note']}',
            style: const TextStyle(color: muted, fontSize: 11, height: 1.4)),
      ),

    // ---- scenarios ----
    if (scenarios[horizon] != null) ...[
      sectionTitle('Scénarios — ${_horizonLabels[horizon]!.toLowerCase()}'),
      _scenarioCard(scenarios[horizon] as Map<String, dynamic>),
    ],

    // ---- risk radar ----
    sectionTitle('Radar de risque'),
    glassCard(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text('${(risk['overall_risk'] as num?)?.round() ?? 0}/100',
              style: TextStyle(
                  fontSize: 20,
                  fontWeight: FontWeight.w800,
                  color: (risk['overall_risk'] as num? ?? 0) >= 60 ? red : amber)),
          const SizedBox(width: 8),
          Text('risque global · confiance ${(risk['confidence'] as num?)?.round() ?? 0}',
              style: const TextStyle(color: muted, fontSize: 11.5)),
        ]),
        const SizedBox(height: 8),
        ...dims.entries.map((e) => _gaugeRow(
            e.key, e.value, (e.value as num? ?? 0) >= 60 ? red : ((e.value as num? ?? 0) >= 40 ? amber : green))),
        if (drivers.isNotEmpty) const SizedBox(height: 8),
        ...drivers.take(3).map((s) => _statementLine(s as Map<String, dynamic>, red)),
      ]),
    ),

    // ---- analyst breakdown (confidence + what's missing) ----
    sectionTitle('Les analystes'),
    ...analysts.entries.map((e) => _analystCard(e.key, e.value as Map<String, dynamic>)),

    // ---- what would invalidate this / what to watch ----
    if ((focus['invalidation'] as List?)?.isNotEmpty ?? false) ...[
      sectionTitle('Ce qui invaliderait cette thèse'),
      glassCard(
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: (focus['invalidation'] as List)
                  .map((t) => _argLine('$t', amber))
                  .toList())),
    ],
    if ((focus['watch_next'] as List?)?.isNotEmpty ?? false) ...[
      sectionTitle('À surveiller'),
      glassCard(
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children:
                  (focus['watch_next'] as List).map((t) => _argLine('$t', accent)).toList())),
    ],

    // ---- thesis history (investment memory) ----
    if (thesisHistory.isNotEmpty) ...[
      sectionTitle('Historique de la thèse'),
      ...thesisHistory.take(5).map((c) => _thesisChangeCard(c as Map<String, dynamic>)),
    ],

    // ---- company knowledge ----
    if (knowledge.isNotEmpty) ...[
      sectionTitle('Connaissances société'),
      ...knowledge.entries.map((e) => _knowledgeCard(e.key, e.value as List)),
    ],

    Padding(
      padding: const EdgeInsets.fromLTRB(4, 14, 4, 0),
      child: Text('${d['disclaimer'] ?? ''} · moteur ${d['engine_version'] ?? ''}',
          style: const TextStyle(color: muted, fontSize: 10.5, fontStyle: FontStyle.italic)),
    ),
  ]);
}

Widget _label(String s) => Text(s,
    style: const TextStyle(
        color: muted, fontSize: 10.5, fontWeight: FontWeight.w800, letterSpacing: 1));

Widget _casePanel(String title, List items, Color color) => Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: surface,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: color.withValues(alpha: 0.28)),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(title,
            style: TextStyle(
                color: color, fontSize: 10.5, fontWeight: FontWeight.w800, letterSpacing: 1)),
        const SizedBox(height: 6),
        if (items.isEmpty)
          const Text('—', style: TextStyle(color: muted, fontSize: 12))
        else
          ...items.take(4).map((s) => _statementLine(s as Map<String, dynamic>, color)),
      ]),
    );

/// Every statement carries its fact / inference / opinion label — the reader must
/// never have to guess which is which.
Widget _statementLine(Map<String, dynamic> s, Color color) {
  final kind = '${s['kind'] ?? 'inference'}';
  return Padding(
    padding: const EdgeInsets.only(bottom: 6),
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Container(
        padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
        decoration: BoxDecoration(
          color: _kindColor(kind).withValues(alpha: 0.14),
          borderRadius: BorderRadius.circular(4),
        ),
        child: Text(kind,
            style: TextStyle(
                color: _kindColor(kind), fontSize: 8.5, fontWeight: FontWeight.w800)),
      ),
      const SizedBox(height: 2),
      Text('${s['text'] ?? ''}',
          style: const TextStyle(fontSize: 12, height: 1.4, color: text)),
    ]),
  );
}

Widget _debateCard(Map<String, dynamic> e) {
  final unresolved = e['winner'] == 'unresolved';
  return glassCard(
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        badge(_horizonLabels['${e['horizon']}'] ?? '${e['horizon']}', accent),
        const SizedBox(width: 8),
        Expanded(
            child: Text('${e['topic'] ?? ''}',
                style: const TextStyle(color: muted, fontSize: 11),
                overflow: TextOverflow.ellipsis)),
      ]),
      const SizedBox(height: 8),
      _debateSide('${e['bull_analyst']}', '${e['bull_claim']}', green,
          e['winner'] == e['bull_analyst']),
      _debateSide('${e['bear_analyst']}', '${e['bear_claim']}', red,
          e['winner'] == e['bear_analyst']),
      const SizedBox(height: 6),
      Container(
        padding: const EdgeInsets.all(8),
        decoration: BoxDecoration(
          color: surface2,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(unresolved ? Icons.balance : Icons.gavel_rounded,
              size: 13, color: unresolved ? amber : accent),
          const SizedBox(width: 6),
          Expanded(
              child: Text('${e['resolution'] ?? ''}',
                  style: const TextStyle(fontSize: 11.5, height: 1.4, color: muted))),
        ]),
      ),
    ]),
  );
}

Widget _debateSide(String who, String claim, Color color, bool won) => Padding(
      padding: const EdgeInsets.only(bottom: 5),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Container(width: 3, height: 30, color: color.withValues(alpha: won ? 1 : 0.3)),
        const SizedBox(width: 8),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Text(who,
                  style: TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w800,
                      color: won ? color : muted)),
              if (won) ...[
                const SizedBox(width: 4),
                Icon(Icons.check_circle, size: 11, color: color),
              ],
            ]),
            Text(claim, style: const TextStyle(fontSize: 11.5, height: 1.35, color: text)),
          ]),
        ),
      ]),
    );

Widget _scenarioCard(Map<String, dynamic> s) {
  Widget row(Map<String, dynamic> sc, Color color) {
    final p = ((sc['probability'] as num?)?.toDouble() ?? 0) * 100;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          SizedBox(
              width: 92,
              child: Text('${sc['name']}',
                  style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: color))),
          Expanded(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(6),
              child: TweenAnimationBuilder<double>(
                tween: Tween(begin: 0, end: p / 100),
                duration: 550.ms,
                curve: Curves.easeOutCubic,
                builder: (c, v, _) => LinearProgressIndicator(
                    value: v, backgroundColor: surface2, color: color, minHeight: 8),
              ),
            ),
          ),
          SizedBox(
              width: 40,
              child: Text('  ${p.round()}%',
                  style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w800),
                  textAlign: TextAlign.right)),
        ]),
        Padding(
          padding: const EdgeInsets.only(left: 92, top: 2),
          child: Text('${sc['rationale'] ?? ''}',
              style: const TextStyle(color: muted, fontSize: 11, height: 1.35)),
        ),
      ]),
    );
  }

  final assumptions = (s['assumptions'] as List?) ?? [];
  return glassCard(
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      row(s['best'] as Map<String, dynamic>, green),
      row(s['base'] as Map<String, dynamic>, accent),
      row(s['worst'] as Map<String, dynamic>, red),
      if (assumptions.isNotEmpty) ...[
        const SizedBox(height: 8),
        _label('HYPOTHÈSES'),
        const SizedBox(height: 4),
        ...assumptions.take(3).map((a) => Text('• $a',
            style: const TextStyle(color: muted, fontSize: 11, height: 1.4))),
      ],
      const SizedBox(height: 6),
      Text('Probabilités estimées, jamais des certitudes.',
          style: const TextStyle(color: muted, fontSize: 10.5, fontStyle: FontStyle.italic)),
    ]),
  );
}

const _analystNames = {
  'technical': 'Technique',
  'market_structure': 'Marché & secteur',
  'news': 'Actualités',
  'historical_behaviour': 'Comportement historique',
  'fundamental': 'Fondamentaux',
  'macro': 'Macroéconomie',
  'company': 'Société',
  'portfolio': 'Portefeuille',
};

Widget _analystCard(String key, Map<String, dynamic> a) {
  final conf = (a['confidence'] as num?)?.toDouble() ?? 0;
  final missing = (a['missing_data'] as List?) ?? [];
  final unavailable = conf == 0;
  return glassCard(
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Text(_analystNames[key] ?? key,
            style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 13)),
        const Spacer(),
        Text('confiance ${conf.round()}',
            style: TextStyle(
                color: unavailable ? red : _hCol(conf),
                fontSize: 11,
                fontWeight: FontWeight.w700)),
      ]),
      const SizedBox(height: 3),
      Text('${a['headline'] ?? ''}',
          style: TextStyle(
              color: unavailable ? red : muted, fontSize: 11.5, height: 1.4)),
      if (missing.isNotEmpty) ...[
        const SizedBox(height: 6),
        _label('INFORMATION MANQUANTE'),
        const SizedBox(height: 3),
        ...missing.take(2).map((m) => Text('• $m',
            style: const TextStyle(color: muted, fontSize: 10.5, height: 1.35))),
      ],
    ]),
  );
}

Widget _thesisChangeCard(Map<String, dynamic> c) => glassCard(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          badge(_horizonLabels['${c['horizon']}'] ?? '${c['horizon']}', muted),
          const SizedBox(width: 8),
          Text('${c['from'] ?? '—'}',
              style: const TextStyle(color: muted, fontSize: 11.5, fontWeight: FontWeight.w700)),
          const Icon(Icons.arrow_forward_rounded, size: 13, color: muted),
          Text('${c['to'] ?? ''}',
              style: TextStyle(
                  color: recColor('${c['to']}'),
                  fontSize: 11.5,
                  fontWeight: FontWeight.w800)),
          const Spacer(),
          Text('${'${c['changed_at'] ?? ''}'.split('T').first}',
              style: const TextStyle(color: muted, fontSize: 10)),
        ]),
        const SizedBox(height: 5),
        Text('${c['reason'] ?? ''}',
            style: const TextStyle(fontSize: 11.5, height: 1.4, color: text)),
        ...((c['new_evidence'] as List?) ?? [])
            .take(2)
            .map((e) => _argLine('$e', amber)),
      ]),
    );

Widget _knowledgeCard(String category, List facts) => glassCard(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        _label(category.toUpperCase().replaceAll('_', ' ')),
        const SizedBox(height: 5),
        ...facts.take(4).map((f) {
          final m = f as Map<String, dynamic>;
          return Padding(
            padding: const EdgeInsets.only(bottom: 3),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              SizedBox(
                  width: 108,
                  child: Text('${m['key']}',
                      style: const TextStyle(color: muted, fontSize: 11))),
              Expanded(
                  child: Text('${m['value']}',
                      style: const TextStyle(fontSize: 11.5, height: 1.35),
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis)),
            ]),
          );
        }),
      ]),
    );

Widget _gaugeRow(String label, dynamic value, Color color) {
  final v = (value as num?)?.toDouble() ?? 0;
  return Padding(
    padding: const EdgeInsets.symmetric(vertical: 4),
    child: Row(children: [
      SizedBox(width: 88, child: Text(label, style: const TextStyle(fontSize: 12, color: muted))),
      Expanded(
        child: ClipRRect(
          borderRadius: BorderRadius.circular(6),
          child: TweenAnimationBuilder<double>(
            tween: Tween(begin: 0, end: (v.clamp(0, 100)) / 100),
            duration: 500.ms,
            curve: Curves.easeOutCubic,
            builder: (c, val, _) =>
                LinearProgressIndicator(value: val, backgroundColor: surface2, color: color, minHeight: 8),
          ),
        ),
      ),
      SizedBox(
          width: 36,
          child: Text('  ${v.round()}',
              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700),
              textAlign: TextAlign.right)),
    ]),
  );
}

// ------------------------------- news ------------------------------------- //
class NewsPage extends StatefulWidget {
  const NewsPage({super.key});
  @override
  State<NewsPage> createState() => _NewsPageState();
}

class _NewsPageState extends State<NewsPage> {
  List _news = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final d = await api('api/news');
      setState(() {
        _news = (d['news'] as List?) ?? [];
        _loading = false;
      });
    } catch (_) {
      setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator(color: accent));
    if (_news.isEmpty) {
      return const Center(child: Text('Aucune actualité pour le moment.', style: TextStyle(color: muted)));
    }
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(14, 10, 14, 24),
      itemCount: _news.length,
      itemBuilder: (c, i) {
        final n = _news[i] as Map<String, dynamic>;
        final symbol = n['symbol'];
        return glassCard(
          onTap: () {
            final url = n['url'];
            if (url is String) html.window.open(url, '_blank');
          },
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(n['title'] ?? '', style: const TextStyle(fontWeight: FontWeight.w600, height: 1.3)),
            const SizedBox(height: 8),
            Row(children: [
              const Icon(Icons.public, size: 13, color: muted),
              const SizedBox(width: 5),
              Expanded(child: Text('${n['source'] ?? ''}${symbol != null ? '  ·  $symbol' : ''}', style: const TextStyle(color: muted, fontSize: 11.5), overflow: TextOverflow.ellipsis)),
              // Only when the item was actually linked to a listed stock — most
              // official notices are, but some are market-wide and have no symbol.
              if (symbol != null) SymbolStar(symbol: '$symbol', size: 17),
              const Icon(Icons.open_in_new, size: 13, color: muted),
            ]),
          ]),
        ).enter(i);
      },
    );
  }
}

// ---------------------------- notifications ------------------------------- //
class NotificationsPage extends StatefulWidget {
  const NotificationsPage({super.key});
  @override
  State<NotificationsPage> createState() => _NotificationsPageState();
}

class _NotificationsPageState extends State<NotificationsPage> {
  List _items = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final d = await api('api/notifications');
      setState(() {
        _items = (d['notifications'] as List?) ?? [];
        _loading = false;
      });
    } catch (_) {
      setState(() => _loading = false);
    }
  }

  IconData _icon(String kind) => switch (kind) {
        'digest' => Icons.summarize_outlined,
        'intraday' => Icons.update,
        'test' => Icons.check_circle_outline,
        'urgent' => Icons.warning_amber_rounded,
        'analysis' => Icons.psychology_outlined,
        _ => Icons.notifications_outlined,
      };

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator(color: accent));
    if (_items.isEmpty) {
      return RefreshIndicator(
        onRefresh: _load,
        color: accent,
        backgroundColor: surface2,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          children: const [
            SizedBox(height: 120),
            Icon(Icons.notifications_off_outlined, size: 44, color: muted),
            SizedBox(height: 14),
            Padding(
              padding: EdgeInsets.symmetric(horizontal: 36),
              child: Text(
                "Aucune notification pour l'instant.\nElles arrivent à 9h · 11h · 13h · 15h · 17h (jours ouvrés) — ou appuie sur « Actualiser » dans Portefeuille pour en générer une tout de suite.",
                textAlign: TextAlign.center,
                style: TextStyle(color: muted, height: 1.5),
              ),
            ),
          ],
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: _load,
      color: accent,
      backgroundColor: surface2,
      child: ListView.builder(
        padding: const EdgeInsets.fromLTRB(14, 10, 14, 24),
        itemCount: _items.length,
        itemBuilder: (c, i) {
          final n = _items[i] as Map<String, dynamic>;
          final when = _fmtDate(DateTime.tryParse('${n['created_at']}')?.toLocal());
          return glassCard(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Icon(_icon('${n['kind']}'), size: 18, color: accent),
                const SizedBox(width: 8),
                Expanded(child: Text(n['title'] ?? '', style: const TextStyle(fontWeight: FontWeight.w700))),
                Text(when, style: const TextStyle(color: muted, fontSize: 11)),
              ]),
              const Divider(color: line, height: 18),
              Text(n['body'] ?? '', style: const TextStyle(fontSize: 13, height: 1.5)),
            ]),
          ).enter(i);
        },
      ),
    );
  }
}

String _fmtDate(DateTime? d) {
  if (d == null) return '';
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(d.day)}/${two(d.month)} ${two(d.hour)}:${two(d.minute)}';
}

// --------------------------- stock detail sheet --------------------------- //
void showStockDetail(BuildContext context, String symbol) {
  showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: bg,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(22))),
    builder: (c) => DraggableScrollableSheet(
      initialChildSize: 0.92,
      maxChildSize: 0.96,
      expand: false,
      builder: (c, ctrl) => FutureBuilder(
        future: api('api/stock/$symbol'),
        builder: (c, snap) {
          if (!snap.hasData) return const Center(child: CircularProgressIndicator(color: accent));
          final d = snap.data as Map<String, dynamic>;
          return _detailBody(d, ctrl);
        },
      ),
    ),
  );
}

Widget _detailBody(Map<String, dynamic> d, ScrollController ctrl) {
  final s = d['score'] as Map<String, dynamic>?;
  final mom = d['momentum'] as Map<String, dynamic>? ?? {};
  final ma = d['moving_averages'] as Map<String, dynamic>? ?? {};
  final history = (d['history'] as List?) ?? [];
  return ListView(controller: ctrl, padding: const EdgeInsets.fromLTRB(16, 10, 16, 28), children: [
    Center(child: Container(width: 40, height: 4, margin: const EdgeInsets.only(bottom: 14), decoration: BoxDecoration(color: line, borderRadius: BorderRadius.circular(2)))),
    Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('${d['symbol']}', style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
          Text('${d['company_name'] ?? ''}', style: const TextStyle(color: muted, fontSize: 13)),
        ]),
      ),
      SymbolStar(symbol: '${d['symbol']}', size: 24),
      if (s != null) badge(s['label'] ?? 'NEUTRE'),
    ]),
    const SizedBox(height: 12),
    Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
      Text('${fmt(d['price'])}', style: const TextStyle(fontSize: 30, fontWeight: FontWeight.w800, letterSpacing: -1)),
      const SizedBox(width: 6),
      const Padding(padding: EdgeInsets.only(bottom: 5), child: Text('MAD', style: TextStyle(color: muted, fontSize: 13))),
      const Spacer(),
      Text('${signed(d['daily_variation'])}% aujourd\'hui', style: TextStyle(color: plc(d['daily_variation']), fontWeight: FontWeight.w700)),
    ]),
    const SizedBox(height: 14),
    glassCard(child: _priceChart(history)),
    if (s != null) ...[
      sectionTitle("Score d'opportunité"),
      glassCard(
        child: Column(children: [
          Row(mainAxisAlignment: MainAxisAlignment.spaceAround, children: [
            _scoreCol('Acheter', s['buy'], green),
            _scoreCol('Surveiller', s['watch'], amber),
            _scoreCol('Éviter', s['avoid'], red),
          ]),
          const SizedBox(height: 12),
          ...((s['components'] as Map?)?.entries ?? []).map((e) => _bar(e.key as String, e.value)),
        ]),
      ),
      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Expanded(child: _listCard('Atouts', (s['reasons'] as List?) ?? [], muted)),
        const SizedBox(width: 10),
        Expanded(child: _listCard('Risques', (s['risks'] as List?) ?? [], amber)),
      ]),
    ],
    sectionTitle('Indicateurs techniques'),
    glassCard(
      child: Column(children: [
        _mrow('Momentum 5j', mom['d5'], '%'),
        _mrow('Momentum 30j', mom['d30'], '%'),
        _mrow('Momentum 90j', mom['d90'], '%'),
        _mrow('MM20', ma['ma20']),
        _mrow('MM50', ma['ma50']),
        _mrow('MM200', ma['ma200']),
        _mrow('Volatilité 30j', d['volatility_30d'], '%'),
        _mrow('Volume vs moy.', d['volume_anomaly'], '×'),
        _mrow('Support', d['support']),
        _mrow('Résistance', d['resistance']),
        _mrow('+ haut 52 sem.', d['week52_high']),
        _mrow('+ bas 52 sem.', d['week52_low'], '', true),
      ]),
    ),
  ]);
}

Widget _priceChart(List history) {
  final pts = history.where((h) => h['p'] != null).toList();
  if (pts.length < 2) {
    return const SizedBox(height: 60, child: Center(child: Text("Pas encore assez d'historique pour un graphique.", style: TextStyle(color: muted, fontSize: 12))));
  }
  final spots = <FlSpot>[];
  for (var i = 0; i < pts.length; i++) {
    spots.add(FlSpot(i.toDouble(), (pts[i]['p'] as num).toDouble()));
  }
  final ys = spots.map((s) => s.y).toList();
  final minY = ys.reduce(math.min), maxY = ys.reduce(math.max);
  final pad = (maxY - minY) * 0.12 + 0.0001;
  final up = spots.last.y >= spots.first.y;
  final col = up ? green : red;
  return SizedBox(
    height: 150,
    child: LineChart(LineChartData(
      minY: minY - pad,
      maxY: maxY + pad,
      gridData: FlGridData(show: false),
      titlesData: FlTitlesData(show: false),
      borderData: FlBorderData(show: false),
      lineTouchData: LineTouchData(enabled: false),
      lineBarsData: [
        LineChartBarData(
          spots: spots,
          isCurved: true,
          color: col,
          barWidth: 2.5,
          dotData: FlDotData(show: false),
          belowBarData: BarAreaData(
            show: true,
            gradient: LinearGradient(
              colors: [col.withOpacity(0.28), col.withOpacity(0.0)],
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
            ),
          ),
        ),
      ],
    )),
  );
}

Widget _scoreCol(String label, dynamic v, Color col) => Column(children: [
      Text(label, style: const TextStyle(color: muted, fontSize: 12)),
      const SizedBox(height: 2),
      Text('${(v as num?)?.round() ?? 0}', style: TextStyle(fontSize: 24, fontWeight: FontWeight.w800, color: col)),
    ]);

const _compLabels = {
  'momentum': 'Momentum',
  'volume_anomaly': 'Volume',
  'valuation_opportunity': 'Valorisation',
  'support_proximity': 'Support',
  'sector_strength': 'Secteur',
  'news_sentiment': 'Actus',
};

Widget _bar(String key, dynamic value) {
  final v = (value as num?)?.toDouble() ?? 0;
  return Padding(
    padding: const EdgeInsets.symmetric(vertical: 4),
    child: Row(children: [
      SizedBox(width: 92, child: Text(_compLabels[key] ?? key, style: const TextStyle(fontSize: 12.5, color: muted))),
      Expanded(
        child: ClipRRect(
          borderRadius: BorderRadius.circular(6),
          child: TweenAnimationBuilder<double>(
            tween: Tween(begin: 0, end: (v.clamp(0, 100)) / 100),
            duration: 500.ms,
            curve: Curves.easeOutCubic,
            builder: (c, val, _) => LinearProgressIndicator(value: val, backgroundColor: surface2, color: accent, minHeight: 8),
          ),
        ),
      ),
      SizedBox(width: 34, child: Text('  ${v.round()}', style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700), textAlign: TextAlign.right)),
    ]),
  );
}

Widget _listCard(String title, List items, Color color) => Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(color: surface, borderRadius: BorderRadius.circular(16), border: Border.all(color: line)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(title, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13)),
        const SizedBox(height: 6),
        ...items.map((r) => Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text('• $r', style: TextStyle(color: color, fontSize: 12, height: 1.3)),
            )),
      ]),
    );

Widget _mrow(String label, dynamic value, [String suffix = '', bool last = false]) => Container(
      padding: const EdgeInsets.symmetric(vertical: 9),
      decoration: BoxDecoration(border: last ? null : const Border(bottom: BorderSide(color: line))),
      child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Text(label, style: const TextStyle(color: muted, fontSize: 13)),
        Text(value == null ? 'n/a' : '${fmt(value)}$suffix', style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700)),
      ]),
    );

class _Skeleton extends StatelessWidget {
  const _Skeleton();
  @override
  Widget build(BuildContext context) => Column(
        children: List.generate(
          3,
          (i) => Container(
            height: 14,
            margin: const EdgeInsets.symmetric(vertical: 6),
            decoration: BoxDecoration(color: surface2, borderRadius: BorderRadius.circular(6)),
          ).animate(onPlay: (c) => c.repeat()).shimmer(duration: 1200.ms, color: line),
        ),
      );
}
