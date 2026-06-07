# Design: § 35a EStG Handwerkerbonus-Helfer

**Status:** Design-vor-Build (Deliberate Mode). Hub-Mandat (vdyofkr8, 2026-06-07): autonom durchziehen.
**Kritikalität:** R22 — Schema-Migration (neue Spalten) + Steuer-Logik (publiziert im Produkt). Codex-Refute auf Design UND Diff.

## Problem / Ziel
§ 35a Abs. 3 EStG: **20 % der Arbeitskosten** von Handwerkerleistungen, **max. 1.200 €/Jahr**
(→ max. 6.000 € Arbeitskosten/Jahr) als Steuerermäßigung. Der Helfer rechnet das pro Jahr aus —
und bewahrt den Nutzer vor den drei klassischen Fehlern. **Genau die Caveats sind der Wert.**

## Verifizierte Rechtslage (research-einvoice-legal.md §6, alle [VERIFIED])
- **20 % der Arbeitskosten, max 1.200 €/Jahr** (§ 35a Abs. 3). Quelle gesetze-im-internet.de/estg/__35a.html.
- **Nur Arbeits-/Lohnkosten** (inkl. Maschinen-/Fahrtkosten + USt darauf), **NICHT Material** (Abs. 5).
- **Nur unbare Zahlung** aufs Konto des Leistenden — **Barzahlung NICHT anerkannt** (Abs. 5).
- **NEUBAU NICHT begünstigt**: „Handwerkliche Tätigkeiten im Rahmen einer Neubaumaßnahme sind nicht
  begünstigt; Neubaumaßnahme = alle Maßnahmen im Zusammenhang mit der Errichtung eines Haushalts bis zu
  dessen Fertigstellung." **[VERIFIED]** Autorität = **BMF-Schreiben v. 09.11.2016, BStBl I 2016, 1213**
  (re-verifiziert 2026-06-07 via Haufe/smartsteuer/Finanzverwaltung-MV). → typische Bauphase: § 35a greift
  MEIST NICHT; erst nach Bezug (Restarbeiten, Garten, Carport).
  > **Korrektur (Codex-Refute 2026-06-07):** Das früher zitierte BFH-Az **VI R 24/20** ist NICHT die
  > Neubau-Autorität (anderer Sachverhalt). Im **Produkt nur § 35a EStG + BMF 09.11.2016 zitieren**, kein
  > BFH-Az. research-einvoice-legal.md §6 ist entsprechend zu korrigieren ([UNCERTAIN] auf das Az).
- Haushaltsbezug: Leistung im Haushalt des Steuerpflichtigen (Abs. 4).

## Scope (MVP, R12 — kein Gold-Plating)
**IN:** pro Beleg Arbeitskosten-Anteil + Zahlungsart erfassen; globales Einzugsdatum; deterministische
Eignungs-/Abzugs-Berechnung pro Jahr mit prominenten Caveats; §35a-Übersicht (Endpoint + UI-Card);
Hinweise „warum ein Beleg NICHT zählt".
**OUT (später):** Auto-Split Arbeit/Material aus Line-Items (XML trägt keine Arbeit/Material-Klassifikation
→ bräuchte Heuristik/Per-Zeilen-Tagging); §35a-Block im ZIP-Export; Abs. 2 (haushaltsnahe Dienstleistungen,
20%/max 4.000 €) — bewusst NUR Abs. 3 Handwerker, das ist der Bau-Case.

## Datenmodell
- **Einzugsdatum**: globales **Runtime-Setting** `move_in_date` (settings-KV-Tabelle via runtime_config) —
  KEINE Schema-Änderung. Treibt die „vor/nach Bezug"-Heuristik.
- **Pro Beleg (NEUE Spalten auf `invoices`)**:
  - `labor_amount FLOAT NULL` — Arbeitskosten-Anteil in €.
  - `payment_method VARCHAR NULL` — "transfer" | "cash" | NULL (unbekannt).
  - `payment_date DATE NULL` — Zahlungsdatum (Abflussprinzip § 11 EStG; maßgeblich fürs Jahr).

