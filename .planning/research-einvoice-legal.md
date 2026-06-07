# Recherche: E-Rechnung & Rechts-/Steuer-Aspekte für "Sammelmappe" (BauRechnungScanner)

**Stand: 2026-06-05** · Zielgruppe: PRIVATE Bauherren, die Bau-Belege bündeln und bei der Baufinanzierung einreichen.
Confidence-Labels: **[VERIFIED]** = Primärquelle od. ≥2 unabhängige reputable · **[LIKELY]** = eine reputable Sekundärquelle · **[UNCERTAIN]** = umstritten/Einzelquelle/nicht abrufbar.

> **Kein Steuerrechts-Rat.** Faktensammlung für Produktentscheidungen. Steuer/Recht ändern sich.

---

## TL;DR — die vier produktrelevanten Kernpunkte

1. **E-Rechnungspflicht gilt NICHT für B2C** (Rechnungen an Privatpersonen). **[VERIFIED]** — Unsere Bauherren erhalten von Handwerkern als Verbraucher i.d.R. weiterhin Papier/PDF. Strukturiertes ZUGFeRD/XRechnung-Parsing ist ein **Nice-to-have**, kein gesetzlicher Zwang für die Zielgruppe.
2. **§ 14b Abs. 1 S. 5 UStG: 2 Jahre Aufbewahrungspflicht für Privatpersonen** bei steuerpflichtigen Werklieferungen/sonstigen Leistungen **im Zusammenhang mit einem Grundstück**. **[VERIFIED]** — Das ist DER gesetzliche Aufhänger für unsere Zielgruppe. Bußgeld bis 500 € bei Verstoß (§ 26a UStG). Der Leistende muss auf der Rechnung darauf hinweisen.
3. **§ 35a EStG Handwerkerbonus gilt NICHT für Neubau/Neuerrichtung**, nur für Maßnahmen am bestehenden, bereits bezogenen Haushalt. **[VERIFIED]** (BFH VI R 24/20, BMF 09.11.2016). Viele Nutzer bauen NEU → für die Bauphase greift § 35a meist nicht; erst nach Einzug.
4. **Claude-Vision = Drittlandtransfer (USA)** nach Art. 44 ff. DSGVO. Abgesichert über EU-US Data Privacy Framework (Anthropic gelistet) + SCC; **AVV nur bei kommerziellen API-Konten**, nicht bei Consumer-Plänen. **[VERIFIED/LIKELY]** — Bei selbst-gehosteter privater Nutzung greift die DSGVO i.d.R. nicht (Haushaltsausnahme Art. 2 Abs. 2 lit. c), aber Nutzer sollten wissen: Belegbilder verlassen die EU.

---

## 1. E-Rechnungspflicht Deutschland ab 2025

**Rechtsgrundlage:** Wachstumschancengesetz → Neufassung § 14 UStG für Umsätze nach dem 31.12.2024. Kern: obligatorische E-Rechnung bei **inländischen B2B-Umsätzen**. **[VERIFIED]**
Quelle: gesetze-im-internet.de/ustg_1980/__14.html · BMF-FAQ. Abruf 2026-06-05.

**Definition E-Rechnung (§ 14 Abs. 1 S. 3 UStG):** „eine Rechnung, die in einem strukturierten elektronischen Format ausgestellt, übermittelt und empfangen wird und eine elektronische Verarbeitung ermöglicht" — und die der **EN 16931** entspricht. **[VERIFIED]**

**B2C / Privatpersonen — KERNFRAGE:** Die Pflicht gilt **NICHT** für Rechnungen an Endverbraucher (B2C). BMF wörtlich: „Die Regelungen zur verpflichtenden E‑Rechnung gelten nur, wenn überhaupt eine umsatzsteuerliche Pflicht zur Ausstellung einer Rechnung besteht. Daher gelten die Regelungen nicht bei Rechnungen an Endverbraucher (sogenannte B2C‑Umsätze)." § 14 Abs. 2 UStG adressiert nur Unternehmer/juristische Personen. **[VERIFIED]**
Quelle: bundesfinanzministerium.de/Content/DE/FAQ/e-rechnung.html · gesetze-im-internet.de/ustg_1980/__14.html. Abruf 2026-06-05.

