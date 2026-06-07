# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden hier dokumentiert.
Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionierung nach [Semantic Versioning](https://semver.org/lang/de/).

## [1.3.0] — 2026-06-07

### Added
- **E-Rechnung-Positionen im Beleg-Detail.** Bei strukturierten E-Rechnungen
  (ZUGFeRD / Factur-X / XRechnung) zeigt der „Beleg bearbeiten"-Dialog jetzt die
  **einzelnen Rechnungspositionen** (Beschreibung, Menge + Einheit, Netto-Betrag,
  MwSt-%) — direkt aus dem strukturierten XML gelesen, kein OCR/Raten. Read-only.
- Neuer API-Endpoint `GET /api/invoices/{id}/lines` — liefert die Positionen
  **on-demand aus der gespeicherten Originaldatei** (nicht persistiert, daher keine
  DB-Migration und sofort für bereits hochgeladene E-Rechnungen verfügbar). Beträge
  sind **netto** pro Position (EN 16931 BT-131) und summieren sich nicht zum Brutto-
  Gesamtbetrag des Headers. UN/ECE-Einheiten-Codes werden auf kurze Labels gemappt
  (C62 → Stk, HUR → Std, MTK → m², …).

### Security
- Positions-Parsing ist gedeckelt (`_MAX_LINES = 1000`) und vollständig best-effort
  gekapselt: eine fehlerhafte Position kann die Header-Extraktion nicht brechen. Codex-
  Refute-Review vor Merge (kein neuer DoS/Traversal/Regressions-Befund). Der Endpoint
  liest einen server-generierten Dateinamen, kein User-Input → kein Path-Traversal.

## [1.2.0] — 2026-06-07

### Added
- **Kostenaufstellung im Export-Bundle.** Die `uebersicht.csv` und die `README.txt`
  enthalten jetzt eine **Summe je Kategorie** (Material, Handwerker, Dach, …) — die
  Kostenaufstellung nach Gewerk, die Bank und Bauherr ohnehin brauchen.
- **§ 14b-Aufbewahrung wandert mit ins Bundle.** Die CSV hat eine neue Spalte
  **„Aufbewahren bis"** pro Beleg; die README nennt das **späteste** Aufbewahren-bis-
  Datum der Mappe und erinnert an Zahlungsbeleg / Bauvertrag / Abnahmeprotokoll.

### Changed
- CSV-/README-Erzeugung in reine, getestete Helfer (`build_overview_csv`,
  `build_readme_text`, `category_subtotals`) ausgelagert; der ZIP-Dateiname und die
  CSV-Spalte „Datei" teilen sich jetzt denselben Generator (`_archive_name`) und
  können nicht mehr auseinanderlaufen. Keine Verhaltensänderung am Datei-Layout.

## [1.1.1] — 2026-06-05

### Added
- **DSGVO-Transparenz:** Der Einstellungs-Dialog weist jetzt darauf hin, dass bei
  Claude-Vision-Nutzung das Belegbild an Anthropic (USA, Drittland) übertragen wird;
  Tesseract + E-Rechnung-Parsing laufen rein lokal. Neuer „Datenschutz (DSGVO)"-
  Abschnitt in der README. Keine Verhaltensänderung — reine Aufklärung.

## [1.1.0] — 2026-06-05

### Added
- **E-Rechnung-Ingest (ZUGFeRD / Factur-X / XRechnung).** Eingebettete E-Rechnung in
  einem PDF oder eine standalone `.xml`-Datei wird deterministisch aus dem
  strukturierten XML ausgelesen (Rechnungssteller, Brutto-Gesamtbetrag inkl. MwSt,
  Datum, Rechnungsnummer) statt per OCR/Claude geraten — exakt, sofort, kostenlos.
  Unterstützt beide EN-16931-Syntaxen: CII (UN/CEFACT) und UBL (OASIS).
  Neues Dokumenttyp-Badge **„E-Rechnung"** (blau) in Liste + Detail-Dialog.
- `.xml`-Uploads (`application/xml`, `text/xml`) werden jetzt akzeptiert.
- **Aufbewahrungs-Hinweis (§ 14b UStG).** Jeder Beleg zeigt im Detail-Dialog ein
  **„Aufbewahren bis"-Datum** (Rechnungsjahr + 2 Jahre, zum Jahresende) — der
  gesetzliche Aufhänger für private Bauherren bei grundstücksbezogenen Leistungen.
  Tooltip weist darauf hin, auch Zahlungsbeleg / Bauvertrag / Abnahmeprotokoll
  aufzubewahren. API: neues Feld `retention_until` pro Beleg. Keine Steuerberatung.

### Security
- E-Rechnung-XML wird mit `defusedxml` geparst (Schutz gegen XXE / Billion-Laughs
  Entity-Expansion), da Belege von Dritten stammen können.
- **Schutz vor Dekompressions-Bomben:** eingebettete PDF-Attachments werden nicht
  mehr eager dekomprimiert, sondern über die `/EmbeddedFiles`-Struktur traversiert
  und (FlateDecode) mit hartem Output-Limit (12 MiB) inflatiert — verhindert OOM
  auf dem 1-GiB-LXC. Decoded- und Roh-Streamgrößen sind gedeckelt.
- Mehrdeutige PDFs mit mehreren E-Rechnung-XMLs unterschiedlicher Identität
  (Betrag / Nummer / Vendor) → Fallback auf OCR statt stiller Auswahl.
- **Gutschriften** werden NICHT als positive Rechnung übernommen — erkannt sowohl
  am UBL-`CreditNote`-Root als auch am Dokumenttyp-Code (UNTDID 1001, z.B. 381)
  in CII (`ExchangedDocument/TypeCode`) und UBL (`InvoiceTypeCode`) → Fallback.
- Short-Circuit nur bei vollständigem Ergebnis (positiver Betrag UND Nummer UND
  Vendor — alle EN-16931-Pflichtfelder), sonst regulärer OCR-Pfad.

### Notes
- Die E-Rechnungs-*Pflicht* gilt nur B2B; für private Bauherren ist das Feature ein
  Genauigkeits-Bonus, kein Muss. Rechtliche Faktensammlung (mit Quellen) unter
  `.planning/research-einvoice-legal.md`. Security-Review: Codex (2. Meinung).

## [1.0.0] — 2026-05-21

### Added
- Erstveröffentlichung: Mobile-First-Beleg-Capture (PWA), hybride OCR
  (Tesseract + Claude-Vision-Fallback), TSE-QR-Scan für Kassenbelege,
  Multi-Page-PDF, Liste mit Live-Summe, ZIP-Export für Baufinanzierung,
  Status-Tracking, Einreichungs-Historie, Duplikat-Erkennung, Kategorien,
  Auth (Argon2), Proxmox-/Docker-Setup.

[1.3.0]: https://github.com/chloepriceless/Sammelmappe/releases/tag/v1.3.0
[1.2.0]: https://github.com/chloepriceless/Sammelmappe/releases/tag/v1.2.0
[1.1.1]: https://github.com/chloepriceless/Sammelmappe/releases/tag/v1.1.1
[1.1.0]: https://github.com/chloepriceless/Sammelmappe/releases/tag/v1.1.0
[1.0.0]: https://github.com/chloepriceless/Sammelmappe/releases/tag/v1.0.0
