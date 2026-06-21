# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden hier dokumentiert.
Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionierung nach [Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

Drei Stufen: die 4 Release-Blocker (Branch `fix/release-blockers`) und die
MEDIUM/LOW-Folge-Härtung (Branch `harden/medium-findings`) aus dem Qualitäts-Review
vom 2026-06-14 (`.planning/RELEASE-REVIEW-2026-06-14-FINDINGS.md`), plus die
nachgelagerte Round-2-Härtung (Branch `harden/round-2`). **200 → 286 Tests grün.**

### Behoben — Release-Blocker (`fix/release-blockers`)

#### Security
- **Fail-Fast-Guard gegen unsicheren `SECRET_KEY`.** Die App startet nicht mehr mit
  einem Platzhalter-Secret (`change-me` u.ä.) **oder einem zu kurzen, schwachen
  Schlüssel** (Mindestlänge 32 Zeichen — `secrets.token_urlsafe(32)` liefert 43).
  Sonst wären die Session-Cookies fälschbar (vorhersagbarer Payload, signiert per
  HMAC; der Cookie-Validierungspfad ist nicht rate-limitiert → ein schwacher
  Schlüssel ist per Wörterbuch angreifbar). `docker-compose` verlangt `SECRET_KEY`
  zwingend; `.env.example` ist leer mit Generier-Hinweis. **Beim Deploy einen echten
  `SECRET_KEY` setzen, sonst bootet die App nicht.**
- **Brute-Force-Rate-Limit auf `/api/auth/login`.** In-Memory-Sliding-Window pro
  Client-IP (15-Min-Fenster, Sperre ab 10 Fehlversuchen → HTTP 429 + `Retry-After`).
  Neue Option `TRUSTED_PROXIES`: hinter einem Reverse-Proxy dort die Proxy-IP
  eintragen, damit das Limit auf die echte Client-IP greift (`X-Forwarded-For` wird
  nur von vertrauenswürdigen Proxys akzeptiert — nicht fälschbar).

#### Performance
- **Upload blockiert den Event-Loop nicht mehr.** Datei-Write, Hashing und die
  OCR-/Thumbnail-Pipeline (Tesseract/Claude/poppler) laufen jetzt im Threadpool
  statt synchron im `async`-Endpoint.

### Härtung — MEDIUM/LOW-Folge-Findings (`harden/medium-findings`)

#### Security
- **Session-Cookie standardmäßig `Secure`.** Das `Secure`-Flag war hart auf `False`
  — hinter dem dokumentierten Reverse-Proxy konnte das Cookie über Klartext-HTTP
  abgegriffen werden. Jetzt `COOKIE_SECURE` (Default `true`, secure-by-default);
  `false` nur für reinen HTTP-LAN-Betrieb.
- **Upload-Magic-Byte-Validierung.** Der Upload prüfte nur den Client-`Content-Type`
  — als `image/*` getarnte HTML/SVG/XML passierte. Neues `app/filetype.py` (stdlib)
  snifft den echten Typ; als Bild getarntes Markup und unerkannte Inhalte werden mit
  `415` abgelehnt, der gespeicherte MIME ist der erkannte kanonische Typ.
- **Datei-Serve gehärtet.** `X-Content-Type-Options: nosniff` auf allen File-Serves;
  XML/Markup wird nie inline ausgeliefert (forciert `attachment`); Dateiname über
  RFC-5987 statt manuellem Header-Bau (kein CR/LF-/Quote-Injection).
- **Mindest-Passwortlänge auf 10 angehoben** (`set_password` + Setup-UI; analog zum
  `SECRET_KEY`-Guard). Login bleibt bei 6, damit Bestands-Passwörter sich weiter
  anmelden können — die Grenze gilt nur für neu gesetzte Passwörter.

#### Behoben
- **§ 35a: Arbeitskosten-Konsistenz bei `amount`-Änderung.** Eine PATCH-Änderung nur
  des Betrags (kleiner) ließ einen gespeicherten höheren `labor_amount` stehen →
  still falsche Handwerkerbonus-Schätzung. Jetzt explizite Revalidierung (HTTP 400).
  `amount` zusätzlich gegen NaN/Inf/negativ gehärtet.
- **Upload-Disk-Write abgesichert.** Volle/nicht-beschreibbare Platte gibt jetzt
  `507` statt `500` und hinterlässt keine verwaiste Teildatei (auch der DB-Commit-Pfad
  räumt bei Fehler auf).
- **Thumbnail liefert `404` statt `500`,** wenn das Rendern fehlschlägt und keine Datei
  schreibt.
- **Upload-`Content-Type` toleranter.** Ein gültiges `application/xml; charset=utf-8`
  oder `IMAGE/PNG` wurde vom exakten Vergleich fälschlich abgelehnt; jetzt wird der
  MIME-Teil normalisiert (der Magic-Byte-Sniff bleibt die eigentliche Validierung).

### Dokumentation
- CHANGELOG-Linkrefs für `v1.6.1`/`v1.6.0` ergänzt; README um `COOKIE_SECURE`,
  `TESSERACT_CMD` und den `X-Forwarded-For`-Deploy-Caveat erweitert.

### Tests
- **+63 Tests** gesamt (167 → 232): SECRET_KEY-Guard inkl. Low-Entropy-Reject,
  Login-Rate-Limit inkl. End-to-End-429, Upload-Pfad (415/413/400/409/507/Happy),
  Magic-Byte-Sniffing, Serve-Header (nosniff/disposition), § 35a-PATCH-Konsistenz,
  Cookie-Secure. `requirements-dev.txt` + README-Abschnitt „Tests". Flaky
  Tamper-Token-Test aus v1.6.1 deterministisch gemacht.

### Härtung — Round 2 (`harden/round-2`)

#### Security / Datenintegrität
- **`sha256` ist jetzt DB-seitig `UNIQUE` — schließt das Duplikat-Race.** Die
  Dubletten-Erkennung beim Upload war rein anwendungsseitig (prüfen → einfügen); zwei
  gleichzeitige Uploads derselben Datei konnten beide die Prüfung passieren und beide
  gespeichert werden. Der UNIQUE-Constraint ist der Backstop: ein Commit-Konflikt
  liefert jetzt dieselbe `409`-Duplikat-Antwort wie die Vorab-Prüfung (statt eines
  `500`). SQLite-`busy_timeout` wird gepinnt (`timeout=30`), damit der Verlierer des
  Schreib-Locks deterministisch im Constraint landet statt in „database is locked".
- **Fail-safe, nicht-destruktive Migration für Bestands-DBs.** `create_all` lässt einen
  vorhandenen, nur namensgleichen Index unangetastet, also wandelt eine eigene Migration
  den alten Nicht-Unique-Index in einen Unique-Index gleichen Namens um — atomar über
  eine explizite `BEGIN`-Transaktion auf der rohen DBAPI-Verbindung (ein fehlschlagendes
  `CREATE` darf den Index nicht verlieren). Liegen Alt-Duplikate vor, wird der Constraint
  **nicht** angelegt und **keine Zeile gelöscht**; stattdessen warnt der Start laut und
  `/healthz` meldet den Zustand, bis der Betreiber manuell bereinigt.

#### Security — HTTP-Header / CSP
- **Content-Security-Policy + Security-Header auf jeder Response** (globale Middleware):
  strenge `script-src 'self'` (kein `'unsafe-inline'`), dazu `default-src/connect-src/
  img-src/worker-src 'self'`, `object-src/frame-src/frame-ancestors 'none'`,
  `base-uri/form-action 'self'`, plus `X-Frame-Options: DENY`, `Referrer-Policy:
  no-referrer`, `X-Content-Type-Options: nosniff`. Defense-in-Depth für die bewusst
  inline gerenderten Belege (M3) und Backstop gegen Stored-XSS aus extrahierten
  Feldern (die App escaped sie bereits an der Quelle). Kein HSTS — die App darf im LAN
  über HTTP laufen (`COOKIE_SECURE` konfigurierbar); TLS/HSTS ist Reverse-Proxy-Sache.
- **Inline-`<script>` aus den HTML-Seiten ausgelagert** (`static/sw-register.js`,
  `login.js`, `setup.js`) — Voraussetzung für die strenge `script-src` ohne
  `'unsafe-inline'`. Verhalten unverändert; Forms behalten ihren No-JS-`POST`-Fallback.

#### Build/CI
- **GitHub-Actions-Pipeline** (`.github/workflows/ci.yml`): pytest auf Python 3.12 mit
  den OCR-/PDF-/QR-Systempaketen aus dem Dockerfile; least-privilege, concurrency-cancel.

#### Korrektheit — OCR/Datum
- **TSE-Belegdatum wird lokal (`Europe/Berlin`) bestimmt statt aus UTC.** Der
  Kassenbeleg-Zeitstempel ist UTC; `.date()` darauf lag bei Belegen kurz nach
  lokaler Mitternacht einen Tag zu früh — am Jahreswechsel landete der Beleg im
  falschen **Steuerjahr** (§35a = Zahlungsjahr). Jetzt wird vor der Tagesableitung
  nach Berlin konvertiert (DST-korrekt; naive Zeitstempel gelten als UTC). `tzdata`
  ist Runtime-Dependency, damit die Zone auf jedem Deploy verfügbar ist.
- **Claude-Primary ohne Betrag fällt auf Tesseract zurück.** Lieferte Claude (als
  Primär-Engine) ein Ergebnis ohne Gesamtbetrag, wurde Tesseract nie befragt —
  asymmetrisch zum Legacy-Pfad. Jetzt holt Tesseract den fehlenden Betrag nach; die
  ausgewiesene Confidence ist das Minimum beider Engines (kein Über-Vertrauen in
  einen Tesseract-Betrag unter Claude-Confidence).
- **Datumserkennung versteht ISO 8601 (`YYYY-MM-DD`)** im gelabelten und im
  Fallback-Pfad (vorher nur `TT.MM.JJJJ`/`-`/`/`).
- **Fallback-Datumsfenster auf 3 Jahre erweitert** (war 2): Bauprojekte spannen
  domänentypisch mehrere Jahre, spät gebündelte Belege sind real ~2,5 Jahre alt.

### Tests
- **+13 Tests** (232 → 245): sha256-Migration (Konvertierung, Idempotenz, Composite-Index,
  Duplikat-Abbruch+Warnung, **Atomaritäts-Regression: alter Index überlebt fehlschlagendes
  `CREATE`**), Upload-Race → `409` ohne Orphan, IntegrityError-ohne-Treffer → `500`,
  `/healthz`-Warnsignal.
- **+35 Tests** (245 → 280): **M7** — Engine-Auswahl von `ocr.extract()` (Claude-Primary,
  Legacy-Quality-Fallback, TSE-QR-Override, E-Invoice-Shortcut, Defensiv-Pfade) mit
  gemockten Engines; OCR/Datum-Korrektheit (UTC→Berlin-Belegdatum inkl. Jahreswechsel/DST,
  Claude-leer→Tesseract-Rescue, ISO-Datum, 3-Jahres-Fenster).
- **+6 Tests** (280 → 286): CSP/Security-Header auf Normal-/Static-/Fehler-Responses,
  strikte `script-src`-Regression (kein `'unsafe-inline'`), kein HSTS.

## [1.6.1] — 2026-06-13

### Fixed
- **Laufzeit-Einstellungen: falscher Vorgabewert blieb bis zu 60 s im Cache.**
  Der TTL-Cache in `runtime_config` speicherte den bereits mit dem Vorgabewert
  aufgelösten Wert nur unter dem Schlüssel. Da derselbe Schlüssel an
  verschiedenen Stellen mit unterschiedlichen Vorgaben gelesen wird — speziell
  `anthropic_api_key`: `None` auf der Einstellungs-Seite vs. der `.env`-Schlüssel
  im OCR-Pfad — konnte bei nur per `.env` gesetztem Key (ohne UI-Override) für bis
  zu 60 s gewinnen, wer zuerst cachte: die OCR-Erkennung sah dann „kein API-Key"
  (Claude-OCR fiel kurz aus) bzw. die UI wies den `.env`-Key fälschlich als Quelle
  „db" aus. Fix: es wird nur der rohe DB-Wert gecacht, der Vorgabewert erst beim
  Lesen angewandt → Cache ist vorgabe-unabhängig.
- **`datetime.utcnow()` durchgängig ersetzt** durch `datetime.now(timezone.utc)`
  (Python-3.12-Deprecation). Verhalten unverändert (naive UTC bleibt naive UTC),
  keine Schema-Migration. Testsuite jetzt warnungsfrei.

### Tests
- **+48 Tests.** Erstmals Coverage für das sicherheitskritische Auth-Modul
  (Argon2-Passwort-Hash, Session-Cookie-Signatur inkl. Tamper-/Falsch-Key-/
  Falsch-Salt-Abweisung, `require_auth`: `/api/*` → 401, HTML → Redirect `/login`)
  sowie für `runtime_config` (TTL-Cache, Leer→Vorgabe, Löschen, Invalidate inkl.
  Regressionstest für den obigen Fix) und die `/api/settings`-Logik
  (Key-Maskierung ohne Leak, db/env/none-Priorität, Schwellwert-Koersion,
  Eingabe-Validierung). 167 Tests grün, 0 Warnungen.

## [1.6.0] — 2026-06-12

### Added
- **§ 14b-Aufbewahrungs-Warnung.** Die App weist jetzt proaktiv darauf hin, wenn
  die 2-Jahres-Mindestaufbewahrungsfrist eines Belegs **in ≤ 90 Tagen endet** oder
  **bereits erreicht** ist: farbiger Hinweis an der „Aufbewahren bis"-Zeile im
  Beleg-Detail + eine „Aufbewahrung (§14b)"-Karte im Statistik-Tab (erscheint nur,
  wenn es etwas zu melden gibt). Bewusst **keine Entsorgungs-Empfehlung** — der
  Hinweis erinnert zugleich an die 5-jährige Gewährleistung (§ 634a BGB). Neue
  API-Felder: `retention_status` pro Beleg, `retention`-Block in `GET /api/stats`.