### Migration (R22-kritisch — `create_all` ALTERT bestehende Tabellen NICHT; race-sicher)
Leichte, idempotente, **race-sichere** Migration in `init_db()` NACH `create_all`:
```python
existing = {row[1] for row in PRAGMA table_info(invoices)}
for col, decl in WANTED.items():
    if col not in existing:
        try: ALTER TABLE invoices ADD COLUMN <col> <decl>
        except OperationalError as e:
            if "duplicate column" not in str(e).lower(): raise   # race: anderer Worker war schneller
```
Spaltennamen/-typen hartcodiert (kein User-Input → keine SQL-Injection). Das `try/except` auf
„duplicate column" macht es **race-sicher** bei parallelem Multi-Worker-Start (Codex-Punkt 4): zwei
Prozesse können beide „fehlt" sehen — der Verlierer schluckt den Duplicate-Fehler statt zu crashen.
Frische DBs bekommen die Spalten via create_all (ORM-Modell trägt die Felder ohnehin).

## Reine Berechnung `app/section35a.py` (testbar, kein DB/Netz)
Konstanten: `RATE = 0.20`, `MAX_DEDUCTION = 1200.0`, `MAX_LABOR = 6000.0`.

`evaluate_invoice(invoice_date, move_in_date, labor_amount, payment_method, payment_date) -> Eval`:
KONSERVATIV — ein Beleg zählt nur als `confirmed`, wenn ALLE Bedingungen explizit erfüllt sind.
Jede Unsicherheit → `excluded` mit Grund (nicht stillschweigend mitgezählt; Codex-Punkte 1+2).
- `eligible = True`, `reasons = []`.
- `labor_amount` None/≤0 → False, reason `"no_labor"`.
- **`payment_method != "transfer"`** → False: reason `"cash"` bei "cash", sonst `"payment_unconfirmed"`
  (NULL/unbekannt zählt NICHT — § 35a Abs. 5 verlangt NACHGEWIESENE unbare Zahlung; „unbekannt" ≠ „unbar").
- Neubau-/Bezug-Heuristik:
  - `move_in_date` None → reason `"no_move_in"` → nicht bestätigt.
  - `invoice_date` None → reason `"no_date"` → nicht bestätigt.
  - beide gesetzt & `invoice_date < move_in_date` → False, reason `"before_move_in"` (Neubauphase n. BMF 09.11.2016).
- `qualifying = labor_amount` nur wenn eligible (alle obigen ok), sonst 0.
- **Jahr = `(payment_date or invoice_date).year`** (Abflussprinzip § 11 EStG; `payment_date` Vorrang).
  Fehlt payment_date → invoice-Jahr mit Flag `year_assumed_from_invoice`.
- Eval trägt: eligible, qualifying, reasons[], year, year_assumed (bool).

`summarize(rows, move_in_date) -> Summary`:
- pro **Kalenderjahr** (Cap ist je Veranlagungszeitraum): Σ qualifying labor, `deduction = min(RATE*labor, MAX_DEDUCTION)`,
  `capped` Flag wenn `RATE*labor > MAX_DEDUCTION`.
- Buckets: `confirmed` (zählt), `excluded` (Summen je Grund: no_labor/cash/payment_unconfirmed/before_move_in/no_move_in/no_date),
  `undated` (kein Jahr ableitbar).
- `estimated_deduction` = Σ Jahres-deductions. **Explizit als konservative Schätzung gelabelt, vom Nutzer + ggf.
  Steuerberater zu prüfen** (die invoice/move_in/payment-Daten sind Nutzereingaben, kein Steuerbescheid).

## API
- `GET /api/section35a` → Übersicht: pro Jahr {labor, deduction, capped}, excluded-Buckets + Gründe,
  move_in_date gesetzt? Gesamt-Schätzung. Liest alle Invoices + move_in_date.
