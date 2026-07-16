# Audit technique — Moroccan Stock Intelligence Platform

> **Audit en lecture seule.** Aucune modification de code n'a été effectuée pour produire ce document.
> État réel de la plateforme au **16 juillet 2026**, établi exclusivement à partir du code, des tests,
> de la structure du dépôt et de la documentation — vérifié **par exécution**, pas par lecture.

| | |
|---|---|
| **Branche** | `main` |
| **Commit** | `3bcf040` |
| **Backend** | 11 940 lignes Python (60 modules) |
| **Tests** | 226 cas — 226 verts (20 s) |
| **Moteur de recherche** | `ENGINE_VERSION = 2.0` |
| **Hôte de production** | Railway — conteneur Docker unique, SQLite sur volume |

### Suivi des corrections

Les constats ci-dessous sont un **instantané daté du 16 juillet 2026** et ne sont pas réécrits — ils
restent la trace de ce qui a été trouvé. Ce tableau suit ce qui a été corrigé depuis.

| Constat | Section | Statut |
|---|---|---|
| Aucune sauvegarde de la base | §10, §13 | **Corrigé** (2026-07-16) — job `database_backup` quotidien 22:00 : snapshot par l'API de backup en ligne SQLite, `PRAGMA integrity_check` sur la copie, gzip (~9×), envoi hors-hôte Telegram, rotation 7 jours. Commande `cli backup` en garde-fou avant toute opération destructive. 25 tests. |
| Double chemin Telegram sur deux bases | §10, §13 | **Corrigé** (2026-07-16) — `.github/workflows/stock-alert.yml` supprimé. Le service déployé est le seul émetteur. Test de non-régression : aucun workflow ne peut porter `TELEGRAM_BOT_TOKEN`. |
| Scheduler non testé | §12 | **Partiellement corrigé** (2026-07-16) — `tests/test_scheduler_jobs.py` couvre l'enregistrement des jobs et le comportement du backup. La logique des autres jobs reste non testée. |
| Backfill news non appliqué | §5 | **Corrigé** (2026-07-16) — appliqué localement, conforme au dry-run, idempotence vérifiée. **Reste à appliquer en production** (`railway ssh`). |
| Poids news mort dans le moteur A | §4, §5 | **Corrigé** (2026-07-16) — `news_sentiment_score` branché dans `compute_state`. Coût mesuré : 0.3 ms (0.08 % de `compute_state`). Impact réel : 1 titre sur 80 bouge (−0.44), aucune étiquette ne change. |
| `NewsContext` dupliqué | §5, §11 | **Corrigé** (2026-07-16) — un seul builder dans `services/news_context.py`. |
| Inversion de dépendance (`views.compute_state`) | §2, §11 | **Corrigé** (2026-07-16) — `compute_state` déplacé dans `services/market_state.py`. Test de non-régression : aucune couche de calcul n'importe `views`. |
| `signals` écrite jamais lue | §7, §11 | **Corrigé** (2026-07-16) — `generate_alerts`, `store_signal`, `build_event_message` et le modèle `Signal` supprimés. **La table `signals` subsiste en base** (`create_all` ne supprime jamais) : à retirer par la première migration Alembic. |
| Dashboard Streamlit non déployé | §11 | **Corrigé** (2026-07-16) — supprimé, avec `config/watchlist.json`, `load_watchlist` et `WATCHLIST_FILE`. Retire ~180 Mo (streamlit + plotly) de l'image de production. |
| Deux moteurs de scoring divergents | §4 | Ouvert — étude comparative A/B avant décision. |
| Aucune authentification | §13 | Ouvert. |
| Aucune migration Alembic | §7, §10 | Ouvert. Devra aussi supprimer la table orpheline `signals`. |
| Aucun test sur la couche API | §12 | **Corrigé** (2026-07-16) — 46 tests. `api.py` 0 % → **88 %**. L'API était *intestable*, pas seulement non testée : elle ouvre un engine et lance `init_db()` à l'import. `tests/conftest.py` redirige `DATABASE_URL` avant le premier import de `config`. |
| `research/notifications` non testé | §12 | **Corrigé** (2026-07-16) — 16 tests. 0 % → **93 %**. |
| Tests sortant sur le réseau | — | **Nouveau, corrigé** (2026-07-16) — un test `/api/refresh` scrapait réellement casablanca-bourse.com (81 lignes) à chaque exécution. Garde-fou `no_outbound_network` dans `conftest.py` : tout appel `requests` échoue bruyamment. |
| Couverture de tests | §12 | **Mesurée** — 68 % à l'audit → **76 %** au 16 juillet. Restent à 0 % : `synthesis/claude.py` (LLM désactivé), `collectors/company.py`, `collectors/issuers.py`. |
| `compute_metrics` = 98.6 % du coût de `compute_state` | §13 | **Nouveau** (mesuré 2026-07-16) — 367 ms pour 400 lignes de prix, sur 6 endpoints à chaque requête. À investiguer au chantier perfs. |
| Documentation obsolète | §11 | **Corrigé** (2026-07-16) — README (arborescence, Streamlit, horaires, notifications, sauvegardes) et HANDOVER alignés sur le code réel. Reste le statut de `ARCHITECTURE_AI_ANALYST.md`. |

### Sommaire