## [1.5.0] — 2026-06-08

### Added
- **„Alle offenen auswählen"-Button** über der Beleg-Liste — wählt alle offenen Belege
  (mit erkanntem Betrag) auf einen Schlag für den Export aus; ein zweiter Klick hebt die
  Auswahl wieder auf (Select-all / Deselect-all). Ist deaktiviert, wenn nichts auswählbar ist.
- **„Öffnen"-Button pro Beleg** — öffnet das Detail/Bearbeiten mit einem **einzelnen Klick**,
  statt Doppelklick (Desktop) bzw. langem Tippen (Handy). Ein Klick auf die Karte wählt sie
  weiterhin für den Export aus; Doppelklick/Long-Press funktionieren wie gehabt weiter.

## [1.4.0] — 2026-06-07

### Added
- **§ 35a EStG Handwerkerbonus-Helfer.** Pro Beleg lassen sich jetzt der
  **Arbeitskosten-Anteil**, die **Zahlungsart** und das **Zahlungsdatum** erfassen
  (aufklappbarer Bereich im Beleg-Detail); das **Einzugsdatum** wird in den
  Einstellungen gesetzt. Die Übersicht (Statistik-Tab) schätzt daraus konservativ die
  mögliche Steuerermäßigung (**20 % der Arbeitskosten, max 1.200 €/Jahr**) und zeigt
  transparent, **warum** ein Beleg NICHT zählt (Barzahlung, Neubauphase vor Einzug,
  fehlender Arbeitskosten-Anteil, …). Neuer Endpoint `GET /api/section35a`.
