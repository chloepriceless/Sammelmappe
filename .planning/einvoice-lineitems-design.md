# Design: E-Rechnung Line-Items (CII/UBL-Positionen)

**Status:** Design-vor-Build (Deliberate Mode). Hub-Mandat-Cycle (autonom).
**Datum:** 2026-06-07
**Kritikalität:** R22 — berührt den security-reviewten, untrusted-XML-Parser → Codex-Refute vor Push.

## Problem / Ziel
Strukturierte E-Rechnungen (ZUGFeRD/Factur-X/XRechnung) tragen neben den Header-Feldern
die **einzelnen Rechnungspositionen** (BG-25, EN 16931). Aktuell liest die App nur die
Header (vendor/brutto/datum/nummer). Die Positionen sind (a) ein konkreter Anzeige-Mehrwert
im Detail-Dialog (was stand auf der Rechnung), (b) das Fundament für den späteren
§35a-Arbeit/Material-Split. Nur die deterministisch im XML vorhandenen Positionen werden
gelesen — KEIN OCR/LLM-Raten von Positionen (das wäre unzuverlässig).

## Scope (eng, R12)
- Positionen aus CII + UBL deterministisch extrahieren: Position, Beschreibung, Menge+Einheit,
  Einzelpreis (netto), **Netto-Zeilenbetrag**, MwSt-%.
- Anzeige read-only im „Beleg bearbeiten"-Dialog, NUR für E-Rechnungen.
- NICHT in Scope: Positionen in den Export-CSV (Scope-Creep; kommt mit §35a), Editierbarkeit
  der Positionen, neue DB-Tabelle.

## Storage-Entscheidung: DERIVE-ON-DEMAND, keine Schema-Änderung
Neuer read-only Endpoint `GET /api/invoices/{id}/lines` re-parst die **gespeicherte
Originaldatei** (XML bzw. eingebettetes ZUGFeRD-XML) und liefert die Positionen.

**Warum nicht persistieren (verworfene Alternativen):**
- *Neue Tabelle `invoice_line`*: `Base.metadata.create_all` legt fehlende Tabellen an, ALTERT
  aber bestehende nicht — eine neue Tabelle wäre migrations-sicher. ABER: Positionen sind
  **abgeleitete Daten** aus einer Datei, die wir ohnehin dauerhaft vorhalten. Persistenz brächte
  Sync-Risiko (Re-OCR/Edit) ohne Nutzen, solange niemand die Positionen mutiert.
- *JSON-Spalte auf invoices*: `create_all` altert die Tabelle NICHT → Spalte fehlt auf
  bestehenden DBs → Migration nötig. Schlechtester Weg.
- **Derive-on-demand gewählt:** 0 Migration, immer konsistent mit der Quelle, funktioniert
  sofort für bereits hochgeladene E-Rechnungen (kein Backfill). Parse-Kosten vernachlässigbar
  (XML < 12 MiB, Microsekunden). §35a-Per-Line-Klassifikation braucht mutablen State → DANN
  eine Tabelle einführen, wenn §35a wirklich gebaut wird (YAGNI).

## Feldpfade (per local-name, EN 16931 BG-25)
**CII** — `SupplyChainTradeTransaction/IncludedSupplyChainTradeLineItem` (wiederholt):
- Position: `AssociatedDocumentLineDocument/LineID`
- Beschreibung: `SpecifiedTradeProduct/Name`
- Menge: `SpecifiedLineTradeDelivery/BilledQuantity` (Attr `unitCode`)
- Einzelpreis netto: `SpecifiedLineTradeAgreement/NetPriceProductTradePrice/ChargeAmount`
- Netto-Zeile: `SpecifiedLineTradeSettlement/SpecifiedTradeSettlementLineMonetarySummation/LineTotalAmount`
- MwSt-%: `SpecifiedLineTradeSettlement/ApplicableTradeTax/RateApplicablePercent`

**UBL** — `InvoiceLine` (wiederholt):
- Position: `ID`
- Menge: `InvoicedQuantity` (Attr `unitCode`)
- Netto-Zeile: `LineExtensionAmount`
- Beschreibung: `Item/Name` (Fallback `Item/Description`)
- Einzelpreis netto: `Price/PriceAmount`
- MwSt-%: `Item/ClassifiedTaxCategory/Percent`