- `routes/settings.py`: `move_in_date` get/set (validiert YYYY-MM-DD).
- `PATCH /api/invoices/{id}`: akzeptiert `labor_amount` (float|null) + `payment_method` (transfer|cash|null);
  in `_invoice_to_dict` mitserialisieren.

## UI
- Settings-Dialog: Feld „Einzugsdatum (für § 35a)".
- Edit-Dialog: Felder „Arbeitskosten-Anteil (€)" + „Zahlungsart" (Überweisung/Bar) — die vorhandene
  Positions-Tabelle (v1.3.0) hilft dem Nutzer, den Lohnanteil abzuschätzen.
- §35a-Übersichts-Card (im Stats-Tab): Schätzung + **prominente Caveats** + „warum X nicht zählt".

### Caveats (prominent im UI — Codex-Punkt 5, vollständig)
1. **Nur Arbeitskosten** (Lohn/Maschine/Fahrt + USt darauf), **kein Material** — Rechnung muss den
   Arbeitsanteil getrennt ausweisen/nachweisbar machen.
2. **Nur tatsächlich UNBAR gezahlt** (Überweisung aufs Konto des Leistenden); Barzahlung wird nicht
   anerkannt. „Zahlungsart unbekannt" zählt NICHT als bestätigt.
3. **Nicht in der Neubauphase**: bis zur Fertigstellung/zum Bezug des Haushalts nicht begünstigt
   (BMF 09.11.2016) — erst danach (Restarbeiten, Garten, Carport).
4. **§ 35a mindert die STEUER, nicht das Einkommen** (20 %, max 1.200 €/Jahr); bei zu geringer
   tariflicher Einkommensteuer **verpufft** ein Teil (keine Erstattung darüber hinaus, kein Vor-/Rücktrag).
5. **Höchstbetrag je Haushalt/Veranlagungszeitraum** — bei mehreren Personen/Haushalten kann die
   Aufteilung komplexer sein (MVP klammert das aus).
6. **Keine Steuerberatung** — die Schätzung beruht auf Nutzereingaben; im Zweifel Steuerberater.

## Verifikation (R31)
Pure-Python-Tests (Oracle = Hand gerechnete Fälle):
- 20%/Cap: labor 2.000 → 400; labor 8.000 → 1.200 (capped True); labor 6.000 → 1.200 (Grenze).
- `payment_method`: "transfer" → bestätigt; "cash" → excluded `"cash"`; NULL → excluded `"payment_unconfirmed"`.
- vor Einzug → excluded `"before_move_in"`, qualifying 0; nach Einzug + transfer → confirmed.
- kein move_in_date → `"no_move_in"`; kein labor → `"no_labor"`; kein Datum → `"no_date"`.
- Jahr: `payment_date` schlägt invoice_date; fehlt payment_date → invoice-Jahr + Flag `year_assumed`.
- Jahres-Gruppierung: zwei Jahre → zwei unabhängige Caps.
- Migration: ALTER greift auf einer DB ohne die Spalten; idempotent + race-sicher (duplicate column geschluckt).
UI: code-review gegen bestehende Edit-/Settings-Patterns (kein Browser → ehrlich gelabelt).

## Codex-Sparring (R22/R26)
1. **Design-Refute DURCHGEFÜHRT 2026-06-07** (GPT-5-codex, codex exec). Verdikt: mehrere valide Schwächen,
   ALLE eingearbeitet: (1) payment NULL zählt nicht (transfer-Pflicht), (2) Jahr nach Zahlungsdatum
   (§11 Abflussprinzip, payment_date), (3) BFH-Az VI R 24/20 raus → BMF 09.11.2016 (re-verifiziert),
   (4) Migration race-sicher (OperationalError/duplicate column), (5) Caveats vervollständigt. → PROCEED.
2. **Diff-Refute** vor Push (Phase A + B). Reviewer in `agents_involved`.

## Phasen (jede committet/gepusht)
- **A (Backend):** Migration + Felder + PATCH + `section35a.py` + Settings move_in_date + Tests.
- **B (API+UI):** `GET /api/section35a` + Settings-Feld + Edit-Felder + Übersichts-Card.
