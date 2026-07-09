# Phase 1b — Source Validation

> **Status: VALIDATION — awaiting approval. No collector code written yet.**
> Every field below was **probed live on 2026-07-09** from this machine. Nothing here is assumed.
> Where a probe failed or was not run, it is labelled **UNVERIFIED** rather than guessed.

## 0. Headline findings

| Question | Answer (verified) |
|---|---|
| Are fundamentals reliably available? | **Yes, but only 6 ratios** — `BPA, ROE, Payout, Dividend yield, PER, PBR`, for 3 fiscal years, on the official issuer page. **10/10 sampled issuers had them.** Revenue, net income, margins, ROA, debt/equity, book value are **NOT published** anywhere machine-readable. |
| Where do company profile + fundamentals live? | **The same page** — `/fr/live-market/emetteurs/{code}`. One fetch feeds both collectors. |
| Is there an official MASI/MSI20 index feed? | **No.** `bourse_data/indice` returns a real **HTTP 404** (not a timeout). The equal-weighted proxy stays, still labelled as inference. |
| Macro? | **Yes.** `bkam.ma` homepage embeds 6 full time series as inline JS, incl. `policy_rate`, `inflation_rate`, `eur`, `usd`. |
| Oil / phosphate? | **Not available** from BAM/HCP. Stays `None` → `missing_data`. |
| robots.txt | Casablanca Bourse: `User-agent: * / Allow: /`. BKAM: disallows only `/switch/`. Both targets permitted. |

A guessed BKAM URL (`/Politique-monetaire/Cadre-operationnel/Taux-directeur`) returned **404** — a reminder that no URL in this document is included unless it returned 200.

---

## 1. `company_profiles`

**Primary source (official, Casablanca Bourse — the market operator)**
- Issuer page: `https://www.casablanca-bourse.com/fr/live-market/emetteurs/{emetteur_code}` → **HTTP 200**, ~213 KB, server-rendered HTML.
- Discovery of `{emetteur_code}`: the undocumented Drupal JSON:API proxy already used by this project for price history —
  `GET https://www.casablanca-bourse.com/api/proxy/fr/api/bourse_data/instrument?filter[s][condition][path]=symbol&filter[s][condition][operator]=%3D&filter[s][condition][value]=ATW`
  → attribute **`emetteur_url`** = `/fr/live-market/emetteurs/BCM130843`. **Verified for all 10 sampled symbols.**

**Extractable fields (verified on the ATW issuer page)**

Identity table — note the **naming trap**: it sits under the heading *"Indicateurs clés"* but contains **company identity, not financial ratios**.

| Label on page | Maps to |
|---|---|
| `Nom de la société` | `company_name` |
| `Objet social` | `description` (the business purpose) |
| `Siège social` | `siege_social` |
| `Commissaire aux comptes` | `commissaire_aux_comptes` |
| `Date de constitution` | `date_constitution` |
| `Date d'introduction` | `date_introduction` |
| `Durée de l'Exercice Social` | `duree_exercice_social` |

Plus a **`Principaux actionnaires`** table (verified: 12 rows for ATW, terminating in `Total = 100,00 %`) → `ownership`.

Confirmed section headings: `Dirigeants de l'entreprise`, `Principaux actionnaires`, `Indicateurs clés`, `Opérations financières`, `Franchissements de seuil`, `Dernières publications des émetteurs`. Also 9 PDF links to `media.casablanca-bourse.com`.

> **RESOLVED during implementation (step 7).** `Dirigeants de l'entreprise` is **not a table at all** — it is a grid of `div.keen-slider__slide` cards, each containing two `<p>` elements (role, then name). That is why a `find_next("table")` selector skipped it and matched the shareholders table. Confirmed on **ATW (3), LBV (3), IAM (6)**; `management` is now mapped for real, e.g. `{"role": "Président Directeur Général", "name": "EL KETTANI Mohamed"}`.
> **Not published:** there is no separate "business model" or "competitors" field. `business_model` will remain `NULL` — `description` (Objet social) is what exists. We will not synthesise one.

- **Update frequency:** rarely changes (legal identity, auditor, ownership). → **monthly**.
- **Reliability:** **High.** Official market operator. 10/10 issuer pages reachable. Caveat: the host **intermittently ReadTimeouts** from this network (robots.txt and the JSON:API root each needed 2–4 retries). The existing `scrapers/base.py` tenacity retry already handles this.
- **Parsing difficulty:** **Medium.** Tables have no stable `id`/`class`; must key off the label text in `<td>`/`<th>` and locate the shareholders table by its `Total` row. Brittle to a site redesign.
- **Legal / ethical risk:** **Low.** `robots.txt` = `Allow: /`. Publicly published regulated-issuer information, no auth, no paywall. Mitigations: browser UA (already used), sequential fetches with a delay, weekly/monthly cadence, `raw_payload` stored for audit. ~60–80 issuer fetches per run.
- **Fallback (official, no broker needed):** the `instrument` JSON:API attributes, always present: `libelleFR/EN/AR`, `codeISIN`, `dateIntroduction`, `nombreTitres`, `valeurNominale`. Yields a **degraded** profile (name + ISIN + listing date) with `description = NULL`.
- **Missing data representation:** a missing label → column `NULL`; an unreachable page → **no row inserted at all**. `CompanyProfile.has_data` is then `False` and `company.py` emits its existing honest-unavailable report. **No placeholder text is ever written.**