> Zeilenbeträge sind **NETTO** (BT-131). Sie summieren sich NICHT zum Brutto-Gesamt des Headers
> — das ist korrekt und wird im UI klargestellt („Positionen netto"). Einheiten sind UN/ECE-Rec-20-
> Codes (C62=Stück, HUR=Std, MTK=m², …) → kleine Mapping-Tabelle auf deutsche Labels.

## Architektur
`app/einvoice.py`:
- `@dataclass EInvoiceLine(position, description, quantity, unit, unit_label, unit_price, net_amount, vat_percent)`.
- `EInvoiceData.lines: list[EInvoiceLine]` (default_factory list).
- `_parse_cii_lines(txn) / _parse_ubl_lines(root)` — gedeckelt auf `_MAX_LINES = 1000`, best-effort,
  werfen nie. In `_parse_cii/_parse_ubl` aufgerufen.
- `_UNIT_LABELS` Mapping (häufige Bau-Einheiten).

`app/routes/invoices.py`:
- `GET /{invoice_id}/lines` → `{available, profile, truncated, lines:[...]}`. Re-Parse via
  `einvoice.find_einvoice(stored_path, mime)`; Pfad aus DB-Filename (kein User-Input → kein Traversal).

`static/` — `#edit-lines`-Container nach `#edit-ocr-meta`; `openEdit` lazy-fetcht bei
`doc_type==='E-Rechnung'`, rendert kompakte Tabelle (Pos · Beschreibung · Menge · Netto),
scheitert still. Minimal-CSS.

## Sicherheit (R22 — untrusted XML)
- **`_MAX_LINES` Cap** gegen riesige Positionslisten (DoS/Payload-Blow-up); `truncated`-Flag.
- Parsing best-effort, fängt alle Exceptions (wie der bestehende Parser).
- Endpoint liest DB-abgeleiteten Pfad, nicht User-Input → kein Path-Traversal.
- Bestehende Schutzschichten gelten weiter (defusedxml, 12-MiB-Decoded-Cap, Dekompressions-Bombe).

## Verifikation (R31)
Oracle: synthetische CII- + UBL-Fixtures mit bekannten Positionen → Parser liefert exakt
diese Positionen/Mengen/Beträge/Einheiten-Labels. Tests rein Python:
- CII-Positionen (2+ Zeilen, Menge+Einheit, Netto, MwSt-%).
- UBL-Positionen (Item/Name + Description-Fallback).
- `_MAX_LINES`-Cap greift + `truncated`.
- Einheiten-Mapping (C62→Stück, HUR→Std, fallback = roher Code).
- Kein-Positionen-XML → leere lines, `available` korrekt.
- Bestehende Header-Tests bleiben grün (lines additiv, Default leer).
UI: code-review gegen bestehende `openEdit`-Patterns (kein Browser hier → ehrlich als
„code-verifiziert, nicht browser-getestet" gelabelt). Endpoint-Logik über Parser-Tests abgedeckt.

## Codex-Sparring (R22/R26) — DURCHGEFÜHRT 2026-06-07
Diff (einvoice.py + invoices.py) per `codex exec --sandbox read-only` mit Refute-Prompt
(GPT-5-codex, Diff inline, da bwrap die Repo-Files hier nicht lesen kann).
**Verdikt: PROCEED** — kein neuer Header-Break, kein unbounded DoS, kein neuer Traversal,
keine Regression an find_einvoice/Conflict/Credit-Note. Zwei Hinweise, beide geprüft:
1. *Path-Traversal*: nicht ausnutzbar — `inv.filename` ist server-generiert
   (`YYYYMMDD_HHMMSS_<hex><suffix>`, suffix = `Path(...).suffix` → nur Extension); das
   `invoices_dir/filename`-Muster nutzen bereits /file, /thumbnail, export.
2. *unit_price-Semantik*: roher BT-146-Nettopreis (ohne BaseQuantity/Allowances) — korrekt
   als BT-146 gelabelt, wird im UI NICHT angezeigt (angezeigt = `net_amount`, autoritative
   Zeilensumme BT-131). Kein Nutzer-Korrektheitsproblem.