- Konservative, verifizierte Regeln (keine Steuerberatung): nur **Arbeit** (kein
  Material), nur **unbar** (Barzahlung/unbekannt zählen nicht), nur am **bezogenen
  Haushalt** (Neubau bis Fertigstellung ist nicht begünstigt — BMF-Schreiben
  v. 09.11.2016), Jahres-Cap nach dem **Zahlungsjahr** (§ 11 Abflussprinzip).

### Changed
- Leichte, **idempotente, race-sichere DB-Migration** beim Start ergänzt die neuen
  Beleg-Spalten (`labor_amount`, `payment_method`, `payment_date`) auf bestehenden
  Datenbanken — `create_all` legt nur fehlende Tabellen an, ändert vorhandene nicht.

### Security
- `labor_amount` wird auf **endliche, nicht-negative** Werte **≤ Rechnungssumme**
  validiert (NaN/Inf werden abgewiesen) — der § 35a-Pfad zählt nie einen unsicheren
  oder unplausiblen Wert. Design **und** Implementierung Codex-refute-geprüft.

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

[1.6.1]: https://github.com/chloepriceless/Sammelmappe/releases/tag/v1.6.1
[1.6.0]: https://github.com/chloepriceless/Sammelmappe/releases/tag/v1.6.0
[1.5.0]: https://github.com/chloepriceless/Sammelmappe/releases/tag/v1.5.0
[1.4.0]: https://github.com/chloepriceless/Sammelmappe/releases/tag/v1.4.0
[1.3.0]: https://github.com/chloepriceless/Sammelmappe/releases/tag/v1.3.0
[1.2.0]: https://github.com/chloepriceless/Sammelmappe/releases/tag/v1.2.0
[1.1.1]: https://github.com/chloepriceless/Sammelmappe/releases/tag/v1.1.1
[1.1.0]: https://github.com/chloepriceless/Sammelmappe/releases/tag/v1.1.0
[1.0.0]: https://github.com/chloepriceless/Sammelmappe/releases/tag/v1.0.0