**Example raw record (ATW, verified 2026-07-09)**
```
Nom de la société          = ATTIJARIWAFA BANK
Siège social               = 2. Bd Moulay Youssef BP: 11141 - Casablanca
Commissaire aux comptes    = Deloitte Audit et Mazars Audit et Conseil
Date de constitution       = 01/01/1911
Date d'introduction        = 13/08/1943
Durée de l'Exercice Social = 12
Objet social               = Toutes opérations de banque. de finance. de c…
Principaux actionnaires    = AL MADA 46,54 % | DIVERS ACTIONNAIRES 19,56 % |
                             WAFA ASSURANCE 6,32 % | … | Total 100,00 %
```

**Target table** `company_profiles` (new; `create_all`-safe)
```
id PK · stock_id FK→stocks.id · emetteur_code · emetteur_url · company_name
description(TEXT, ← Objet social) · business_model(TEXT NULL, not published)
siege_social · commissaire_aux_comptes · date_constitution · date_introduction
duree_exercice_social · ownership_json(TEXT) · management_json(TEXT) · source
source_url · raw_payload(TEXT) · updated_at
UNIQUE (stock_id)
```

---

## 2. `fundamentals`

**Primary source: the SAME issuer page.** A second table, distinct from the identity table, with `<th>` headers `['Ratio', '2025', '2024', '2023']` — i.e. **three fiscal years per issuer**.

**Extractable fields (exact row labels, verified)**

| Row label (verbatim) | Unit | Maps to |
|---|---|---|
| `BPA` | MAD | `eps` |
| `ROE (en %)` | % | `roe` |
| `Payout (en %)` | % | `payout` *(new field)* |
| `Dividend yield (en %)` | % | `dividend_yield` |
| `PER` | ratio | `per` |
| `PBR` | ratio | `pbr` |

**Coverage probe (10 symbols, all FOUND — 10/10, 0 missing, 0 unreachable):**

| Symbol | BPA | ROE % | PER | PBR |
|---|---|---|---|---|
| ATW | 49,48 | 13,23 | 14,76 | 1,95 |
| IAM | 7,93 | 28,11 | 13,75 | 3,87 |
| BCP | 22,15 | 7,77 | 13,09 | 1,02 |
| LBV | 200,00 | 15,77 | 23,00 | 3,63 |
| CIH | 30,60 | 10,15 | 13,56 | 1,38 |
| MNG | 253,02 | 23,96 | 25,29 | 6,06 |
| TGC | 27,46 | 20,95 | 33,00 | 6,91 |
| SID | 69,70 | 13,64 | 31,95 | 4,36 |
| ADH | 1,13 | 4,36 | 31,92 | 1,39 |
| AFM | 72,66 | 103,25 | 16,86 | 17,41 |

> **What is NOT available — do not build analysts that need it.** Revenue, net income, gross/net margin, cash flow, ROA, debt-to-equity, book value, enterprise value. These appear **nowhere** in machine-readable form. They exist only inside issuer **PDF** press releases (9 linked on the ATW page). PDF extraction is high-difficulty, low-reliability and is **explicitly out of scope**. `fundamental.py` must keep declaring these in `missing_data` forever unless a new source is approved.

- **Update frequency:** **annual** (a new fiscal-year column after results). A **weekly** refresh is generous.
- **Reliability:** **High** for the 6 ratios (official, 100% of the sample). The values are the operator's own computation, so they are internally consistent with its price data.
- **Parsing difficulty:** **Medium.** Identify the table by `PER` **and** (`PBR` or `BPA`) in its text; read years from `<th>`; key rows by label. Decimal comma (`49,48`) → the project's existing `parse_number()` already handles Moroccan formats.
- **Legal / ethical risk:** **Low**, identical to §1 (same page, same fetch — collect both in one request).
- **Missing data representation:** the page uses a literal **`-`** for a missing cell (verified: ATW 2024 `Payout` and `Dividend yield` are both `-`). `parse_number("-")` already returns `None` → store `NULL`. **Never store `0.0`.** No fundamentals table on a page → no row → analyst reports unavailable.
- **Fallback (no broker page needed):** if the `PER` cell is `-` but `BPA` is present, `PER` **can be derived** as `current_price / BPA` from data we already hold — this must be stored with `source = "derived"` so the CIO can label it *inference*, not *fact*. `PBR` is **not** derivable (book value is unpublished). Broker/research pages remain the last-resort fallback only, per your constraint, and are **not** proposed here.