**Empfangspflicht:** Ab 01.01.2025 müssen **alle** inländischen Unternehmer (auch Kleinunternehmer) E-Rechnungen empfangen können — ein E-Mail-Postfach genügt. **[VERIFIED]**

**Ausstellungs-Übergangsfristen (B2B):** **[VERIFIED]**
| Zeitraum | Regel | Bedingung |
|---|---|---|
| 01.01.2025 – 31.12.2026 | Wahlrecht: E-Rechnung ODER Papier/PDF (mit Zustimmung) | alle Aussteller |
| 01.01.2025 – 31.12.2027 | dito (verlängert) | Aussteller mit **Vorjahresumsatz ≤ 800.000 €** |
| 01.01.2025 – 31.12.2027 | EDI-Verfahren erlaubt (auch nicht-EN-16931) | alle |
| **ab 01.01.2028** | **E-Rechnung obligatorisch** | alle |
Quelle: BMF-FAQ. Abruf 2026-06-05.

**Zulässige Formate:** XRechnung und **ZUGFeRD ab Version 2.0.1** (mit **Ausnahme** der Profile **MINIMUM** und **BASIC-WL** — diese sind NICHT EN-16931-konform). Hybride Formate (XML + menschenlesbares PDF in einer Datei) zulässig. **[VERIFIED]**
Quelle: BMF-FAQ; BMF-Schreiben 15.10.2024. Abruf 2026-06-05.

**Maßgebliches BMF-Schreiben:** **BMF v. 15.10.2024**, Az. **III C 2 - S 7287-a/23/10001 :007**, BStBl I 2024 S. 1320. Aktualisiert/ergänzt durch **BMF v. 15.10.2025**. **[VERIFIED]**
Quelle: bundesfinanzministerium.de (Download 2025-10-15-einfuehrung-obligatorische-e-rechnung.pdf); datenbank.nwb.de/Dokument/1046425. Abruf 2026-06-05.

---

## 2. ZUGFeRD / Factur-X (technisch)

**Herausgeber:** FeRD (Forum elektronische Rechnung Deutschland); Factur-X ist der frz. Zwilling (FNFE-MPE). Gemeinsamer Standard, jüngste Version ZUGFeRD 2.3 / Factur-X 1.0.07. **[VERIFIED]**

**Profile (aufsteigender Datenumfang):** MINIMUM, BASIC-WL, BASIC, **EN 16931** (früher COMFORT), EXTENDED, XRECHNUNG. **[VERIFIED]**
- EN-16931-konform (umsatzsteuerlich gültige E-Rechnung): **BASIC, EN 16931, EXTENDED, XRECHNUNG**.
- NICHT konform: **MINIMUM, BASIC-WL** (nur Buchhilfe, keine vollständige Rechnung).
Quelle: zugferd.org; de.wikipedia.org/wiki/ZUGFeRD; FeRD/gefeg-Profildoku. Abruf 2026-06-05.

**Syntax:** CII (Cross Industry Invoice, UN/CEFACT) als XML. **[VERIFIED]**

**Einbettung:** Hybrid = **PDF/A-3** (ISO 19005-3) mit eingebettetem XML-Attachment. Übliche Dateinamen: **`factur-x.xml`** (ZUGFeRD ≥ 2.1 / Factur-X), historisch `ZUGFeRD-invoice.xml` (2.0), **`xrechnung.xml`** (XRECHNUNG-Profil). Verknüpfung über **`AFRelationship`** = `Alternative` (= `/Data`-Beziehung) im PDF. **[VERIFIED]**
Quelle: pdflib.com/pdf-knowledge-base/zugferd-and-factur-x; zugferd.org. Abruf 2026-06-05.

**EN 16931 / Richtlinie 2014/55/EU:** definiert das semantische Datenmodell der E-Rechnung; ZUGFeRD-Profile ab BASIC und XRechnung bilden sie ab. **[VERIFIED]**

> **Produkt-Hinweis:** Für robustes Parsing eingebetteter E-Rechnungen: PDF/A-3-Attachments extrahieren (alle Kandidatennamen prüfen, nicht nur einen), dann CII-XML mappen. Da die Zielgruppe B2C ist, kommen eingebettete E-Rechnungen vor allem von größeren Lieferanten/Bauträgern vor, nicht flächendeckend.

