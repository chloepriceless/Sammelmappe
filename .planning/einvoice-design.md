# Design: E-Rechnung-Ingest (ZUGFeRD / Factur-X / XRechnung)

**Status:** Design-vor-Build (Deliberate Mode). Cycle 1 des Hub-Mandats.
**Datum:** 2026-06-05

## Problem / Ziel
Strukturierte elektronische Rechnungen (E-Rechnung-Pflicht DE ab 2025) tragen die
Rechnungsdaten als maschinenlesbares XML. Die aktuelle Pipeline rät diese Daten per
OCR/Claude aus dem gerenderten Bild — fehleranfällig und kostet API-Calls. Ist ein
strukturiertes XML vorhanden, **parsen wir es deterministisch** → 100 % korrekte
Werte (vendor, brutto, datum, nummer), kein OCR, kein LLM-Call.

Zwei Liefer-Varianten in der Praxis:
1. **Hybrid-PDF (ZUGFeRD / Factur-X):** sieht aus wie ein normales PDF, hat das
   XML als eingebettete Datei (PDF/A-3 Attachment, Name z.B. `factur-x.xml`,
   `zugferd-invoice.xml`, `xrechnung.xml`). Ein privater Bauherr bekommt diese
   zunehmend, weil Firmen ihre Rechnungsstellung komplett auf ZUGFeRD umstellen.
2. **Reines XML (XRechnung):** standalone `.xml`-Datei (oft B2G; selten beim Bauherrn,
   aber trivial mitzunehmen).

## XML-Syntaxen (beide nötig)
- **CII** (UN/CEFACT Cross Industry Invoice) — ZUGFeRD/Factur-X + XRechnung-CII.
  Root: `CrossIndustryInvoice`.
- **UBL** (OASIS Universal Business Language) — XRechnung-UBL. Root: `Invoice`.

Parsing **namespace-robust per local-name** (ElementTree, Namespace ignorieren),
damit Profil-/Versions-Unterschiede (ZUGFeRD 2.x, Factur-X 1.0, EN16931) nicht brechen.

### Feldpfade (per local-name)
**CII (`CrossIndustryInvoice`):**
- Nummer: `ExchangedDocument/ID`
- Datum: `ExchangedDocument/IssueDateTime/DateTimeString` (Format 102 = `YYYYMMDD`)
- Vendor: `.../SellerTradeParty/Name`
- Brutto-Gesamt: `.../SpecifiedTradeSettlementHeaderMonetarySummation/GrandTotalAmount`
- Währung: `.../InvoiceCurrencyCode` (Fallback: currencyID-Attribut am Betrag)

**UBL (`Invoice`):**
- Nummer: `Invoice/ID` (direktes Kind, nicht die IDs in Unterelementen!)
- Datum: `Invoice/IssueDate` (`YYYY-MM-DD`)
- Vendor: `AccountingSupplierParty/Party/PartyLegalEntity/RegistrationName`
  (Fallback: `AccountingSupplierParty/Party/PartyName/Name`)
- Brutto-Gesamt: `LegalMonetaryTotal/TaxInclusiveAmount`
- Währung: `DocumentCurrencyCode` (Fallback: currencyID)

> **amount = GrandTotalAmount / TaxInclusiveAmount** (Brutto-Gesamt inkl. MwSt), NICHT
> DuePayableAmount/PayableAmount (das ist der nach Anzahlung noch offene Betrag).
> Für die Bank-Einreichung zählt der Rechnungs-Brutto-Gesamtbetrag.

## Architektur
Neues Modul `app/einvoice.py`:
- `extract_embedded_xml(pdf_path) -> bytes | None` — eingebettete Rechnungs-XML aus PDF
  ziehen (pypdf `reader.attachments`; Namens-Allowlist + Content-Sniff auf Root-Tag).
- `parse_einvoice_xml(xml_bytes) -> EInvoiceData | None` — defusedxml parsen, CII/UBL
  erkennen, Felder extrahieren. None, wenn kein gültiges E-Rechnung-XML.
