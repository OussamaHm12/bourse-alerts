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

Widget badge(String label) {
  final c = labelColor(label);
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
  final _pages = const [PortfolioPage(), MarketPage(), OppsPage(), NewsPage()];

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
          onDestinationSelected: (i) => setState(() => _idx = i),
          destinations: const [
            NavigationDestination(icon: Icon(Icons.account_balance_wallet_outlined), selectedIcon: Icon(Icons.account_balance_wallet), label: 'Portefeuille'),
            NavigationDestination(icon: Icon(Icons.show_chart_outlined), selectedIcon: Icon(Icons.show_chart), label: 'Marché'),
            NavigationDestination(icon: Icon(Icons.bolt_outlined), selectedIcon: Icon(Icons.bolt), label: 'Opportunités'),
            NavigationDestination(icon: Icon(Icons.article_outlined), selectedIcon: Icon(Icons.article), label: 'Actus'),
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
    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
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
              Expanded(child: Text('${n['source'] ?? ''}${n['symbol'] != null ? '  ·  ${n['symbol']}' : ''}', style: const TextStyle(color: muted, fontSize: 11.5), overflow: TextOverflow.ellipsis)),
              const Icon(Icons.open_in_new, size: 13, color: muted),
            ]),
          ]),
        ).enter(i);
      },
    );
  }
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