---

## 3. XRechnung

**Herausgeber:** **KoSIT** (Koordinierungsstelle für IT-Standards) im Auftrag des IT-Planungsrats. **[VERIFIED]**
**Syntaxen:** zwei gleichwertige — **UBL** (OASIS, Basis für Peppol BIS Billing 3.0) und **CII** (UN/CEFACT). **[VERIFIED]**
**B2G-Rechtsgrundlage:** **E-Rechnungs-Verordnung (ERechV)** v. 06.09.2017, setzt EU-Richtlinie **2014/55/EU** in Bundesrecht um; Pflicht zur E-Rechnung an Bundesbehörden. **[VERIFIED]**
**Leitweg-ID:** Pflicht-Routing-Feld nur im **B2G**-Kontext (nicht B2B/B2C). **[VERIFIED]**
Quelle: xeinkauf.de/xrechnung; eu-rechnung.de; banqup.com. Abruf 2026-06-05.

---

## 4. GoBD

**Maßgebliches BMF-Schreiben:** **BMF v. 28.11.2019**, Az. **IV A 4 - S 0316/19/10003 :001**; aktualisiert durch BMF v. 11.03.2024 (und Folge-Anpassungen 2025). **[VERIFIED]**
Quelle: finanzamt.bayern.de (2019-11-28-GoBD-1.pdf); datenbank.nwb.de/Dokument/800308; bundesfinanzministerium.de (2024-03-11-aenderung-gobd.pdf). Abruf 2026-06-05.

**Geltungsbereich — KERNFRAGE:** GoBD richten sich an **buchführungs- und aufzeichnungspflichtige Steuerpflichtige** (primär Unternehmer/Bilanzierer). **Reine PRIVATPERSONEN ohne Buchführungs-/Aufzeichnungspflicht fallen NICHT darunter.** **[VERIFIED]** — Für unsere Kern-Zielgruppe (private Bauherren) ist GoBD damit i.d.R. nicht direkt anwendbar.
Quelle: GoBD-BMF-Schreiben Rz. 1 ff.; ihk-muenchen.de. Abruf 2026-06-05.

**Kernanforderungen (für Betroffene):** Unveränderbarkeit/Festschreibung, Nachvollziehbarkeit & Nachprüfbarkeit, Vollständigkeit, Richtigkeit, zeitgerechte Erfassung, Ordnung, Verfügbarkeit/Datenzugriff; **Verfahrensdokumentation** inkl. „Organisationsanweisung" für das ersetzende Scannen von Papierbelegen. **[VERIFIED]**

> **Produkt-Hinweis:** GoBD ist für die Privat-Zielgruppe kein Muss, aber ein **Vertrauens-/Qualitätsmerkmal**, falls Kleinunternehmer/Selbstbauer mit Gewerbe das Tool nutzen: Original-Datei unverändert speichern (Hash/Versionierung), Änderungen protokollieren, ggf. kurze Verfahrensdoku anbieten.

---

## 5. Aufbewahrungsfristen

### § 147 AO (Geschäftsunterlagen — Unternehmer)
- **10 Jahre:** Bücher, Aufzeichnungen, Inventare, Jahresabschlüsse, Lageberichte, Zollunterlagen. **[VERIFIED]**
- **8 Jahre:** **Buchungsbelege** (§ 147 Abs. 1 Nr. 4) — verkürzt von 10 auf 8 Jahre durch **Bürokratieentlastungsgesetz IV**. **[VERIFIED]**
- **6 Jahre:** sonstige steuerbedeutsame Unterlagen, Handels-/Geschäftsbriefe.
- **Ab wann 8 Jahre:** gilt ab 01.01.2025 für alle Buchungsbelege, deren Frist am **31.12.2024 noch nicht abgelaufen** war (Anwendungsregel Art. 97 § 19a EGAO). **[VERIFIED]**
- Fristbeginn: Ablauf des Kalenderjahres der Entstehung/Ausstellung. Verlängerung, solange steuerlich relevant (Festsetzungsfrist nicht abgelaufen, laufende Prüfung etc.). **[VERIFIED]**
Quelle: gesetze-im-internet.de/ao_1977/__147.html; deloitte-tax-news.de (BEG IV); haufe.de. Abruf 2026-06-05.