- `EInvoiceData` Dataclass (vendor, amount, currency, invoice_date, invoice_number, profile).

Integration in `app/ocr.py extract()` als **Stage 0** (vor TSE-QR + OCR):
```
xml = einvoice.find_einvoice(path, mime)   # PDF-embedded ODER standalone .xml
if xml: return ExtractedInvoice(..., confidence=0.99, engine="einvoice-cii|ubl")  # short-circuit
```
Short-circuit spart die teure PDF-Rasterisierung (TSE-Scan) + OCR + Claude komplett.

`routes/invoices.py`:
- ALLOWED_MIME um `application/xml`, `text/xml` erweitern.
- `_document_type()`: engine-Prefix `einvoice` → doc_type "E-Rechnung".
- Thumbnail für reine .xml: kein Bild → graceful (kein Crash, kein Thumb).

## Sicherheit (R22 — untrusted XML)
Belege können von Dritten stammen (manipuliertes ZUGFeRD-PDF). XML-Parsing ist klassischer
XXE-/Billion-Laughs-Vektor → **`defusedxml`** statt stdlib-ElementTree. Auf 1-GiB-LXC ist
Entity-Expansion-DoS real. defusedxml = minimale, korrekte Absicherung.

## Tests (rein Python, kein Tesseract/Netz)
- `parse_einvoice_xml` mit synthetischem CII-XML → korrekte Felder.
- dito UBL-XML.
- Datum-Parsing CII Format 102 + UBL ISO.
- amount = GrandTotal/TaxInclusive (nicht DuePayable).
- Nicht-E-Rechnung-XML → None.
- defusedxml wehrt Entity-Expansion ab (EntitiesForbidden).
- currency-Fallback.

## Verworfene Alternativen
- **drafthorse / factur-x lib**: schwere Dependency, Profil-Lücken, mehr Pin-Churn.
  stdlib+defusedxml deckt die 5 Header-Felder robust ab. (R12 kein Gold-Plating.)
- **Line-Items jetzt**: Scope-Creep. Später für §35a-Split (Backlog).
- **amount = DuePayable**: falsch bei Anzahlungen; GrandTotal ist der Rechnungswert.

## Verifikation (R31)
Oracle: synthetische ZUGFeRD-CII- + XRechnung-UBL-Fixtures mit bekannten Werten →
Parser liefert exakt diese Werte; Tests grün; `extract()` short-circuitet (kein OCR-Pfad).
Zusätzlich gegen REAL FlateDecode-komprimiertes pypdf-Attachment verifiziert
(engine `einvoice-cii`, amount 2147.24 ohne OCR).

## Security-Härtung (nach Codex-Review, 2026-06-05)
Codex (GPT-5-codex, `codex exec`) gab im Refute-Review zunächst **BLOCK**. Behoben:
1. **Dekompressions-Bombe (Must-Fix):** `reader.attachments` (eager-decode) ersetzt
   durch manuelle `/Names /EmbeddedFiles`-Traversal + `_decode_stream_bounded()` —
   FlateDecode wird mit `zlib.decompressobj().decompress(raw, _MAX_XML_BYTES+1)`
   inflatiert; bei `unconsumed_tail`/Überlauf → skip. Roh-Stream-Cap 30 MiB,
   Decoded-Cap 12 MiB. Nur XML-benannte Attachments werden überhaupt dekodiert.
2. **Mehrere widersprüchliche XMLs:** `find_einvoice` parst alle Kandidaten; bei
   ≠ Beträgen → None (OCR-Fallback) statt stiller Auswahl.
3. **CreditNote:** nicht mehr als positive Rechnung übernommen → None.
4. **Confidence-Gate:** Short-Circuit nur bei amount > 0 UND (Nummer ODER Vendor).
5. **Decoded-Size-Guard** auch für standalone-XML (`parse_einvoice_xml`).
