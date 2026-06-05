# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden hier dokumentiert.
Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionierung nach [Semantic Versioning](https://semver.org/lang/de/).

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
- Mehrdeutige PDFs mit mehreren, widersprüchlichen E-Rechnung-XMLs → Fallback auf
  OCR statt stiller Auswahl.
- Gutschriften / CreditNotes werden NICHT automatisch als (positive) Rechnung
  übernommen (Vorzeichen-Semantik) → Fallback OCR/manuell.
- Short-Circuit nur bei konsistentem Ergebnis (positiver Betrag + Nummer oder
  Vendor), sonst regulärer OCR-Pfad.

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

[1.1.0]: https://github.com/chloepriceless/Sammelmappe/releases/tag/v1.1.0
[1.0.0]: https://github.com/chloepriceless/Sammelmappe/releases/tag/v1.0.0