### § 14b UStG — PRIVATE Empfänger (ZIELGRUPPE!)
**§ 14b Abs. 1 S. 5 UStG:** Bei **steuerpflichtiger Werklieferung oder sonstiger Leistung im Zusammenhang mit einem GRUNDSTÜCK** an einen **Nichtunternehmer** (oder für den nichtunternehmerischen Bereich) muss der **Leistungsempfänger** die **Rechnung, einen Zahlungsbeleg oder eine andere beweiskräftige Unterlage** (z.B. Bauverträge, Abnahmeprotokolle nach VOB) **2 Jahre** aufbewahren. **[VERIFIED]**
Gesetzeswortlaut: „…hat der Leistungsempfänger die Rechnung, einen Zahlungsbeleg oder eine andere beweiskräftige Unterlage zwei Jahre … aufzubewahren."
- **Fristbeginn:** Ablauf des Kalenderjahres der Rechnungs-/Belegausstellung. **[VERIFIED]**
- **Hinweispflicht des Leistenden:** § 14 Abs. 4 S. 1 Nr. 9 UStG verlangt in diesen Fällen einen **Hinweis auf der Rechnung** auf die Aufbewahrungspflicht des Empfängers. **[VERIFIED]**
- **Bußgeld bei Verstoß:** bis **500 €** nach § 26a Abs. 2 UStG (Privatperson). **[LIKELY]** (mehrere reputable Sekundärquellen; § 26a-Wortlaut nicht direkt geprüft)
Quelle: gesetze-im-internet.de/ustg_1980/__14b.html; haufe.de (Schwarz/Widmann/Radeisen § 14b); rechnungswesen-info.de. Abruf 2026-06-05.

> **Produkt-Hinweis:** Genau hierfür ist „Sammelmappe" da. Sinnvoll: pro Beleg ein „Aufbewahren-bis"-Datum (Rechnungsjahr + 2 Jahre) berechnen/anzeigen; Erkennung des Pflicht-Hinweises auf Grundstücksleistungen; Hinweis an den Nutzer, dass NICHT nur die Rechnung, sondern auch Zahlungsbeleg/Bauvertrag/Abnahmeprotokoll aufzubewahren ist.

---

## 6. § 35a EStG (Handwerkerbonus)

**Steuerermäßigung:** **20 % der Arbeitskosten**, **höchstens 1.200 € pro Jahr** (§ 35a Abs. 3 EStG) → max. anerkannte Arbeitskosten 6.000 €/Jahr. **[VERIFIED]**
Quelle: gesetze-im-internet.de/estg/__35a.html. Abruf 2026-06-05.

**Voraussetzungen (§ 35a Abs. 5 EStG):** **[VERIFIED]**
- ordnungsgemäße **Rechnung** erhalten,
- **Zahlung auf das Konto des Leistenden** (unbar/Überweisung) — **KEINE Barzahlung** anerkannt,
- nur **Arbeits-/Lohnkosten** (inkl. Maschinen-/Fahrtkosten + USt darauf), **NICHT Material**.

**Haushaltsbezug:** Leistung muss „in einem … Haushalt des Steuerpflichtigen" (EU/EWR) erbracht werden (§ 35a Abs. 4 S. 1). **[VERIFIED]**

**NEUBAU — KERNFRAGE:** Handwerkerleistungen **im Rahmen einer Neubaumaßnahme sind NICHT begünstigt**. Begünstigt sind nur Maßnahmen am **bestehenden, bereits bezogenen Haushalt**. **[VERIFIED]**
- Neubaumaßnahme = alle Maßnahmen im Zusammenhang mit der Errichtung eines Haushalts **bis zu dessen Fertigstellung** (BMF v. 09.11.2016, Az. IV C 8 - S 2296-b/07/10003, BStBl I 2016 S. 1213).
- Nach Einzug/Bezug erbrachte Leistungen (z.B. Verputzarbeiten, erstmalige Gartenanlage, Carport-Errichtung) sind begünstigt.
Quelle: **BMF-Schreiben v. 09.11.2016, BStBl I 2016, 1213** (autoritativ, re-verifiziert 2026-06-07 via Haufe/smartsteuer); deloitte-tax-news.de (Anlage BMF 35a).

