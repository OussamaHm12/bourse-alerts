import 'dart:convert';
import 'dart:html' as html;
import 'dart:js_interop';

import 'package:flutter/material.dart';

// --- minimal JS bridge to the proven web-push code living in web/push.js ---
@JS('appEnablePush')
external JSPromise<JSString> appEnablePush();
@JS('appTestPush')
external JSPromise<JSString> appTestPush();
@JS('appRunNow')
external JSPromise<JSString> appRunNow();

// ------------------------------- theme ------------------------------------ //
const bg = Color(0xFF0F172A);
const card = Color(0xFF1E293B);
const line = Color(0xFF334155);
const muted = Color(0xFF94A3B8);
const accent = Color(0xFF38BDF8);
const green = Color(0xFF22C55E);
const red = Color(0xFFEF4444);
const amber = Color(0xFFFACC15);

void main() => runApp(const BourseApp());

class BourseApp extends StatelessWidget {
  const BourseApp({super.key});
  @override
  Widget build(BuildContext context) {
    final base = ThemeData.dark(useMaterial3: true);
    return MaterialApp(
      title: 'Bourse Casablanca',
      debugShowCheckedModeBanner: false,
      theme: base.copyWith(
        scaffoldBackgroundColor: bg,
        colorScheme: base.colorScheme.copyWith(primary: accent, surface: card),
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
  return v.toStringAsFixed(d).replaceAllMapped(
      RegExp(r'\B(?=(\d{3})+(?!\d))'), (m) => ' ');
}

String signed(num? v, [int d = 2]) =>
    v == null ? 'n/a' : (v >= 0 ? '+' : '') + fmt(v, d);

Color plc(num? v) => (v ?? 0) >= 0 ? green : red;

Color labelColor(String label) {
  switch (label) {
    case 'ACHETER':
      return green;
    case 'ÉVITER':
      return red;
    case 'SURVEILLER':
      return amber;
    default:
      return muted;
  }
}

Widget badge(String label) => Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: labelColor(label).withOpacity(0.15),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: labelColor(label)),
      ),
      child: Text(label,
          style: TextStyle(
              color: labelColor(label), fontSize: 11, fontWeight: FontWeight.w700)),
    );

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
      appBar: AppBar(
        backgroundColor: bg,
        title: Row(children: [
          Container(
              width: 10,
              height: 10,
              decoration: const BoxDecoration(color: green, shape: BoxShape.circle)),
          const SizedBox(width: 8),
          const Text('Bourse Casablanca', style: TextStyle(fontSize: 18)),
        ]),
      ),
      body: SafeArea(child: IndexedStack(index: _idx, children: _pages)),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _idx,
        onDestinationSelected: (i) => setState(() => _idx = i),
        backgroundColor: card,
        destinations: const [
          NavigationDestination(icon: Icon(Icons.account_balance_wallet), label: 'Portefeuille'),
          NavigationDestination(icon: Icon(Icons.bar_chart), label: 'Marché'),
          NavigationDestination(icon: Icon(Icons.track_changes), label: 'Opportunités'),
          NavigationDestination(icon: Icon(Icons.article), label: 'Actus'),
        ],
      ),
    );
  }
}

// --------------------------- shared building blocks ----------------------- //
Widget sectionTitle(String t) => Padding(
      padding: const EdgeInsets.fromLTRB(4, 12, 4, 8),
      child: Text(t.toUpperCase(),
          style: const TextStyle(
              color: muted, fontSize: 13, letterSpacing: 1, fontWeight: FontWeight.w600)),
    );