1. [Vue d'ensemble](#1-vue-densemble)
2. [Modules et composants](#2-modules-et-composants)
3. [Pipeline complet](#3-pipeline-complet)
4. [Moteurs de scoring](#4-moteurs-de-scoring)
5. [Système de news](#5-système-de-news)
6. [Multi-agents IA](#6-multi-agents-ia)
7. [Base de données](#7-base-de-données)
8. [API](#8-api)
9. [Frontend Flutter / PWA](#9-frontend-flutter--pwa)
10. [Infrastructure](#10-infrastructure)
11. [État actuel du projet](#11-état-actuel-du-projet)
12. [Tests](#12-tests)
13. [Analyse critique](#13-analyse-critique)
14. [Roadmap](#14-roadmap)
15. [**Executive Summary**](#executive-summary)

---

## 1. Vue d'ensemble

### Le problème

La Bourse de Casablanca n'offre pas d'outil de recherche accessible à l'investisseur particulier : les
données sont dispersées entre plusieurs sources, les cours sont différés d'environ 15 minutes, et aucune
couverture analytique structurée n'existe. La plateforme comble ce vide pour un utilisateur unique — son
propriétaire.

### L'objectif

Produire automatiquement une thèse d'investissement explicable par titre, sur trois horizons (court, moyen,
long), et notifier son propriétaire quand la conclusion change. Le périmètre est explicitement borné par le
code lui-même (`DISCLAIMER` dans [investment_analysis.py](moroccan_stock_intelligence/services/investment_analysis.py)) :
information seulement, aucun ordre passé, aucun conseil réglementé.

### Fonctionnement global

Un unique conteneur Docker héberge l'API FastAPI, la PWA Flutter compilée servie en statique, et un
ordonnanceur APScheduler in-process. L'ordonnanceur collecte, analyse, produit les rapports et notifie ;
l'API sert des rapports **déjà calculés et stockés**, jamais recalculés à la requête. Le chemin coûteux est
délibérément hors du chemin de requête.

### Architecture — flux de bout en bout

```
SOURCES EXTERNES                COLLECTE               STOCKAGE            CALCUL
──────────────────              ─────────              ─────────           ──────
casablanca-bourse.com  ──┐
  /live-market (cours)   │
  /fr/avis (avis)        ├──►  scrapers/        ──►   prices             ┌─► analytics.py
  /emetteurs (émetteurs) │     (3 sources        │    news               │   (MetricSet)
BMCE Capital (repli)   ──┤      en cascade)      │    stocks             │        │
CDG Capital  (repli)   ──┤                       │    fundamentals       │        ├─► scoring.py
bkam.ma (macro)        ──┘     collectors/     ──┘    company_profiles   │        │   (buy/watch/avoid)
                               (macro, issuers,       macro_indicators   │        │
                                history 3 ans)                           │        └─► horizon_strategy.py
                                                                         │            (court/moyen/long
                                                                         │             + risque + confiance)
                                                                         │                  │
RESTITUTION            API                  RECHERCHE                    │                  ▼
───────────            ───                  ─────────                    │         orchestrator.py
PWA Flutter    ◄──  FastAPI  ◄────────  analysis_reports  ◄──────────────┘         8 analystes
Telegram       ◄──  25 routes           prediction_history                         + risk_manager
Web Push       ◄──                      thesis_changes                             + CIO
                                        company_knowledge                          + débat + scénarios
                                        analyst_performance ◄── learning.py (notation + recalibrage)
```

> **Principe structurant.** Trois règles sont appliquées par l'architecture, pas par bonne volonté :
> **aucune donnée n'est inventée** (une métrique absente part en `missing_data` et abaisse la confiance) ;
> **seul le CIO recommande** (le schéma `AnalystReport` n'a structurellement pas de champ recommandation) ;
> **jamais de prix prédit**, uniquement des probabilités de scénarios.

---

## 2. Modules et composants

### Noyau

| Module | Lignes | Responsabilité | Dépend de |
|---|---:|---|---|
| [`repository.py`](moroccan_stock_intelligence/repository.py) | 749 | Toutes les lectures/écritures SQL. Point de passage unique vers la base. | `models` |
| [`models.py`](moroccan_stock_intelligence/models.py) | 433 | 16 tables SQLAlchemy 2.0 (ORM déclaratif). | — |
| [`api.py`](moroccan_stock_intelligence/api.py) | 397 | FastAPI : 25 routes + montage statique de la PWA. | `views`, `investment_analysis`, `orchestrator` |
| [`scheduler.py`](moroccan_stock_intelligence/scheduler.py) | 396 | 11 jobs APScheduler, démarrés dans le lifespan FastAPI. | `cli`, services |
| [`cli.py`](moroccan_stock_intelligence/cli.py) | 276 | 17 sous-commandes. Point d'entrée du conteneur et des GitHub Actions. | services |
| [`config.py`](moroccan_stock_intelligence/config.py) | 107 | 36 variables d'environnement, dataclass gelée. | `dotenv` |
| [`db.py`](moroccan_stock_intelligence/db.py) | 36 | Engine + session factory. `create_all`, pas de migration. | `config` |

### Couche services — calcul

| Module | Lignes | Responsabilité |
|---|---:|---|
| [`analytics.py`](moroccan_stock_intelligence/services/analytics.py) | 171 | Calcule le `MetricSet` (27 champs) depuis le cadre de prix pandas : momentum 1/5/30/90j, MM20/50/200, volatilité, anomalie de volume, support/résistance, 52 semaines, force sectorielle. |
| [`scoring.py`](moroccan_stock_intelligence/services/scoring.py) | 142 | Moteur historique : `buy_score` / `watch_score` / `avoid_score` + `classify_label`. |
| [`horizon_strategy.py`](moroccan_stock_intelligence/services/horizon_strategy.py) | 470 | Noyau par horizon : `assess_all`, `compute_risk`, `compute_confidence`. Réutilisé tel quel par le CIO. |
| [`investment_analysis.py`](moroccan_stock_intelligence/services/investment_analysis.py) | 669 | Composition explicable (`/api/analysis/*`). Le plus gros module de la couche. |
| [`portfolio.py`](moroccan_stock_intelligence/services/portfolio.py) | 182 | Positions réelles, P/L net de frais, avis VENDRE/CONSERVER. |
| [`favorites.py`](moroccan_stock_intelligence/services/favorites.py) | 132 | Watchlist — sans quantité ni prix d'achat, donc sans P/L. |
| [`news_classifier.py`](moroccan_stock_intelligence/services/news_classifier.py) | 441 | Classification événementielle des avis officiels. |
| [`news_backfill.py`](moroccan_stock_intelligence/services/news_backfill.py) | 226 | Reclassification idempotente des lignes stockées (prête, non appliquée). |

### Couche services — restitution et I/O

| Module | Lignes | Responsabilité |
|---|---:|---|
| [`views.py`](moroccan_stock_intelligence/services/views.py) | 344 | Payloads JSON de l'API « marché ». Porte `compute_state`, appelé par 6 endpoints. |
| [`digest.py`](moroccan_stock_intelligence/services/digest.py) | 358 | Mise en forme HTML des digests Telegram et des payloads push. |
| [`alerts.py`](moroccan_stock_intelligence/services/alerts.py) | 282 | Événements techniques + alertes urgentes intraday. |
| [`refresh.py`](moroccan_stock_intelligence/services/refresh.py) | 143 | Rafraîchissement à l'ouverture de l'app, avec verrou et cooldown de 900 s. |
| [`collector.py`](moroccan_stock_intelligence/services/collector.py) | 68 | Cascade des 3 scrapers : si l'un échoue, le suivant prend la main. |
| [`push.py`](moroccan_stock_intelligence/services/push.py) / [`telegram.py`](moroccan_stock_intelligence/services/telegram.py) | 82 / 27 | Web Push VAPID et Bot API Telegram (sortants). |

### Sous-systèmes

- **[`scrapers/`](moroccan_stock_intelligence/scrapers/)** (4 modules) — Casablanca Bourse en primaire, BMCE Capital et CDG Capital en repli. En-têtes navigateur, retries `tenacity`, isolation par source.
- **[`services/collectors/`](moroccan_stock_intelligence/services/collectors/)** (7 modules) — feeds Phase 1b : `macro` (BKAM), `issuers`/`issuer_page` (profil + 6 ratios), `history` (backfill ~3 ans), `company`, `fundamentals`, `http`.
- **[`services/analysts/`](moroccan_stock_intelligence/services/analysts/)** (11 modules) — 8 analystes + Risk Manager + CIO + `base` (protocole et helpers).
- **[`services/research/`](moroccan_stock_intelligence/services/research/)** (10 modules) — `context`, `contracts`, `orchestrator`, `debate`, `scenarios`, `learning`, `knowledge`, `store`, `notifications`.
- **[`services/synthesis/`](moroccan_stock_intelligence/services/synthesis/)** (3 modules) — `template` (déterministe, défaut), `claude` (LLM optionnel), `base` (protocole + validateur anti-hallucination).

### Interactions et sens de dépendance

Le graphe est globalement propre et acyclique : `api` → `views`/`investment_analysis`/`orchestrator` →
services de calcul → `repository` → `models`. Les analystes sont des fonctions pures du `ResearchContext`
(aucune I/O, aucune session).

> **MAJEUR — Le scheduler importe le CLI, et `refresh` aussi.**
> [`scheduler.py`](moroccan_stock_intelligence/scheduler.py) et [`services/refresh.py`](moroccan_stock_intelligence/services/refresh.py)
> importent `run_analysis` / `run_news` depuis [`cli.py`](moroccan_stock_intelligence/cli.py) (imports différés
> en corps de fonction). La logique métier vit donc dans la couche interface. C'est fonctionnel — les imports
> tardifs évitent le cycle — mais l'orchestration devrait descendre dans un service pour que le CLI reste une façade.

Une seule inversion notable : `investment_analysis.py` et `research/context.py` importent `compute_state`
depuis `services/views.py` — une couche de calcul dépend d'une couche de vue. Le commentaire de `scoring.py`
revendique explicitement l'inverse (« *none of them should import a view layer* ») ; l'intention n'est donc pas tenue.

---

## 3. Pipeline complet

| Étape | Mécanisme | Déclencheur | Écrit dans |
|---|---|---|---|
| **Collecte cours** | `collect_market_snapshots()` → 3 scrapers en cascade, en-têtes navigateur, retries exponentiels | Digests (9h/17h), intraday (11h/13h/15h), `/api/refresh` | `prices`, `stocks` |
| **Collecte avis** | `collect_news()` → parse `/fr/avis`, rattache au symbole par nom ou code | Digests uniquement (pas en intraday) | `news` |
| **Collecte macro** | `collect_macro()` → 6 séries inline dans la page d'accueil BKAM (JS, pas JSON) | Lun-ven 7h30 | `macro_indicators` |
| **Collecte émetteurs** | `collect_issuers()` → page émetteur : profil + 6 ratios | Dimanche 3h00 | `company_profiles`, `fundamentals` |
| **Backfill historique** | `backfill_history()` → endpoint `instrument_history`, ~3 ans glissants | One-shot à +180 s du boot, auto-réparant | `prices` (source distincte) |
| **Traitement** | `compute_metrics()` → resample journalier pandas → `MetricSet` par titre | À chaque analyse | — (en mémoire) |
| **Scoring** | `score_opportunity()` et/ou `assess_all()` selon le chemin | À chaque analyse / rapport | — (en mémoire) |
| **Rapports IA** | `orchestrator.generate()` → contexte → 8 analystes → risque → CIO → scénarios | Lun-ven 18h00, ou `?fresh=true` | `analysis_reports`, `prediction_history`, `thesis_changes` |
| **Apprentissage** | `run_learning_cycle()` → note les prédictions échues → recalibre | Tous les jours 6h00 | `analyst_performance`, `prediction_history` |
| **Connaissance** | `harvest_all()` → faits dédupliqués par hash | Dimanche 4h30 | `company_knowledge` |
| **Notifications** | Digests Telegram + push VAPID ; notifications de thèse ; alertes urgentes | Voir §10 | `notifications`, `alerts` |

### Cache et fraîcheur

Trois niveaux :

- `MARKET_CACHE_SECONDS` (900 s) borne le re-scraping — inutile d'aller plus vite que les 15 min de différé annoncés par la Bourse.
- `APP_REFRESH_COOLDOWN_SECONDS` (900 s) borne le rafraîchissement à l'ouverture de l'app.
- `REPORT_CACHE_SECONDS` (21 600 s, soit 6 h) borne la réutilisation d'un rapport stocké.

Le cache des rapports est en outre invalidé par `ENGINE_VERSION` — un rapport produit par une autre logique
n'est jamais resservi.

---

## 4. Moteurs de scoring

Le projet contient **trois** mécanismes de scoring distincts. Deux produisent un score faisant autorité et
coexistent en production ; le troisième ne sert qu'au débat.

### Moteur A — `scoring.py` · score d'opportunité

Le moteur historique. Produit trois scores 0-100 et une étiquette française unique. C'est lui qui alimente
tout ce que voit l'utilisateur au quotidien : l'onglet Marché, l'onglet Opportunités, l'aperçu, les digests
Telegram, les favoris, le portefeuille.

| Composant | Poids | Formule |
|---|---:|---|
| `momentum` | 0.25 | Moyenne pondérée 1j (.15) / 5j (.25) / 30j (.35) / 90j (.25), chaque `clamp(50 + var×3)` |
| `volume_anomaly` | 0.20 | `clamp((anomalie − 1) / 2 × 100)` |
| `valuation_opportunity` | 0.20 | 0.6 × proximité du plus bas 52s + 0.4 × distance au plus haut |
| `support_proximity` | 0.15 | `clamp(100 - abs(support_distance) * 8)` |
| `sector_strength` | 0.10 | `clamp(50 + force_sectorielle × 2)` |
| `news_sentiment` | 0.10 | `clamp(50 + sentiment × 25)` — **constante 50 en production** |

`avoid_score` est additif : momentum 5j < −5 → +25 ; momentum 30j < −10 → +25 ; drawdown < −25 → +20 ;
volume élevé en séance de baisse → +15 ; news < −0.5 → +15.

`watch_score = 0.65 × buy + 0.35 × (100 − avoid)`

`classify_label` tranche : `avoid ≥ 60` → **ÉVITER** ; `buy ≥ 65` → **ACHETER** ; `buy ≥ 50` ou `watch ≥ 55`
→ **SURVEILLER** ; sinon **NEUTRE**.

**Limitations du moteur A :**

- **Pas de notion de couverture.** Un composant absent est remplacé par une valeur neutre codée en dur (50.0) et pèse quand même. Le score ne dit jamais sur combien de données il repose.
- **Pas de confiance.** Aucun indicateur de fiabilité n'accompagne le score.
- **Pas d'horizon.** Un score unique mélange momentum 1 jour et structure 52 semaines.
- **Poids news mort** (voir §5).

### Moteur B — `horizon_strategy.py` · par horizon

Le noyau moderne, réutilisé tel quel par le CIO. Il calcule chaque horizon comme une moyenne pondérée des
**composants disponibles uniquement**.

| Horizon | Formule (poids) | Cible historique |
|---|---|---:|
| **Court** | 0.30 momentum court + 0.20 volume + 0.20 cassure + 0.15 support + **0.15 actualités** − pénalité surchauffe (6 pts si var. jour > +4 %) | 30 j |
| **Moyen** | 0.35 tendance (30/90j) + 0.25 moyennes mobiles + 0.15 secteur + 0.15 volatilité (inverse) + **0.10 actualités** | 90 j |
| **Long** | 0.30 tendance longue + 0.30 stabilité + 0.20 structure 52s + 0.10 secteur + **0.10 événements** | 250 j |

Deux mécanismes distinguent réellement ce moteur du précédent :

- **Rétraction vers le neutre** — `score = 50 + (score − 50) × min(1, couverture / 0.8)`. Un score fort bâti sur un seul composant disponible serait une fausse certitude ; il est donc atténué.
- **Confiance explicite** — `confiance = 50 × couverture + 30 × min(historique / cible, 1) + 20 × cohérence`, plafonnée à 35 si la couverture est sous 50 %. La cohérence vaut `clamp(20 − écart-type des composants / 2.5)`.

`compute_risk` agrège volatilité, momentum négatif, drawdown, volume de baisse, **actualités défavorables
(+12)** et incertitude d'historique (+10 si < 30 j).

### Moteur C — `lean_from` · penchants d'analyste

Chaque analyste produit un `HorizonSignal.lean` (0-100). Ces penchants ne font **pas** autorité : ils servent
à détecter les contradictions et à alimenter le moteur de débat. Le commentaire de `contracts.py` l'énonce
explicitement — le score autoritaire vient du noyau B.

### Points de divergence

> **CRITIQUE — Deux moteurs, deux vocabulaires, deux verdicts sur le même titre.**
>
> Les moteurs A et B ne partagent ni entrées, ni poids, ni échelle de sortie, ni vocabulaire — et sont servis
> simultanément à la même interface. L'onglet *Opportunités* (moteur A, via `/api/opportunities`) peut afficher
> **ACHETER** pendant que la fiche du même titre (moteur B, via `/api/report/{symbol}`) affiche **À surveiller**
> ou **Risqué**. Aucun des deux n'est en tort : ils répondent à des questions différentes avec des données
> différentes.
>
> Sources concrètes de divergence :
> - **News** — le moteur A les ignore totalement, le moteur B les intègre au score, au risque et à la confiance.
> - **Couverture** — A ne rétracte pas, B rétracte. Sur un titre à historique court, A peut être catégorique là où B est prudent.
> - **Étiquettes** — A produit ACHETER/SURVEILLER/ÉVITER/NEUTRE ; le CIO produit Forte opportunité/À surveiller/Conserver/Prendre des bénéfices/Éviter/Risqué. Aucune table de correspondance n'existe.
> - **Portefeuille** — le CIO tient compte de la détention (`HOLD`, `TAKE_PROFIT`) ; A l'ignore.

---

## 5. Système de news

### Nature réelle de la source

Point le plus mal compris du système, établi en inspectant les 11 lignes réellement présentes en base :
`casablanca-bourse.com/fr/avis` **ne publie pas de news éditoriales**. Il publie des **avis procéduraux** :
dates de détachement, termes d'augmentation de capital, valeur théorique d'un droit de souscription,
admissions de contrats à terme, avis réglementaires. Sur le corpus réel, 8 lignes sur 11 sont des opérations
sur titre, et 2 ne concernent aucun émetteur. Un modèle de tonalité par mots-clés y est une erreur de catégorie.

### Collecte

`collect_news()` parse les liens PDF de la page d'avis, dédoublonne par URL, extrait une date par regex dans
le texte parent du lien, et rattache au symbole via `match_symbol()` — recherche du code (frontière de mot)
puis du nom de société dans le titre. Plafond de 100 items. **Appelé uniquement dans les digests** (9h/17h),
pas en intraday.

### Classification — taxonomie

Table de règles ordonnée, la plus spécifique gagnant d'emblée. Aucun score cumulé, donc aucune égalité à
départager. 23 types d'événements repliés sur 5 familles.

| Famille | Types précis (`event_type`) | Impact |
|---|---|---|
| `dividend` | `ex_dividend` · `dividend_payment` · `dividend_announcement` · `dividend_cut` | 0.00 · +0.20 · +0.35 · −0.60 |
| `capital_action` | `subscription_right` · `capital_increase_cash` · `capital_increase_reserves` · `capital_increase_employees` · `capital_increase_merger` · `merger` · `tender_offer_buy` · `tender_offer_withdraw` · `public_offering` · `share_buyback` · `stock_split` | 0.00 · −0.35 · 0.00 · −0.10 · 0.00 · 0.00 · +0.50 · 0.00 · 0.00 · +0.40 · 0.00 |
| `results` | `profit_warning` · `results` | −0.85 · 0.00 (±0.5 si direction énoncée) |
| `trading_notice` | `trading_suspension` · `trading_resumption` · `delisting` | −0.50 · +0.10 · −0.80 |
| `announcement` | `threshold_crossing` · `market_notice` · `announcement` | 0.00 · 0.00 · 0.00 |

Deux drapeaux dérivés complètent le verdict : `is_mechanical` (l'effet prix est arithmétique et l'actionnaire
est rendu indemne — détachement, actions gratuites, division du nominal, droit de souscription) et
`is_company_event` (faux pour un avis de marché).

### Event families — le rôle réel

`event_family()` replie la taxonomie fine vers les 5 valeurs historiques et gère les valeurs héritées. Sans
elle, les prédicats existants `has_dividend` / `has_results` — qui comparaient `event_type == "dividend"` —
seraient devenus silencieusement toujours faux, faisant chuter le composant `evenements` long terme de 70 à
50 sans aucun signal d'erreur.

### NewsContext

Agrégat par symbole sur 30 jours, construit **deux fois** dans le code
(`investment_analysis.build_news_contexts` et `research/context._build_news` — deux implémentations quasi
identiques). Champs : `count`, `avg_impact`, `positive`/`negative`, `latest_title`, `latest_at`,
`fresh_negative` (négatif < 24 h), `has_dividend`, `has_results`.

### Ce qui est réellement utilisé — et ce qui ne l'est pas

| Consommateur | Usage des news | Statut |
|---|---|---|
| `horizon_strategy` (moteur B) | Composant `actualites`/`evenements`, risque (+12), et confiance via la couverture | **Actif** |
| `news_analyst` (agent 5) | Rapport dédié : clustering anti-doublon, tonalité, heuristique « déjà dans les cours » | **Actif** |
| `research/notifications` | Déclencheur « news négative fraîche sur une position détenue » | **Actif** |
| `knowledge` | Historique des opérations sur titre par famille | **Actif** |
| `/api/news` + onglet Actus | Affichage brut | **Actif** |
| `scoring.py` (moteur A) | Paramètre `news_sentiment_score` jamais transmis | **MORT** |

> **CRITIQUE — 10 % du score d'opportunité est une constante.**
>
> `score_opportunity(metric, news_sentiment_score=0.0)` réserve 10 % du `buy_score` aux news, mais **aucun
> appelant de production ne transmet l'argument** : `views.compute_state`, `cli.run_analysis` et
> `dashboard/app.py` appellent tous `score_opportunity(metric)`. Le composant vaut invariablement 50.
> Vérifié par exécution : le score varie de 46.43 à 51.43 selon le sentiment, mais la production produit
> toujours 48.93. Seul `tests/test_scoring.py` passe l'argument — d'où l'absence de détection.

> **MAJEUR — La base porte encore l'ancienne classification.**
>
> `repository.store_news` dédoublonne par URL et sort immédiatement si la ligne existe (`if existing: return`)
> — il ne met jamais à jour. Les 9 lignes classées par l'ancien modèle conservent leur `+0.6` et restent dans
> la fenêtre de 30 jours qui alimente `NewsContext`. Le moteur B consomme donc aujourd'hui des valeurs fausses.
> Le backfill `reclassify-news` est écrit et testé (21 tests) mais **non appliqué** : dry-run vérifié,
> 11 lignes analysées, 11 à modifier, impact moyen **+0.491 → −0.032**.

---

## 6. Multi-agents IA

Dix modules, registre explicite et ordonné dans `orchestrator.SYMBOL_ANALYSTS` — pas de décorateurs à effet
de bord, donc ordre d'exécution déterministe et testable. Chaque analyste est **isolé en cas de panne** : une
exception produit un `degraded_report` au lieu de couler le rapport entier.

| # | Agent | Portée | Entrées | Sortie particulière | Ver. |
|--:|---|---|---|---|---|
| 1 | `technical` | titre | `MetricSet` | Penchants 3 horizons + scénarios. RSI/MACD/Bollinger déclarés manquants, jamais simulés. | 1.0 |
| 2 | `market_structure` | titre | Read-model marché/secteur | Force relative vs proxy d'indice **équipondéré** — tout ce qui en découle est étiqueté inference. | 1.0 |
| 3 | `news` | titre | `NewsContext` + `NewsView[]` | Clustering anti-doublon, heuristique price-in. | 1.0 |
| 4 | `historical_behaviour` | titre | Historique de prix | Confiance proportionnelle au **nombre d'occurrences passées**. | 1.0 |
| 5 | `macro` | marché | `MacroSnapshot` (BKAM) | **Aucun penchant directionnel** — informe le CIO, ne vote pas sur le prix. | 1.1 |
| 6 | `company` | titre | `CompanyProfile` | Objet social, actionnariat, dirigeants. Pas de narratif inventé. | 1.1 |
| 7 | `fundamental` | titre | 6 ratios officiels | Un PER dérivé (prix/BPA) est une **inference**, jamais un fait ; confiance pénalisée de 8. | 1.0+ |
| 8 | `portfolio_analyst` | portefeuille | `GatheredState` complet | Concentration sectorielle, effet marginal du titre. Signature distincte, appelé explicitement. | 1.0 |
| 9 | `risk_manager` | agrégateur | Contexte + **tous** les rapports | Réutilise `compute_risk` puis moissonne les `risk_flags` de chaque analyste. | — |
| 10 | `cio` | décideur | Tout + fiabilité apprise | **Seul module autorisé à recommander.** | 1.0 |

### Contrat de sortie

Tout analyste renvoie un `AnalystReport` : `observations`, `strengths`, `weaknesses`, `risk_flags`,
`horizon_signals`, `confidence`, `data_used`, `missing_data`, `notes`. Chaque `Statement` porte un `kind`
(`fact` / `inference` / `opinion`), une polarité, un poids et le dictionnaire `evidence` des chiffres bruts.
**Le schéma n'a aucun champ recommandation** — il est structurellement impossible pour un analyste de décider.

### Moteur de débat

Un échange n'est créé que sur un **vrai désaccord** : un analyste haussier face à un baissier sur le même
horizon. L'accord n'est pas un débat. Le poids de chaque camp est le produit de trois facteurs :

```
poids = force_du_penchant × confiance_déclarée × fiabilité_apprise
```

`fiabilité_apprise` vient du moteur d'apprentissage ; tant qu'un analyste n'a pas assez de prédictions
évaluées, son multiplicateur reste exactement 1.0 et le débat ne repose que sur la confiance déclarée.

### Processus de décision du CIO

1. **Score autoritaire** — `assess_all()` + `compute_confidence()` depuis le noyau B. Aucune dérive : le CIO ne recalcule rien.
2. **Débat** — `build_debate()` sur les 5 analystes directionnels (`technical`, `market_structure`, `news`, `historical_behaviour`, `fundamental`).
3. **Verdict par horizon** — les recommandations *peuvent* différer selon l'horizon, et la divergence est explicitement affichée comme une information, pas masquée.
4. **Arbitrage** — si le titre est détenu : avis VENDRE + gain ≥ `take_profit_pct` → `TAKE_PROFIT`, sinon `RISKY` ; risque ≥ 70 → `RISKY` ; sinon `HOLD`. Sinon : score ≥ 70 et confiance ≥ 50 → `STRONG_OPPORTUNITY`.
5. **Thèse** — résumé exécutif, cas haussier et baissier cités par module d'origine, contradictions, conditions d'invalidation, points à surveiller, note de calibration.

### Apprentissage — statistique, pas ML

Chaque prédiction est écrite avec une date d'évaluation (`EVAL_DAYS` : 10 / 60 / 180 jours). Une fois échue
et un prix disponible, elle est notée contre la réalité. Le multiplicateur est une moyenne a posteriori
Beta-Binomiale rétractée vers un prior neutre :

```
taux_a_posteriori = (succès + a) / (n + a + b)     avec a = b = PRIOR_STRENGTH / 2 = 5
```

Bornes : `[0.6 ; 1.4]` — même un analyste brillant ne peut dominer le CIO, un mauvais est amorti plutôt que
réduit au silence. Sous `MIN_CALIBRATION_SAMPLES` (20 évaluations), le multiplicateur reste exactement 1.0.
Un mouvement inférieur à `FLAT_RETURN_PCT` (1.5 %) est du bruit, pas une direction. Métriques suivies : taux
de réussite, score de Brier, erreur de calibration, précision, rappel.

### Synthèse LLM — optionnelle et bridée

Défaut : `TemplateSynthesizer`, déterministe et hors-ligne. Si `LLM_PROVIDER=anthropic` et
`ANTHROPIC_API_KEY` sont posés, `ClaudeSynthesizer` prend le relais — mais reçoit **uniquement le JSON du
rapport déjà décidé**, jamais du HTML ni une ligne de base. Sa sortie passe par `validate_narrative()` : tout
nombre absent du rapport est traité comme une fabrication et déclenche le repli sur le template. Un rapport
n'est jamais bloqué par le LLM.

---

## 7. Base de données

16 tables, SQLAlchemy 2.0 déclaratif, créées par `Base.metadata.create_all()`. **Aucune migration** — pas
d'Alembic, pas d'historique de schéma.

### Socle marché

| Table | Rôle | Contrainte d'unicité | Écrit par | Lu par |
|---|---|---|---|---|
| `stocks` | Référentiel des titres cotés | `symbol` | `collector` | Tout |
| `prices` | Chaque snapshot collecté, conservé indéfiniment | `stock_id + observed_at + source` | `collector`, `history` | `analytics`, `learning` |
| `news` | Avis officiels + classification dérivée | `url` | `run_news` | `context`, `views`, `knowledge` |
| `signals` | Événements techniques + explications de score | — | `generate_alerts` | **Streamlit seul** |
| `alerts` | Registre de déduplication des notifications | `stock_id + event_key` | `alerts`, `notifications` | `dispatch_unsent_alerts` |
| `notifications` | Historique in-app des notifications délivrées | — | `digest`, `push` | `/api/notifications` |
| `push_subscriptions` | Abonnements Web Push VAPID | `endpoint` | `/api/push/subscribe` | `send_push_to_all` |
| `favorites` | Watchlist explicite | `stock_id` | `/api/favorites` | `favorites`, digests |

### Feeds Phase 1b

| Table | Rôle | Contrainte | Note |
|---|---|---|---|
| `fundamentals` | Les 6 ratios officiels par exercice | `stock + fiscal_year + source` | Clé naturelle = `fiscal_year`, pas la date de collecte. Cellule « - » → NULL, jamais 0.0. |
| `company_profiles` | Identité, actionnariat, dirigeants | — | Source `"derived"` pour un PER calculé. |
| `macro_indicators` | Une observation d'une série BKAM | `indicator + as_of + source` | Long/étroit : un nouvel indicateur ne demande aucune migration. |

### Plateforme de recherche

| Table | Rôle | Contrainte | Note |
|---|---|---|---|
| `analysis_reports` | Rapport complet (JSON) + verdicts et confiances dénormalisés | — | Indexé sur `engine_version` et `thesis_hash` : un rapport d'une autre logique n'est jamais resservi. |
| `prediction_history` | Une affirmation falsifiable + son évaluation ultérieure | `report_id + analyst + horizon + scenario` | Colonnes d'issue NULL tant que non évalué — jamais compté correct *ni* incorrect. |
| `analyst_performance` | Statistiques glissantes par (analyste, horizon) | `analyst + horizon` | Reconstruit depuis `prediction_history`. |
| `company_knowledge` | Faits structurés accumulés et dédupliqués | `stock_id + fact_hash` | Ré-observer un fait met à jour `last_seen`. Porte le label `kind` jusqu'en base. |
| `thesis_changes` | Mémoire d'investissement : chaque bascule de recommandation, avec sa raison | — | Stocke la preuve nouvelle et les hypothèses invalidées. |

> **MAJEUR — Table `signals` : écrite en continu, jamais lue en production.**
>
> `generate_alerts()` est appelé à **chaque** `run_analysis` (digests + intraday) et écrit dans `signals` et
> `alerts`. Or `signals` n'est lu que par `dashboard/app.py:132` — le tableau de bord Streamlit, **qui n'est
> pas déployé sur Railway**. Et `dispatch_unsent_alerts()` n'est appelé que par la sous-commande `send-alerts`,
> qui n'est déclenchée par aucun job ni aucun workflow. Les lignes `sent=0` s'accumulent donc indéfiniment
> sans jamais être dépilées, et `build_digest` ne les lit pas. Seule la déduplication via `create_alert_once`
> reste réellement utile.

---

## 8. API

25 routes FastAPI. La PWA est montée en dernier pour que les routes API gardent la priorité.

| Route | Rôle | Modules appelés | Front |
|---|---|---|---|
| `GET /api/health` | Sonde de santé + état du scheduler | — | Infra |
| `GET /api/overview` | Portefeuille + marché + favoris | `views.overview_payload` → moteur A | Oui |
| `GET /api/stocks` | Liste triable/filtrable (`sort`, `sector`, `q`) | `views.stocks_payload` → moteur A | Oui |
| `GET /api/stock/{symbol}` | Fiche : cours, historique, score, news liées | `views.stock_detail_payload` | Oui |
| `GET /api/opportunities` | Opportunités au-dessus d'un seuil | `views.opportunities_payload` → moteur A | Oui |
| `GET /api/news` | Avis récents + classification | `views.news_payload` | Oui |
| `GET /api/notifications` | Historique in-app | `views.notifications_payload` | Oui |
| `GET /api/sectors` | Agrégats sectoriels | `views.sectors_payload` | Oui |
| `GET /api/favorites` | Favoris évalués et triés | `favorites` | Oui |
| `POST/DELETE /api/favorites/{symbol}` | Étoiler / retirer. Idempotent des deux côtés. | `repository` | Oui |
| `GET /api/vapid-public-key` | Clé publique VAPID | `config` | push.js |
| `POST /api/push/subscribe` | Enregistre un abonnement | `push` | push.js |
| `POST /api/push/test` | Notification de test | `push` | push.js |
| `POST /api/run-now` | Collecte + analyse + **notifie** (digest à la demande) | `scheduler.run_update_now` | push.js |
| `POST /api/refresh` | Re-collecte **silencieuse** à l'ouverture. Verrou pris avant réponse. | `refresh` | Oui |
| `GET /api/refresh/status` | Polling pendant la collecte | `refresh` | Oui |
| `GET /api/analysis/market-summary` | Synthèse marché explicable | `investment_analysis` → moteur B | Oui |
| `GET /api/analysis/portfolio` | Analyse du portefeuille | `investment_analysis` | Oui |
| `GET /api/analysis/opportunities` | Opportunités par horizon | `investment_analysis` → moteur B | Oui |
| `GET /api/analysis/{symbol}` | Analyse explicable d'un titre | `investment_analysis` | **Non** |
| `GET /api/report/{symbol}` | Rapport multi-analystes complet | `orchestrator` (cache 6 h) | Oui |
| `GET /api/report/{symbol}/narrative` | Note de recherche rédigée (template ou LLM) | `synthesis` | **Non** |
| `GET /api/reports/history/{symbol}` | Chronologie des recommandations + bascules de thèse | `store.thesis_history_payload` | **Non** |
| `GET /api/knowledge/{symbol}` | Tout ce que la plateforme a accumulé sur la société | `knowledge` | **Non** |
| `GET /api/performance` | Exactitude des prédictions passées | `learning` | Oui |

> **MAJEUR — Quatre fonctionnalités construites et servies, mais sans interface.**
>
> `/api/report/{symbol}/narrative` (la note rédigée, seul point d'entrée du LLM),
> `/api/reports/history/{symbol}` (la mémoire d'investissement — `thesis_changes`),
> `/api/knowledge/{symbol}` (la base de connaissance) et `/api/analysis/{symbol}` ne sont appelés par aucune
> ligne de `main.dart`. Trois sous-systèmes entiers (synthèse, mémoire de thèse, connaissance) tournent,
> écrivent en base, sont testés — et restent invisibles.

---

## 9. Frontend Flutter / PWA

Application Flutter Web (2 177 lignes, fichier unique [`main.dart`](flutter_app/lib/main.dart)), compilée à
la main et **commitée** dans `webapp_flutter/`. Servie en statique par FastAPI sur la même origine — donc
aucun CORS et aucun hébergement frontend séparé.

### Écrans — 7 onglets

| Onglet | Contenu | Endpoints |
|---|---|---|
| Portefeuille | Positions, P/L net de frais, avis par ligne | `/api/overview` |
| Favoris | Watchlist évaluée, étoilage | `/api/favorites` (+ POST/DELETE) |
| Marché | Liste triable, recherche, filtre sectoriel | `/api/stocks`, `/api/sectors`, `/api/stock/{s}` |
| Opportunités | Classement par score, seuil réglable | `/api/opportunities` |
| Analyse | Rapport multi-analystes par horizon, performance | `/api/report/{s}`, `/api/analysis/*`, `/api/performance` |
| Actus | Avis officiels classés | `/api/news` |
| Notifs | Historique des notifications | `/api/notifications` |

### Communication avec l'API

Pas de package `http` : le code utilise `dart:html` `HttpRequest` directement, avec `Uri.base.resolve(path)`
— d'où les URLs relatives et l'absence de configuration d'hôte. Un pont `dart:js_interop` minimal
(`appEnablePush`, `appTestPush`, `appRunNow`) délègue à `web/push.js`, qui détient le code Web Push éprouvé.

Au lancement, l'app appelle `/api/refresh` puis poll `/api/refresh/status` jusqu'à la fin, et recharge alors
l'onglet actif. Le rafraîchissement est silencieux par conception : déclencher le digest à chaque ouverture
notifierait le propriétaire plusieurs fois par jour pour rien.

Palette sombre codée en dur (`#0B1120` fond, `#38BDF8` accent), graphiques via `fl_chart`, animations via
`flutter_animate`. Cache-busting par en-têtes `no-cache` sur l'app-shell pour qu'un déploiement ne serve pas
un service worker périmé.

---

## 10. Infrastructure

### Docker

`python:3.12-slim`, `WORKDIR /app`, installation des dépendances puis copie du dépôt, `mkdir -p data`, port
8000. Commande : `python -m moroccan_stock_intelligence.cli serve --host 0.0.0.0`. Le port vient de `$PORT`
quand l'hôte l'injecte.

### Railway — et la confusion à lever

> **CRITIQUE — Il n'y a pas de PostgreSQL. La production tourne sur SQLite, sur un volume.**
>
> [`HANDOVER.md:79`](HANDOVER.md) liste « *PostgreSQL in production* » sous **« Features still missing / not
> done »**, et [`HANDOVER.md:107`](HANDOVER.md) précise : le fichier SQLite sur un volume Railway monté en
> `/app/data`. PostgreSQL est *supporté* (`psycopg[binary]` est installé, il suffit de changer `DATABASE_URL`)
> mais n'est pas utilisé.
>
> Conséquence opérationnelle vicieuse : `DATABASE_URL` vaut `sqlite:///data/market.db`, un chemin **relatif**.
> Dans le conteneur il désigne le volume ; sur un poste de développement, la même chaîne désigne
> `./data/market.db`. Une commande lancée via `railway run` — qui exécute en local avec les variables Railway
> injectées — produirait un rapport parfaitement crédible **sur la mauvaise base**. Toute opération sur les
> données de production doit passer par `railway ssh`, dans le conteneur.

À noter également : [`render.yaml`](render.yaml) est présent à la racine et décrit un déploiement **Render**
complet (plan Starter, disque 1 Go monté en `/app/data`, healthcheck `/api/health`), alors que l'hôte réel
documenté est Railway. Aucun fichier de configuration Railway n'existe dans le dépôt.

### Scheduler — 11 jobs

| Job | Déclenchement | Action |
|---|---|---|
| `bootstrap` | +8 s après boot | Amorçage initial |
| `feeds_bootstrap` | +90 s | Seed des feeds Phase 1b si tables vides |
| `history_bootstrap` | +180 s | Backfill ~3 ans, auto-réparant, espacé pour ne pas marteler la source |
| `morning_digest` | Lun-ven **09:00** | Collecte + news + analyse + Telegram + push |
| `intraday_update` | Lun-ven 11:00, 13:00, 15:00 | Mise à jour légère + filet de sécurité crash |
| `closing_digest` | Lun-ven **17:00** | Idem matin |
| `macro_collect` | Lun-ven 07:30 | BKAM |
| `issuer_collect` | Dimanche 03:00 | Profils + ratios |
| `knowledge_harvest` | Dimanche 04:30 | Après la collecte émetteurs, pour moissonner du frais |
| `learning_cycle` | Tous les jours 06:00 | Notation + recalibrage |
| `research_reports` | Lun-ven 18:00 | Après clôture, une fois les cours du jour rentrés |

> **CRITIQUE — Deux chemins Telegram, deux bases, quatre digests par jour.**
>
> Le workflow [`.github/workflows/stock-alert.yml`](.github/workflows/stock-alert.yml) exécute
> `morning-digest` à 9h UTC (10:00 Maroc) et `afternoon-digest` à 15h UTC (16:00 Maroc), avec ses propres
> secrets Telegram. Le scheduler Railway envoie les siens à **09:00 et 17:00** Maroc. Si les deux portent
> `TELEGRAM_BOT_TOKEN`, le propriétaire reçoit **quatre digests quotidiens**.
>
> Plus grave que la redondance : le workflow tourne sur une **base SQLite entièrement séparée**, restaurée
> depuis le cache GitHub Actions (`DATABASE_URL: sqlite:///data/market.db` dans le runner). Sa profondeur
> d'historique n'a rien à voir avec celle du volume Railway — donc ses scores, momentums et confiances
> **diffèrent structurellement**. Les deux chemins peuvent affirmer des choses contradictoires sur le même
> titre le même jour.

### Variables d'environnement — 36

Toutes lues dans `config.py` via une dataclass gelée, toutes avec un défaut sauf les secrets. Groupes : base
et HTTP (6), Telegram (2), VAPID (3), portefeuille et frais (3), seuils d'avis (5), scoring (2), scheduler et
caches (5), apprentissage (5), LLM (4), divers (1). Aucun secret n'est commité : `PORTFOLIO_JSON` permet de
passer les prix d'achat en secret plutôt qu'en fichier.

### Persistance et sauvegardes

> **CRITIQUE — Fichier unique, aucune migration, aucune sauvegarde automatisée.**
>
> La totalité de l'état — cours, historique 3 ans, rapports, prédictions, apprentissage, connaissance — vit
> dans un fichier SQLite sur un volume. Il n'existe **aucun job de sauvegarde** dans le dépôt, et **aucune
> migration Alembic** : le schéma est créé par `create_all`, qui crée les tables absentes mais **n'ALTER
> jamais une table existante**. Toute évolution d'une colonne existante devra être faite à la main, en
> production, sans filet.

---

## 11. État actuel du projet

### Totalement terminé

- Collecte des cours avec cascade de 3 sources, retries, isolation, logs structurés.
- Backfill historique ~3 ans, idempotent et auto-réparant.
- Calcul des métriques techniques (`MetricSet`, 27 champs).
- Moteur par horizon avec couverture, rétraction, risque et confiance.
- Les 10 agents, le débat, les scénarios, le Risk Manager, le CIO.
- Moteur d'apprentissage Beta-Binomial + Brier avec seuil de prudence.
- Persistance des rapports, mémoire de thèse, base de connaissance.
- Synthèse déterministe + LLM optionnel avec validateur anti-hallucination.
- Portefeuille, favoris, digests Telegram, Web Push, PWA 7 onglets.
- Classificateur de news événementiel (28 fonctions de test).

### Partiellement terminé

- **Système de news** — classificateur correct, mais base non reclassée et moteur A non branché.
- **Feeds Phase 1b** — collecteurs écrits et planifiés ; les analystes `company`, `fundamental` et `macro` émettent un rapport « données non collectées » honnête tant que les tables sont vides.
- **Analyste technique** — RSI, MACD, Bollinger et chandeliers sont déclarés manquants, en attente d'ajout au `MetricSet`.
- **Structure de marché** — proxy d'indice équipondéré ; aucun flux MASI/MSI20 officiel n'existe (`bourse_data/indice` renvoie un vrai 404).

### Non implémenté

- Authentification, multi-utilisateur, portefeuilles par utilisateur.
- Migrations Alembic.
- PostgreSQL en production.
- Backtesting, indices sectoriels de référence.
- Extraction du texte des PDF d'avis (seuls titres et liens sont parsés).
- Bot Telegram à commandes ; sources RSS supplémentaires.
- Build Flutter automatisé en CI.

### Expérimental

- **Synthèse LLM** — désactivée par défaut (`LLM_PROVIDER=none`), jamais chargée par les tests.
- **Tableau de bord Streamlit** (7 pages) — présent, référencé uniquement dans `docker-compose`, non déployé.

### Dette technique

| Sévérité | Point |
|---|---|
| **CRITIQUE** | Deux moteurs de scoring servis simultanément, sans correspondance de vocabulaire. |
| **CRITIQUE** | Double chemin Telegram sur deux bases divergentes. |
| **CRITIQUE** | Aucune sauvegarde, aucune migration, base mono-fichier. |
| **MAJEUR** | Poids news mort (10 % du `buy_score`). |
| **MAJEUR** | `signals` écrite en continu, jamais lue ; `alerts` jamais dépilée. |
| **MAJEUR** | `NewsContext` construit par deux implémentations quasi identiques (`investment_analysis` et `research/context`). |
| **MAJEUR** | Aucun test sur la couche API (397 lignes jamais chargées). |
| Moyen | `investment_analysis.py` à 669 lignes ; `repository.py` à 749. |
| Moyen | Couches de calcul important `views.compute_state` — inversion de dépendance assumée nulle part. |
| Moyen | Build Flutter commité à la main ; `webapp/` (ancien front JS) conservé en repli mort. |
| Faible | `render.yaml` décrit un hôte qui n'est pas celui utilisé. |

### Incohérences et documentation obsolète

| Source | Affirme | Réalité du code |
|---|---|---|
| `README.md:3, 22` | « exploring the market in Streamlit », « Provides a Streamlit dashboard » | Le front réel est la PWA Flutter ; Streamlit n'est pas déployé. |
| `README.md:15` | Digests à 10:00 et 16:00 | Le scheduler tourne à 09:00 et 17:00 (`scheduler.py:290,301`). |
| `README.md` | Arborescence sans `analysts/`, `research/`, `synthesis/`, `collectors/` | Ces 4 sous-systèmes représentent l'essentiel de la valeur ajoutée. |
| `ARCHITECTURE_AI_ANALYST.md:3` | « **Status: DESIGN — awaiting validation. No implementation until this is approved.** » | Intégralement implémenté : 10 agents, débat, scénarios, apprentissage, synthèse. |
| `cli.py:126,128` | Libellés « Matin (10:00) » / « Clôture (16:00) » | Le scheduler passe « Ouverture (09:00) » / « Clôture (17:00) ». Deux vocabulaires pour le même produit. |
| `horizon_strategy.py:376` | « Annonce de dividende récente » | Déclenché par `has_dividend`, que satisfait un *détachement* — qui n'est pas une annonce. |
| `scheduler.py:59` | « the old event-based analysis alerts … are gone » | `generate_alerts()` tourne toujours dans `run_analysis` (`cli.py:205`) et écrit à chaque passage. |

---

## 12. Tests

| Métrique | Valeur |
|---|---|
| Cas exécutés | **226** (tous verts, 20 s) |
| Fonctions de test | 141 (13 fichiers) |
| Lignes de test | 2 692 (ratio 1:4.4) |
| Modules chargés pendant la suite | 55/60 |
| Fixtures HTML | 2 (émetteur ATW, BKAM) |

> **Correction du 16 juillet 2026.** Ce paragraphe affirmait initialement qu'aucune couverture de lignes
> n'était mesurable, `pytest-cov` étant absent et `pip` supposé bloqué. **C'était faux** : `pip` fonctionne.
> `pytest-cov` a été installé et la couverture réelle mesurée — **68 %** à l'état audité, **76 %** après les
> chantiers du 16 juillet. Les chiffres du tableau ci-dessus restent des proxys ; la couverture réelle par
> module est désormais donnée plus bas.

### Répartition

| Fichier | Fonctions | Ce qui est garanti |
|---|---:|---|
| `test_news_classifier.py` | 28 | Les 3 bugs d'origine en non-régression, taxonomie complète, robustesse (accents, apostrophes courbes, NBSP, tirets, frontières de mots), corpus de production réel, familles et valeurs héritées. |
| `test_news_backfill.py` | 21 | Dry-run par défaut et sans écriture, application, idempotence, rollback, lots, colonnes de contenu intouchées. |
| `test_research_platform.py` | 19 | Cache et invalidation par version, `thesis_hash` sur la décision et non la prose, bascules de thèse, prédictions, seuil de calibration, Brier, débat, scénarios, validateur anti-hallucination. |
| `test_favorites.py` | 19 | Évaluation, tri, alertes, déduplication holding/favori. |
| `test_collectors.py` | 11 | Parsing émetteur et BKAM contre fixtures réelles. |
| `test_refresh.py` | 9 | Verrou, cooldown, états `fresh`/`running`/`started`. |
| `test_history_backfill.py` | 8 | Format décimal, « - » jamais stocké en 0.0, cours ajusté préféré, idempotence. |
| `test_horizon_strategy.py` | 8 | Bornes, couverture, données creuses, risque, gating long terme. |
| `test_research_engine.py` | 5 | Seul le CIO recommande, analystes indisponibles honnêtes, isolation des pannes, registre déterministe. |
| `test_portfolio.py` | 5 | P/L net de frais, avis. |
| `test_investment_analysis.py` | 4 | Composition, données creuses. |
| `test_parsing.py` | 3 | Scrapers contre HTML. |
| `test_scoring.py` | 1 | Bornes et explications du moteur A. |

### Ce qui n'est pas testé

Cinq modules ne sont **jamais chargés** pendant toute la suite :

| Module | Lignes | Risque |
|---|---:|---|
| `api.py` | 397 | **Élevé** — 25 routes, ordre de résolution critique (les routes fixes doivent précéder `/{symbol}`), montage statique. |
| `research/notifications.py` | 196 | **Élevé** — décide *quand notifier le propriétaire* ; un bug est soit du spam, soit un silence total. |
| `synthesis/claude.py` | 127 | Moyen — désactivé par défaut ; le validateur, lui, est testé. |
| `collectors/issuers.py` | 68 | Moyen — orchestration ; le parsing sous-jacent est testé. |
| `collectors/company.py` | 48 | Moyen — idem. |

`scheduler.py` est chargé mais uniquement par effet de bord d'import — aucun test ne vérifie l'enregistrement
ni le déclenchement des 11 jobs. Le moteur A ne compte qu'**un seul** test, alors qu'il alimente la
quasi-totalité de ce que voit l'utilisateur — c'est précisément ce déséquilibre qui a laissé le poids news
mort passer inaperçu.

---

## 13. Analyse critique

### Forces

- **L'honnêteté épistémique est structurelle, pas déclarative.** Le schéma `AnalystReport` n'a pas de champ recommandation : un analyste ne *peut pas* décider. Une donnée absente abaisse la couverture, donc la confiance, et rétracte le score vers le neutre. `fact`/`inference`/`opinion` est porté jusqu'en base. C'est rare, et c'est l'atout le plus difficile à reconstruire ailleurs.
- **La boucle d'apprentissage est réellement prudente.** Beta-Binomial rétracté, multiplicateur borné [0.6 ; 1.4], seuil de 20 échantillons, bande « flat » à 1.5 %. Le système refuse d'apprendre du bruit — l'inverse du réflexe habituel.
- **Isolation des pannes.** Analystes en `degraded_report`, scrapers en cascade, news « best-effort » qui ne bloque jamais l'amorçage des prix.
- **Le LLM est correctement bridé.** Il ne voit que du JSON déjà décidé, ne peut ajouter aucun fait, et sa sortie est validée nombre par nombre avec repli automatique. C'est la bonne architecture, et elle est en place *avant* que le LLM ne soit allumé.
- **Reproductibilité.** Versions par analyste + `ENGINE_VERSION` + `thesis_hash` portant sur la décision et non sur la prose : l'apprentissage ne compare jamais des issues produites par des logiques différentes.

### Faiblesses

- **Deux vérités coexistent.** Le soin mis dans le moteur B (couverture, confiance, honnêteté) est contredit par le moteur A, qui alimente l'écran le plus consulté sans aucun de ces garde-fous. L'utilisateur voit d'abord le moteur le moins rigoureux.
- **Le fossé construction/exposition.** Mémoire de thèse, base de connaissance, note rédigée : trois sous-systèmes achevés, testés, planifiés, écrits en base — et invisibles. Le coût est déjà payé, le bénéfice pas encore encaissé.
- **Duplication d'infrastructure de données.** Deux `NewsContext`, deux chemins Telegram, deux bases, deux fronts (`webapp/` et `webapp_flutter/`).
- **Le socle opérationnel est le maillon faible.** Un projet de ~12 000 lignes avec une boucle d'apprentissage bayésienne repose sur un fichier SQLite sans sauvegarde ni migration.

### Risques techniques

| Risque | Sévérité | Exposition réelle |
|---|---|---|
| Perte du volume Railway | **CRITIQUE** | Perte totale et irréversible : 3 ans d'historique, tous les rapports, tout l'historique d'apprentissage. Aucune sauvegarde. |
| Évolution de schéma | **CRITIQUE** | `create_all` n'ALTER jamais. Ajouter une colonne à une table existante casse la prod silencieusement. |
| Absence d'authentification | **CRITIQUE** | `HANDOVER` : « Auth: none ». Le portefeuille réel du propriétaire, quantités et P/L compris, est exposé à quiconque connaît l'URL. |
| Rupture des scrapers | **MAJEUR** | Cascade de 3 sources en repli, mais toutes scrapées ; un changement de DOM casse la collecte. Timeouts intermittents déjà constatés. |
| Rétention 3 ans glissants | **MAJEUR** | La source ne sert que ~738 séances. La profondeur au-delà ne peut venir que de la collecte forward — d'où la criticité de la sauvegarde. |
| API non testée | **MAJEUR** | Une régression d'ordre de routes ferait passer `opportunities` pour un symbole. |

### Risques métier

- **Contradiction visible pour l'utilisateur.** ACHETER d'un côté, Risqué de l'autre, sur le même titre, le même jour. C'est le risque le plus corrosif : il détruit la confiance dans un produit dont le seul actif *est* la confiance.
- **Quatre digests par jour issus de deux bases divergentes.** Même symptôme, canal Telegram.
- **Signal news structurellement rare.** Après correction, 10 avis sur 11 sont légitimement neutres. Le composant `actualites` restera proche de 50 la plupart du temps. C'est la vérité de cette source — il ne faut pas la « corriger ».
- **Frontière réglementaire.** Le disclaimer est présent et le vocabulaire probabiliste est tenu avec discipline, mais un écran affichant « ACHETER » en capitales frotte contre cette frontière.

### Possibilités de simplification

1. **Supprimer le moteur A ou le réduire à un tri.** Le moteur B fait tout ce qu'il fait, mieux et avec une confiance. La divergence disparaît par construction, pas par synchronisation.
2. **Choisir un seul chemin Telegram.** Le workflow GitHub Actions est un vestige d'avant Railway.
3. **Fusionner les deux `NewsContext`** en une implémentation.
4. **Supprimer `webapp/`** (repli jamais atteint) et trancher sur Streamlit.
5. **Retirer `generate_alerts` de `run_analysis`** ou consommer enfin `signals`.

---

## 14. Roadmap

Priorisation par *risque de perte irréversible* d'abord, *cohérence perçue* ensuite, *valeur nouvelle* en
dernier. Rien de neuf ne devrait être construit avant les jalons 1 et 2.

| # | Chantier | Pourquoi maintenant | Dépend de |
|--:|---|---|---|
| 1 | **Sauvegarde du volume** — job planifié + copie hors Railway | **BLOQUANT** — Seul risque irréversible du projet. Toute autre tâche augmente la valeur de ce qui n'est pas sauvegardé. | — |
| 2 | **Trancher le double Telegram** — désactiver les secrets du workflow ou le scheduler | **BLOQUANT** — Deux bases qui se contredisent. Correction de configuration, pas de code. | — |
| 3 | **Appliquer le backfill news** (`reclassify-news --apply`) | Le moteur B consomme aujourd'hui les anciennes valeurs fausses. Prêt, testé, dry-run vérifié. | 1 (sauvegarde avant écriture) |
| 4 | **Décider du sort du moteur A** — supprimer, ou brancher les news dessus | Décision d'architecture qui conditionne tout le reste. Ne pas brancher les news dans un moteur voué à disparaître. | 3 |
| 5 | **Tests de la couche API** — `TestClient` sur les 25 routes | 397 lignes non couvertes, en façade de tout. Prérequis avant de toucher aux routes. | — |
| 6 | **Authentification minimale** — jeton ou Basic Auth | Le portefeuille réel est public. Effort faible, exposition élevée. | 5 |
| 7 | **Alembic** — baseline sur le schéma actuel | Sans lui, aucune évolution de colonne n'est faisable proprement. Coût croissant avec le temps. | 1 |
| 8 | **Rafraîchir la documentation** — README, statut de `ARCHITECTURE_AI_ANALYST.md` | La doc décrit un produit qui n'existe plus. Premier contact d'un recruteur ou d'un repreneur. | 4 |
| 9 | **Exposer les 3 sous-systèmes invisibles** — mémoire de thèse, connaissance, note rédigée | Le coût est déjà payé. Meilleur ratio valeur/effort une fois le socle sain. | 4, 5 |
| 10 | **Tests de `research/notifications`** | Décide quand le propriétaire est dérangé. Non testé. | — |
| 11 | **Nettoyage** — `webapp/`, Streamlit, `generate_alerts`, `NewsContext` dupliqué, `render.yaml` | Réduit la surface avant toute nouvelle fonctionnalité. | 4 |
| 12 | **Enrichir le `MetricSet`** — RSI, MACD, Bollinger | Valeur nouvelle. L'analyste technique les déclare déjà manquants, l'intégration est prête à les recevoir. | 4, 7 |

### Ordre recommandé

```
SOCLE (avant tout)          COHÉRENCE                  VALEUR
─────────────────           ─────────                  ──────
1 Sauvegarde  ──┬──► 3 Backfill news ──► 4 Sort du moteur A ──┬──► 9 Exposer thèse/connaissance
2 Double Telegram│                              │             │
                 └──► 7 Alembic                 ├──► 8 Doc    ├──► 12 RSI/MACD/Bollinger
5 Tests API ─────────► 6 Auth                   └──► 11 Nettoyage
10 Tests notifications
```

Les jalons 1, 2, 5 et 10 sont indépendants et parallélisables. Le jalon 4 est le **nœud du graphe** : tant que
le sort du moteur A n'est pas tranché, les jalons 8, 9, 11 et 12 travaillent sur une cible mouvante.

---

## Executive Summary

> Une plateforme d'analyse financière d'une rigueur méthodologique inhabituelle, posée sur un socle
> opérationnel fragile, et qui affiche à son unique utilisateur deux verdicts contradictoires sur le même
> titre. Le travail restant n'est pas de construire — c'est de trancher, de consolider et d'exposer.

### Ce qu'est réellement le projet

11 940 lignes de Python, 16 tables, 25 routes API, une PWA Flutter à 7 onglets, 226 tests verts. En
production sur Railway, installée sur le téléphone du propriétaire, notifications push fonctionnelles. Ce
n'est ni un prototype ni une démo : c'est un système vivant, utilisé quotidiennement, qui collecte, analyse,
apprend et notifie sans intervention.

### Niveau de maturité

**Cœur analytique : mature.** Le moteur par horizon, les 10 agents, le débat, les scénarios, la boucle
d'apprentissage bayésienne et le validateur anti-hallucination sont achevés, testés et réfléchis. La
discipline épistémique — jamais inventer, seul le CIO décide, des probabilités jamais des prix, faits séparés
des inférences — est *appliquée par le schéma*, pas promise en commentaire. C'est le genre de choix qu'on ne
rétro-installe pas.

**Socle opérationnel : immature.** Aucune sauvegarde, aucune migration, aucune authentification, aucun test
d'API. L'écart entre le soin porté à la modélisation et le soin porté à l'exploitation est le fait le plus
frappant de cet audit.

**Restitution : en retard sur la construction.** Trois sous-systèmes entiers — la mémoire d'investissement,
la base de connaissance, la note de recherche rédigée — tournent, écrivent en base, sont testés et servis par
l'API… et ne sont appelés par aucune ligne du front. La valeur est produite mais pas livrée.

### Les trois risques principaux

1. **Perte irréversible des données.** Cours, 3 ans d'historique, rapports, prédictions, apprentissage : tout vit dans un fichier SQLite unique sur un volume Railway, sans aucune sauvegarde. La source ne réexpose que ~3 ans glissants — une partie de ce qui serait perdu serait *définitivement* perdue. C'est le seul risque du projet qu'aucun correctif ultérieur ne rattrape.

2. **Contradiction visible.** Deux moteurs de scoring aux entrées, poids et vocabulaires différents sont servis à la même interface : l'onglet Opportunités peut dire ACHETER là où la fiche du même titre dit Risqué. En parallèle, deux chemins Telegram tirent sur **deux bases distinctes** (le volume Railway et un cache GitHub Actions) et peuvent envoyer quatre digests quotidiens structurellement divergents. Pour un produit dont le seul actif est la confiance, c'est corrosif.

3. **Exposition.** Aucune authentification : le portefeuille réel du propriétaire — quantités, prix d'achat, P/L — est accessible à qui connaît l'URL. Et `create_all` n'ALTER jamais une table : la première évolution de colonne cassera la production en silence.

### Recommandations avant toute nouvelle évolution

**Trois actions bloquantes, aucune n'étant une fonctionnalité :**

1. **Sauvegarder le volume** avant toute écriture, backfill news compris. Tant que ce n'est pas fait, chaque jour de collecte augmente la valeur de ce qui n'est pas protégé.

2. **Trancher le double chemin Telegram.** Correction de configuration, coût quasi nul, supprime une source active de contradiction.

3. **Décider du sort du moteur A** — le supprimer au profit du moteur B, ou lui brancher les news. C'est le nœud du graphe de dépendances : documentation, nettoyage, exposition des sous-systèmes et enrichissement du `MetricSet` visent tous une cible mouvante tant qu'il n'est pas tranché. **Recommandation : supprimer.** Le moteur B fait strictement plus, avec une couverture et une confiance ; la divergence disparaît alors par construction, sans travail de synchronisation.

Le backfill des news est prêt, testé et vérifié en dry-run — mais il doit passer *après* la sauvegarde et sa
cible dépend de la décision sur le moteur A.

### Le jugement, en une phrase

La partie difficile — modéliser l'incertitude honnêtement, faire débattre des agents, apprendre sans
surinterpréter le bruit — est faite, et bien faite. La partie facile — sauvegarder, authentifier, migrer,
choisir un seul moteur — ne l'est pas. Le projet ne souffre pas d'un manque d'ambition technique : il souffre
d'un excès de chemins parallèles jamais fermés.