> **KORREKTUR 2026-06-07 (Codex-Refute + Web-Re-Verify):** Das frühere Az **BFH VI R 24/20 (20.04.2023)** als
> Neubau-Autorität ist **[UNCERTAIN] / vermutlich falsch attribuiert** — anderer Sachverhalt (eher
> Rechnungsnachweis/haushaltsnahe Leistungen). Die belastbare Neubau-Autorität ist das **BMF-Schreiben
> 09.11.2016** (Finanzverwaltung). Im Produkt wird daher KEIN BFH-Az zitiert, nur § 35a EStG + BMF 09.11.2016.

> **Produkt-Hinweis:** Für die viele Nutzer betreffende **Neubauphase greift § 35a meist nicht** — wichtige Erwartungssteuerung. Erst nach Einzug (z.B. Restarbeiten, Garten, Carport) wird es relevant. Produkt könnte: Belege nach „Bauphase vor Bezug" vs. „nach Bezug" taggen; bei nach-Bezug-Belegen Arbeitskosten separat ausweisen + auf unbare Zahlung achten (Barzahlung = nicht absetzbar). Hinweis: § 35a betrifft selbstnutzende Eigentümer; für reine Bau-/Finanzierungs-Einreichung bei der Bank ist es ein Bonus-Feature.

---

## 7. DSGVO bei Beleg-/Rechnungsverarbeitung

**Rechnungsdaten = personenbezogene Daten** (Namen, Adressen, ggf. Bankdaten, Leistungsbeschreibung). Verarbeitung braucht Rechtsgrundlage (Art. 6 DSGVO) — im B2B/Unternehmenskontext meist Vertrag/rechtliche Verpflichtung; im rein privaten Eigengebrauch greift die **Haushaltsausnahme (Art. 2 Abs. 2 lit. c DSGVO)** → DSGVO i.d.R. nicht anwendbar, **solange ausschließlich persönlich/familiär** genutzt. **[LIKELY]**

**Drittlandtransfer Claude/Anthropic-API (USA) — KERNFRAGE:** **[VERIFIED/LIKELY]**
- Versand von Belegbildern an die Claude-Vision-API = **Datenübermittlung in die USA** → Art. 44 ff. DSGVO.
- Anthropic ist unter dem **EU-US Data Privacy Framework** gelistet (Angemessenheitsbeschluss der EU-Kommission vom 10.07.2023, Stand 2025/2026 in Kraft); zusätzlich **SCC** als „Belt-and-Braces". **[LIKELY]** — DPF-Status pro Anbieter auf dataprivacyframework.gov verifizierbar.
- **AVV (Art. 28 DSGVO):** Anthropic bietet Auftragsverarbeitungsvertrag **für kommerzielle API-Konten** an; **Consumer-Pläne (Free/Pro/Max/Team) haben i.d.R. keinen AVV**. API-Eingaben werden standardmäßig **nicht** fürs Modelltraining genutzt. **[LIKELY]**
- Schrems-II-Restrisiko: SCC allein genügen nicht ohne zusätzliche Maßnahmen; DPF mindert das, ist aber juristisch angreifbar. **[VERIFIED]**
Quelle: cortina-consult.com; compound.law/anthropic-dsgvo; cierra.ai; EU-Kommission DPF-Adequacy. Abruf 2026-06-05.

> **Produkt-Hinweis (selbstgehostet, privat):**
> - Tesseract läuft **lokal** (kein Transfer). Claude-Vision ist **opt-in-Fallback** → Default-Empfehlung: lokales OCR zuerst, Claude nur bewusst.
> - **Transparenz im UI:** klar kennzeichnen, dass Belegbilder bei Claude-Nutzung in die USA gehen; Toggle „Claude bevorzugt verwenden" entsprechend mit Hinweis versehen.
> - Wer als **Unternehmer/Selbstständiger** das Tool nutzt: API-Key über **kommerzielles Anthropic-Konto mit AVV** verwenden; ggf. Schwärzen/Minimieren sensibler Felder vor Upload anbieten.
> - **Datensparsamkeit:** nur das nötige Belegbild senden; Möglichkeit, sensible Bereiche zu maskieren, ist ein DSGVO-Pluspunkt.