Widget cardBox({required Widget child, VoidCallback? onTap}) => Card(
      color: card,
      margin: const EdgeInsets.only(bottom: 10),
      shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14), side: const BorderSide(color: line)),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(14),
        child: Padding(padding: const EdgeInsets.all(14), child: child),
      ),
    );

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
      child: ListView(
        padding: const EdgeInsets.all(12),
        children: [
          cardBox(
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    const Text('Notifications', style: TextStyle(fontWeight: FontWeight.w700)),
                    Text(_notif, style: const TextStyle(color: muted, fontSize: 12)),
                  ]),
                ),
                Column(children: [
                  FilledButton(
                      onPressed: () async {
                        final r = await appEnablePush().toDart;
                        setState(() => _notif = r.toDart);
                      },
                      child: const Text('Activer')),
                  TextButton(
                      onPressed: () async {
                        final r = await appTestPush().toDart;
                        setState(() => _notif = r.toDart);
                      },
                      child: const Text('Tester')),
                ]),
              ]),
              const SizedBox(height: 8),
              OutlinedButton.icon(
                onPressed: _runNow,
                icon: const Icon(Icons.refresh),
                label: const Text('Lancer une mise à jour maintenant'),
              ),
            ]),
          ),
          sectionTitle('Mon portefeuille'),
          if (_error != null)
            cardBox(child: Text('Erreur : $_error', style: const TextStyle(color: red)))
          else if (p == null)
            cardBox(child: const Text('Chargement…', style: TextStyle(color: muted)))
          else if (holdings.isEmpty)
            cardBox(
                child: const Text('Aucune position. Renseignez PORTFOLIO_JSON côté serveur.',
                    style: TextStyle(color: muted)))
          else ...[
            cardBox(
              child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  const Text('Valeur', style: TextStyle(color: muted, fontSize: 12)),
                  Text('${fmt(p['total_value'], 0)} MAD',
                      style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w700)),
                ]),
                Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
                  Text('P/L net (${fmt((p['fee_rate'] ?? 0) * 100, 2)}%)',
                      style: const TextStyle(color: muted, fontSize: 12)),
                  Text(signed(p['total_net_pl'], 0),
                      style: TextStyle(
                          fontSize: 22,
                          fontWeight: FontWeight.w700,
                          color: plc(p['total_net_pl']))),
                  Text('${signed(p['total_pl_pct'], 1)}%',
                      style: TextStyle(color: plc(p['total_pl_pct']))),
                ]),
              ]),
            ),
            ...holdings.map((h) => _holdingCard(context, h as Map<String, dynamic>)),
          ],
        ],
      ),
    );
  }
}

Widget _holdingCard(BuildContext context, Map<String, dynamic> h) {
  final sell = h['advice'] == 'SELL';
  return cardBox(
    onTap: () => showStockDetail(context, h['symbol'] as String),
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Expanded(
          child: Row(children: [
            Text(h['symbol'] ?? '', style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
            const SizedBox(width: 6),
            Expanded(
                child: Text(h['company_name'] ?? '',
                    style: const TextStyle(color: muted, fontSize: 12), overflow: TextOverflow.ellipsis)),
          ]),
        ),
        badge(sell ? 'ÉVITER' : 'ACHETER'),
      ]),
      const SizedBox(height: 6),
      Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Text('${fmt(h['quantity'], 0)} × ${fmt(h['current_price'])} = ${fmt(h['market_value'], 0)} MAD',
            style: const TextStyle(color: muted, fontSize: 13)),
        Text('${signed(h['net_pl'], 0)} (${signed(h['net_pl_pct'], 1)}%)',
            style: TextStyle(color: plc(h['net_pl']), fontSize: 13)),
      ]),
      const SizedBox(height: 4),
      Text('Acheté @ ${fmt(h['buy_price'])} — ${h['advice_reason'] ?? ''}',
          style: const TextStyle(color: muted, fontSize: 12)),
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
        padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
        child: Row(children: [
          Expanded(
            child: TextField(
              onChanged: (v) {
                _q = v.trim();
                _load();
              },
              decoration: const InputDecoration(
                hintText: 'Rechercher une action…',
                filled: true,
                fillColor: card,
                border: OutlineInputBorder(),
                isDense: true,
              ),
            ),
          ),
          const SizedBox(width: 8),
          DropdownButton<String>(
            value: _sort,
            dropdownColor: card,
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
        ]),
      ),
      Expanded(
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : ListView.builder(
                padding: const EdgeInsets.all(12),
                itemCount: _stocks.length,
                itemBuilder: (c, i) => _stockRow(context, _stocks[i] as Map<String, dynamic>),
              ),
      ),
    ]);
  }
}