**Example raw record (ATW, verified 2026-07-09)**
```
Ratio                | 2025  | 2024  | 2023
BPA                  | 49,48 | 44,18 | 34,90
ROE (en %)           | 13,23 | 13,11 | 11,25
Payout (en %)        | 44,46 |   -   | 47,28     <- '-' = missing, store NULL
Dividend yield (en %)|  3,01 |   -   |  3,59
PER                  | 14,76 | 12,88 | 13,18
PBR                  |  1,95 |  1,69 |  1,48
```

**Target table** `fundamentals` (new; `create_all`-safe)
```
id PK · stock_id FK→stocks.id · fiscal_year INT
eps · roe_pct · payout_pct · dividend_yield_pct · per · pbr      (all NULLable FLOAT)
source · source_url · raw_payload(TEXT) · collected_at
UNIQUE (stock_id, fiscal_year, source)
```
> **Deviation from `ARCHITECTURE_AI_ANALYST.md` §6 (intentional):** that draft listed `revenue, net_income, debt_to_equity, book_value, roa, net_margin, as_of`. Those columns would be **permanently NULL**, so I propose omitting them and keying on `fiscal_year` instead of `as_of`. Requesting your sign-off on this narrowing.

---

## 3. `macro_indicators`

**Primary source (official, Bank Al-Maghrib):** `https://www.bkam.ma` — **HTTP 200**. The homepage embeds complete time series as inline JavaScript:

```js
var policy_rate_json_data = eval('[{x:1780959600000,y:2.250},{x:1781046000000,y:2.250},…]')
```
`x` = epoch **milliseconds**, `y` = value.

**Series verified present, with latest observed values (2026-07-09):**

| JS variable | Indicator | Latest `y` | Unit | Maps to |
|---|---|---|---|---|
| `policy_rate` | Taux directeur | **2.250** | % | `policy_rate` |
| `inflation_rate` | Inflation | **1.200** | % | `inflation` |
| `inflation_underlying_rate` | Inflation sous-jacente | **-0.300** | % | `inflation_underlying` *(new)* |
| `interbank_money_market` | TMP interbancaire | **2.250** | % | `interbank_rate` *(new)* |
| `eur` | EUR/MAD | **10.691** | MAD | `mad_eur` |
| `usd` | USD/MAD | **9.350** | MAD | `mad_usd` |

> **Not available:** **oil** and **phosphate** — absent from BAM. `MacroSnapshot.oil` / `.phosphate` stay `None` and `macro.py` lists them in `missing_data`. HCP was probed: its homepage exposes **no** IPC/inflation link, so **no HCP endpoint is validated** and none is claimed here.

- **Update frequency:** FX daily; inflation monthly; policy rate ~quarterly (BAM board). → collect **daily**, deduplicate on `(indicator, as_of, source)`; a no-change day simply inserts nothing new.
- **Reliability:** **High** as a source (central bank). **Medium** as a *mechanism* — it depends on an inline chart variable on the homepage, which a redesign would break. Failure is detectable (regex finds nothing) and degrades safely to "unavailable".
- **Parsing difficulty:** **Low–Medium.** `re.finditer(r"var (\w+)_json_data\s*=\s*eval\('(\[.*?\])'\)")`, then extract `{x:…,y:…}` pairs with a second regex — the payload is **JS object literal, not JSON** (unquoted keys), so `json.loads` will fail. A 6-line pair extractor suffices. No new dependency.
- **Legal / ethical risk:** **Low.** `robots.txt` = `Disallow: /switch/` only; the homepage is allowed. Public central-bank statistics. One request per day.
- **Fallback (official):** the inflation page `https://www.bkam.ma/fr/Statistiques/Prix/Inflation-et-inflation-sous-jacente` (**HTTP 200**, contains **no** HTML table and **no** embedded series) links a real spreadsheet: `/fr/content/download/771863/8644375/BKAM Inflation et inflation sous jacente.xlsx`. Using it would add an **`openpyxl` dependency** — I recommend **not** adding it unless the inline series disappears.
- **Missing data representation:** a series absent from the page → **no rows inserted** for that indicator; `MacroSnapshot` field stays `None`; `macro.py` reports it in `missing_data`. Oil/phosphate are permanently `None` today.