---

## Quellen (Abruf jeweils 2026-06-05)

**Primärquellen (Gesetze/BMF/BFH/EU):**
- § 14 UStG: https://www.gesetze-im-internet.de/ustg_1980/__14.html
- § 14b UStG: https://www.gesetze-im-internet.de/ustg_1980/__14b.html
- § 35a EStG: https://www.gesetze-im-internet.de/estg/__35a.html
- § 147 AO: https://www.gesetze-im-internet.de/ao_1977/__147.html
- BMF-FAQ E-Rechnung: https://www.bundesfinanzministerium.de/Content/DE/FAQ/e-rechnung.html
- BMF-Schreiben E-Rechnung 15.10.2024 (PDF, Az. III C 2 - S 7287-a/23/10001 :007): https://www.bundesfinanzministerium.de/Content/DE/Downloads/BMF_Schreiben/Steuerarten/Umsatzsteuer/Umsatzsteuer-Anwendungserlass/2025-10-15-einfuehrung-obligatorische-e-rechnung.pdf
- GoBD-BMF-Schreiben 28.11.2019 (PDF): https://www.finanzamt.bayern.de/Informationen/Steuerinfos/Weitere_Themen/Aussenpruefung/2019-11-28-GoBD-1.pdf
- GoBD-Änderung 11.03.2024 (PDF): https://www.bundesfinanzministerium.de/Content/DE/Downloads/BMF_Schreiben/Weitere_Steuerthemen/Abgabenordnung/AO-Anwendungserlass/2024-03-11-aenderung-gobd.pdf
- BFH VI R 24/20 (20.04.2023, § 35a/Neubau, PDF): https://www.bundesfinanzhof.de/de/entscheidung/entscheidungen-online/detail/pdf/STRE202310138?type=1646225765

**Reputable Sekundärquellen:**
- BMF-Schreiben 15.10.2024 (NWB): https://datenbank.nwb.de/Dokument/1046425/
- GoBD (NWB, BMF 28.11.2019): https://datenbank.nwb.de/Dokument/800308/
- BEG IV / 8-Jahres-Frist (Deloitte): https://www.deloitte-tax-news.de/steuern/verfahrensrecht/buerokratieentlastungsgesetz-iv-bundestag-verabschiedet-gesetz.html
- Aufbewahrungsfristen (Haufe): https://www.haufe.de/finance/buchfuehrung-kontierung/aufbewahrungsfristen-welche-unterlagen-vernichtet-werden-koennen_186_432446.html
- § 14b Kommentar (Haufe Schwarz/Widmann/Radeisen): https://www.haufe.de/id/kommentar/schwarzwidmannradeisen-ustg-14b-aufbewahrung-von-re-35-aufbewahrungspflichten-fuer-nichtunternehmer-14b-abs1-s5-ustg-HI16891565.html
- Hinweis Aufbewahrungspflicht: https://www.rechnungswesen-info.de/rechnungen_hinweis_aufbewahrungspflicht.html
- ZUGFeRD/Factur-X technisch (PDFlib): https://www.pdflib.com/pdf-knowledge-base/zugferd-and-factur-x/
- ZUGFeRD-Profile (zugferd.org): https://zugferd.org/e-invoicing/1.0.0/faq.en.html
- ZUGFeRD (Wikipedia): https://de.wikipedia.org/wiki/ZUGFeRD
- XRechnung/KoSIT (XEinkauf): https://xeinkauf.de/xrechnung/xrechnung/
- UBL/CII (eu-rechnung.de): https://www.eu-rechnung.de/blog/zugferd-und-xrechnung-ubl-und-cii-als-technische-grundlage-der-e-rechnungsstandards
- § 35a BMF-Anlage (Deloitte): https://www.deloitte-tax-news.de/steuern/files/100223-anlage-zu-bmf-schreiben-35a-estg.pdf
- DSGVO/Anthropic (Cortina Consult): https://cortina-consult.com/ki-compliance/wissen/claude-datenschutz/
- DSGVO/Anthropic (Compound Law): https://compound.law/de-DE/compliance/anthropic-dsgvo-compliance/