Widget _stockRow(BuildContext context, Map<String, dynamic> s) {
  final trend = s['trend'];
  final arrow = trend == 'haussier' ? '▲' : (trend == 'baissier' ? '▼' : '•');
  final tcol = trend == 'haussier' ? green : (trend == 'baissier' ? red : muted);
  return cardBox(
    onTap: () => showStockDetail(context, s['symbol'] as String),
    child: Row(children: [
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Text(s['symbol'] ?? '', style: const TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(width: 6),
            Text(arrow, style: TextStyle(color: tcol, fontSize: 12)),
          ]),
          Text(s['company_name'] ?? '',
              style: const TextStyle(color: muted, fontSize: 12), overflow: TextOverflow.ellipsis),
        ]),
      ),
      Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
        Text('${fmt(s['price'])}', style: const TextStyle(fontSize: 13)),
        Text('${signed(s['daily_variation'])}%',
            style: TextStyle(color: plc(s['daily_variation']), fontSize: 12)),
      ]),
      const SizedBox(width: 10),
      Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
        badge(s['label'] ?? 'NEUTRE'),
        Text('score ${s['buy_score'] == null ? 'n/a' : (s['buy_score'] as num).round()}',
            style: const TextStyle(color: muted, fontSize: 11)),
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
        padding: const EdgeInsets.fromLTRB(12, 12, 12, 0),
        child: Row(
          children: [0, 50, 60, 70].map((m) {
            return Padding(
              padding: const EdgeInsets.only(right: 8),
              child: ChoiceChip(
                label: Text(m == 0 ? 'Toutes' : '≥ $m'),
                selected: _min == m,
                onSelected: (_) {
                  _min = m;
                  _load();
                },
              ),
            );
          }).toList(),
        ),
      ),
      Expanded(
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : ListView.builder(
                padding: const EdgeInsets.all(12),
                itemCount: _opps.length,
                itemBuilder: (c, i) {
                  final o = _opps[i] as Map<String, dynamic>;
                  final reasons = ((o['reasons'] as List?) ?? []).take(2);
                  return cardBox(
                    onTap: () => showStockDetail(context, o['symbol'] as String),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
                        Expanded(
                          child: Text('${o['symbol']} · ${o['company_name'] ?? ''}',
                              style: const TextStyle(fontWeight: FontWeight.w700),
                              overflow: TextOverflow.ellipsis),
                        ),
                        Text('${(o['buy_score'] as num).round()}/100',
                            style: const TextStyle(
                                color: accent, fontSize: 20, fontWeight: FontWeight.w800)),
                      ]),
                      const SizedBox(height: 4),
                      Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
                        badge(o['label'] ?? 'NEUTRE'),
                        Text('${signed(o['daily_variation'])}%',
                            style: TextStyle(color: plc(o['daily_variation']))),
                      ]),
                      const SizedBox(height: 6),
                      ...reasons.map((r) => Text('• $r',
                          style: const TextStyle(color: muted, fontSize: 12))),
                    ]),
                  );
                },
              ),
      ),
    ]);
  }
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
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_news.isEmpty) {
      return const Center(
          child: Text('Aucune actualité pour le moment.', style: TextStyle(color: muted)));
    }
    return ListView.builder(
      padding: const EdgeInsets.all(12),
      itemCount: _news.length,
      itemBuilder: (c, i) {
        final n = _news[i] as Map<String, dynamic>;
        return cardBox(
          onTap: () {
            final url = n['url'];
            if (url is String) html.window.open(url, '_blank');
          },
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(n['title'] ?? '', style: const TextStyle(fontWeight: FontWeight.w600)),
            const SizedBox(height: 4),
            Text('${n['source'] ?? ''} ${n['symbol'] != null ? '· ${n['symbol']}' : ''}',
                style: const TextStyle(color: muted, fontSize: 12)),
          ]),
        );
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
    builder: (c) => DraggableScrollableSheet(
      initialChildSize: 0.9,
      maxChildSize: 0.95,
      expand: false,
      builder: (c, ctrl) => FutureBuilder(
        future: api('api/stock/$symbol'),
        builder: (c, snap) {
          if (!snap.hasData) return const Center(child: CircularProgressIndicator());
          final d = snap.data as Map<String, dynamic>;
          final s = d['score'] as Map<String, dynamic>?;
          final mom = d['momentum'] as Map<String, dynamic>? ?? {};
          final ma = d['moving_averages'] as Map<String, dynamic>? ?? {};
          return ListView(controller: ctrl, padding: const EdgeInsets.all(16), children: [
            Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
              Expanded(
                child: Text('${d['symbol']} · ${d['company_name'] ?? ''}',
                    style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
              ),
              if (s != null) badge(s['label'] ?? 'NEUTRE'),
            ]),
            const SizedBox(height: 8),
            Text('${fmt(d['price'])} MAD',
                style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w700)),
            Text('${signed(d['daily_variation'])}% aujourd\'hui',
                style: TextStyle(color: plc(d['daily_variation']))),
            if (s != null) ...[
              sectionTitle("Score d'opportunité"),
              cardBox(
                child: Column(children: [
                  Row(mainAxisAlignment: MainAxisAlignment.spaceAround, children: [
                    _scoreCol('Acheter', s['buy'], green),
                    _scoreCol('Surveiller', s['watch'], muted),
                    _scoreCol('Éviter', s['avoid'], red),
                  ]),
                  const SizedBox(height: 8),
                  ...((s['components'] as Map?)?.entries ?? [])
                      .map((e) => _bar(e.key as String, e.value)),
                ]),
              ),
              sectionTitle('Atouts'),
              cardBox(
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: ((s['reasons'] as List?) ?? [])
                          .map((r) => Text('• $r', style: const TextStyle(color: muted)))
                          .toList())),
              sectionTitle('Risques'),
              cardBox(
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: ((s['risks'] as List?) ?? [])
                          .map((r) => Text('• $r', style: const TextStyle(color: amber)))
                          .toList())),
            ],
            sectionTitle('Indicateurs techniques'),
            cardBox(
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
                _mrow('+ bas 52 sem.', d['week52_low']),
              ]),
            ),
          ]);
        },
      ),
    ),
  );
}

Widget _scoreCol(String label, dynamic v, Color col) => Column(children: [
      Text(label, style: const TextStyle(color: muted, fontSize: 12)),
      Text('${(v as num?)?.round() ?? 0}',
          style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700, color: col)),
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
    padding: const EdgeInsets.symmetric(vertical: 3),
    child: Row(children: [
      SizedBox(width: 90, child: Text(_compLabels[key] ?? key, style: const TextStyle(fontSize: 12))),
      Expanded(
        child: ClipRRect(
          borderRadius: BorderRadius.circular(6),
          child: LinearProgressIndicator(
              value: (v.clamp(0, 100)) / 100, backgroundColor: line, color: accent, minHeight: 8),
        ),
      ),
      SizedBox(width: 36, child: Text('  ${v.round()}', style: const TextStyle(fontSize: 12))),
    ]),
  );
}

Widget _mrow(String label, dynamic value, [String suffix = '']) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Text(label, style: const TextStyle(color: muted, fontSize: 13)),
        Text(value == null ? 'n/a' : '${fmt(value)}$suffix',
            style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
      ]),
    );
