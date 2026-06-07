# Design: Export-Bundle aufwerten (Kostenaufstellung + §14b-Hinweis)

**Status:** Design-vor-Build (Deliberate Mode). Cycle (autonom, Hub-Mandat).
**Datum:** 2026-06-07

## Problem / Ziel
Das ZIP-Export ist das eigentliche Bank-Lieferobjekt (Sparda MyBaufi & Co.). Aktuell:
flache `uebersicht.csv` (Position/Datei/Steller/Nr/Datum/Kategorie/Betrag/Notiz) +
`README.txt` mit Gesamtsumme. Die App pflegt **Kategorien** (Material, Handwerker,
Sanitär, …) und einen **§14b-Aufbewahrungshinweis** pro Beleg — beides taucht im
Export NICHT auf. Genau eine **Kostenaufstellung nach Gewerk** und der
**Aufbewahrungs-Hinweis** sind aber das, was Bank UND Bauherr im Bundle erwarten.

## Scope (bewusst eng, R12)
1. CSV: neue Spalte **„Aufbewahren bis"** je Beleg (§14b, aus Rechnungsdatum→Jahresende+2).
2. CSV: Block **„Summe je Kategorie"** unter der Gesamtsumme.
3. README.txt: **Kategorie-Aufstellung** + **§14b-Hinweis** (inkl. spätestes
   Aufbewahren-bis-Datum + Erinnerung Zahlungsbeleg/Bauvertrag/Abnahmeprotokoll).

NICHT in Scope: PDF-Deckblatt, Umsortieren der Belege, neue DB-Felder.

## Architektur
`app/routes/export.py` — reine, testbare Helfer (ohne DB/HTTP) herausziehen:
- `_archive_name(idx, inv)` — DRY: ZIP-Dateiname == CSV-Spalte „Datei" (war doppelt inline).
- `category_subtotals(invoices) -> list[(cat, total)]` — sortiert nach Summe desc,
  leere Kategorie → „Ohne Kategorie".
- `_retention_for(inv)` / `latest_retention(invoices)` — spiegelt `routes/invoices._retention_until`
  (Rechnungsdatum, sonst Upload-Zeit) via `utils.retention_until_date`.
- `build_overview_csv(invoices) -> str` · `build_readme_text(...) -> str`.

Route ruft die Helfer; ZIP-Schreibschleife nutzt denselben `_archive_name` →
CSV-„Datei"-Spalte und tatsächlicher Archivname bleiben garantiert identisch.

## Konsistenz / Korrektheit
- Belegreihenfolge unverändert (`invoice_date asc nullslast, id asc`); CSV/ZIP teilen
  dieselbe geordnete Liste + `enumerate(1)` → gleiche Positionsnummern/Dateinamen.
- Betrag-Format CSV unverändert (Komma-Dezimal, keine Tausender, kein €); README nutzt
  `format_eur` (1.234,56 €). `amount is None` zählt als 0 (wie bisher beim Gesamttotal).
- Kategorie-Summen summieren sich (modulo 2-Dezimal-Float) zum Gesamttotal.

## Verifikation (R31)
Oracle: synthetische Beleg-Liste (SimpleNamespace) mit bekannten Kategorien/Beträgen/Daten.
- `category_subtotals` → exakte Summen, Sortierung desc, „Ohne Kategorie" für leere.
- `build_overview_csv` → Header mit „Aufbewahren bis"; SUMME-Zeile; „Summe je Kategorie"-Block.
- `build_readme_text` → enthält Kategorie-Zeilen, §14b-Text, spätestes Aufbewahren-bis.
- `_archive_name` → `NNN_YYYY-MM-DD_slug.ext`, „ohne-datum" wenn Datum fehlt.
- Retention: Rechnungsdatum bevorzugt, sonst created_at.
Tests rein Python (kein Tesseract/Netz/DB). Baseline 57 grün → Ziel >57 grün.

## Verworfene Alternativen
- **PDF-Deckblatt:** mehr Wert, aber zusätzliche Render-Dependency/Komplexität — separater
  Cycle, wenn überhaupt gewünscht. CSV+README decken die Kostenaufstellung ab.
- **Kategorie-Summen als eigene Datei:** unnötige Datei-Inflation; ein Block in der
  bestehenden CSV + README reicht.