**Example raw record (verified 2026-07-09)**
```
indicator=policy_rate  as_of=2026-06-09T00:00:00Z  value=2.250   unit=%    source=Bank Al-Maghrib
indicator=eur          as_of=2026-07-08T00:00:00Z  value=10.691  unit=MAD  source=Bank Al-Maghrib
indicator=usd          as_of=2026-07-08T00:00:00Z  value=9.350   unit=MAD  source=Bank Al-Maghrib
indicator=inflation_rate as_of=2026-05-01T00:00:00Z value=1.200  unit=%    source=Bank Al-Maghrib
```

**Target table** `macro_indicators` (new; `create_all`-safe)
```
id PK · indicator VARCHAR(48) (indexed) · as_of DATETIME(tz) (indexed)
value FLOAT · unit VARCHAR(16) · source VARCHAR(64) · source_url · collected_at
UNIQUE (indicator, as_of, source)
```

---

## 4. Constraint compliance

| Constraint | Status |
|---|---|
| SQLite + `create_all` compatible | ✅ all three are **new tables**; `create_all` creates missing tables idempotently |
| Do not ALTER existing tables | ✅ no change to `stocks`, `prices`, `news`, `signals`, `alerts`, `notifications`, `push_subscriptions` |
| Official sources first | ✅ Casablanca Bourse (operator) + Bank Al-Maghrib (central bank). **Zero broker sources proposed.** |
| No fabricated fields | ✅ every field traced to a verified probe; unavailable ones named explicitly |
| No Flutter / no LLM / no deploy | ✅ none required |

**Two bonus findings**
1. `instrument` exposes **`nombreTitres`** (shares outstanding). With price, that yields **market cap → a cap-weighted index proxy**, upgrading the current equal-weighted one (architecture §15 Q1 / Phase-1 limitation #3). Cheap, optional.
2. `bourse_data/indice` is a confirmed **404** → an official index feed does not exist on this API. That **closes open question #1**: keep the proxy, keep labelling it inference.

---

## 5. Recommended implementation order

Driven by one insight: **`company_profiles` and `fundamentals` come from the same HTML page**, so they must share a single fetch (≈60–80 issuer requests/week, not 160).

| Step | Work | Why here |
|---|---|---|
| **1** | `models.py` — add the 3 new tables | Zero-risk (`create_all`, no ALTER). Unblocks everything. |
| **2** | `repository.py` — `upsert_*` + `load_fundamentals/profiles/macro` | Pure DB layer, unit-testable with no network. |
| **3** | `context.py` — replace the 3 stubs (`_load_fundamentals`, `_load_profiles`, `_load_macro`) | **The only engine wiring needed.** Analysts already handle the populated case. Verifiable with hand-seeded rows before any scraper exists. |
| **4** | `collectors/macro.py` (BKAM) | Highest value / lowest risk: **one page, no symbol loop, no pagination**. Lights up `macro.py` immediately. |
| **5** | `collectors/issuer_page.py` — shared fetch + parse → emits *both* a profile and fundamentals rows | Avoids double-fetching. Resolve `emetteur_url` via the `instrument` API, dedupe issuers. |
| **6** | `collectors/company.py` + `collectors/fundamentals.py` — thin persisters over step 5 | Keeps the two feeds independently schedulable and testable. |
| **7** | Verify `Dirigeants` table structure; map `management` only if confirmed | Do not guess a selector. |
| **8** | `scheduler.py` — macro daily, issuer page weekly (fundamentals) / monthly (profile) | Off the report hot path. |
| **9** | Extend `Fundamentals` / `CompanyProfile` / `MacroSnapshot` dataclasses + enrich `fundamental.py` / `company.py` / `macro.py` to use `payout`, `inflation_underlying`, `interbank_rate` | Analysts get richer only once real data exists. |
| **10** | Verify end-to-end: `GET /api/report/{symbol}` now shows the 3 analysts with non-zero confidence and real `data_used`, while unpublished fields stay in `missing_data` | The acceptance gate. |

**Tests to add:** parse fixtures saved from a real issuer page and the BKAM homepage (repo convention: saved-HTML fixtures), covering the `-` → `None` case, a missing fundamentals table, and the JS-literal (non-JSON) macro payload.

---

## 6. Decisions I need from you before coding

1. **Narrow the `fundamentals` table** to the 6 published ratios + `fiscal_year` (dropping the permanently-NULL `revenue`/`net_income`/`debt_to_equity`/`book_value`/`roa`/`net_margin` from architecture §6)? *Recommend: yes.*
2. **Allow the derived `PER`** (`price / BPA`) when the published cell is `-`, stored with `source="derived"` so it is labelled inference, not fact? *Recommend: yes.*
3. **Skip the XLSX inflation fallback** (avoids an `openpyxl` dependency) and rely on the inline BKAM series, degrading to "unavailable" if it breaks? *Recommend: yes.*
4. **Add the cap-weighted index proxy** using `nombreTitres` as a small bonus in Phase 1b, or defer it? *Recommend: defer — it is not blocking.*
