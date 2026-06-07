# Sammelmappe

> **Bau-Belege bündeln und gemeinsam einreichen.**
> Mobile aufnehmen, automatisch Beträge erkennen, später am PC oder Handy alles markieren und als ZIP bei deiner Baufinanzierung (z. B. **Sparda MyBaufi**) einreichen.

Selfhosted, läuft als unprivileged LXC auf Proxmox (Ein-Befehl-Setup) oder per Docker. Optimiert für **iPhone**: Foto knipsen → Betrag automatisch erkannt → später am PC oder Handy alles auswählen, ZIP exportieren, fertig.

**Installation in einem Befehl** (community-scripts Style):

```bash
# Auf der Proxmox-VE-Node (als root):
bash -c "$(curl -fsSL https://raw.githubusercontent.com/chloepriceless/Sammelmappe/main/proxmox.sh)"
```

→ legt LXC an, installiert alles, gibt dir am Ende die URL.

---

## Was kann es?

- **📷 Mobile-First Capture** — auf dem iPhone öffnet sich direkt die Kamera (oder Mediathek). Vorinstalliert als PWA „Zum Home-Bildschirm hinzufügen" verhält sich die App wie eine native App.
- **🧠 Hybride OCR** — lokales Tesseract (deutsch+englisch) erkennt Rechnungssteller, Brutto-Gesamtbetrag, Datum und Rechnungsnummer. Bei niedriger Konfidenz fällt das System automatisch auf **Claude Vision** zurück (optional, braucht API-Key).
- **📐 E-Rechnung (ZUGFeRD / Factur-X / XRechnung)** — trägt ein hochgeladenes PDF eine eingebettete E-Rechnung (oder lädst du eine `.xml` direkt hoch), werden die Werte **direkt aus dem strukturierten XML** gelesen statt geraten: 100 % exakt, sofort, ohne OCR und ohne API-Kosten. Erkennt CII (UN/CEFACT) und UBL (OASIS).
- **🧾 Liste mit Live-Summe** — markiere beliebige offene Belege, die Summe wird oben live mitgeführt.
- **📤 Ein-Klick Export** — alle ausgewählten Belege als ZIP, inkl. `uebersicht.csv` (Position, Datei, Steller, Datum, Kategorie, Betrag, **Aufbewahren bis**) und `README.txt`. Beide enthalten eine **Summe je Kategorie** (Kostenaufstellung nach Gewerk) und den **§ 14b-Aufbewahrungshinweis** für die ganze Mappe. Genau das, was du bei MyBaufi hochlädst.
- **✅ Status-Tracking** — exportierte Belege werden automatisch als „Eingereicht" markiert. Alles andere bleibt „Offen". Filter-Chips oben.
- **↩️ Einreichungs-Historie** — alle vergangenen Exporte mit ZIP-Re-Download und „Zurücksetzen" (falls die Bank mal eine Tranche ablehnt).
- **🔁 Duplikat-Erkennung** — gleiche Datei zweimal hochgeladen → 409 + Hinweis.
- **✏️ Manuelle Korrektur** — jedes Feld lässt sich in der Edit-Maske überschreiben (Vendor, Betrag, Datum, Nummer, Kategorie, Notiz).
- **🏷️ Kategorien** — Material, Handwerker, Sanitär, Elektro, Heizung, Fenster/Türen, Boden, Dach, Garten, Planung, Gebühren, Sonstiges.
- **📊 Übersicht** — Summe Offen vs. Eingereicht, pro Kategorie, Anzahl Einreichungen.
- **🧮 § 35a Handwerkerbonus** — schätzt konservativ die mögliche Steuerermäßigung (20 % der Arbeitskosten, max 1.200 €/Jahr) aus dem erfassten Arbeitskosten-Anteil, der Zahlungsart und deinem Einzugsdatum — inkl. Hinweis, warum ein Beleg nicht zählt. Keine Steuerberatung.
- **🌓 Dark Mode** — folgt System-Einstellung.
- **🔍 Suche** — Vendor, Rechnungsnummer, Original-Dateiname, Notizen.
- **🖱️ Drag & Drop** auf dem Desktop.
- **🛡️ Auth** — Passwort beim ersten Start; signed Session-Cookie (Argon2-Hash).

---

## Installation

### Variante A: ein Befehl auf der Proxmox-Node ⭐ empfohlen

Auf der **Proxmox-VE-Node** (nicht in einem Container), Shell als root:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/chloepriceless/Sammelmappe/main/proxmox.sh)"
```

Das war's. Du musst nichts vorher klonen, nichts installieren — das Script erledigt alles automatisch:

1. Fragt **Default** oder **Advanced** (Default reicht in 95 % der Fälle).
2. Lädt das Debian-12-LXC-Template, falls noch nicht da.
3. Sucht die nächste freie CT-ID, legt einen unprivileged LXC an (2 Kerne, 1 GiB RAM, 4 GiB Disk, DHCP), startet ihn.
4. Kopiert den Code in den Container.
5. Installiert Tesseract (DEU+ENG), Poppler, Python-venv, Dependencies.
6. Richtet `sammelmappe`-User + systemd-Service ein und startet.
7. Fragt optional dein Login-Passwort und den Anthropic-API-Key direkt mit ab.
8. Druckt am Ende `http://<lxc-ip>:8080/` zum Aufrufen.

**Im Advanced-Mode** kannst du CT-ID, Hostname, CPU, RAM, Disk, Storage, Bridge, statische IP, App-Port frei wählen.

### Variante B: in einen bestehenden LXC installieren

Wenn du schon einen LXC (Debian 12 oder Ubuntu 22.04+) hast und das Setup dort manuell aufsetzen willst:

```bash
git clone https://github.com/chloepriceless/Sammelmappe.git
cd Sammelmappe
sudo ./install.sh
```

`install.sh` ist auch das Skript, das `proxmox.sh` intern aufruft.

### Variante C: Docker

```bash
cd Sammelmappe
export SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
export ANTHROPIC_API_KEY=sk-ant-...   # optional
docker compose up -d --build
```

### Variante D: lokal entwickeln

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env  &&  ${EDITOR:-vi} .env
.venv/bin/uvicorn app.main:app --reload --port 8080
```

System-Voraussetzung: `tesseract`, `tesseract-ocr-deu`, `poppler-utils` (für PDF).

---

## Erster Start

1. Browser auf `http://<container-ip>:8080/` öffnen.
2. **Passwort festlegen** (mind. 6 Zeichen) — das ist dein Login. (Wenn du das Passwort schon in `proxmox.sh` mitgegeben hast, überspringst du diesen Schritt.)
3. Auf dem iPhone die Seite in **Safari** öffnen → `Teilen` → **„Zum Home-Bildschirm"**. Damit hast du das Icon wie eine App und der Fullscreen-Modus funktioniert.

---

## Workflow für Sparda MyBaufi (oder jede andere Baufi)

```
unterwegs                    am PC / Handy                  Bank-Portal
─────────                    ─────────────                  ───────────
📷 fotografieren     →       ✅ Belege auswählen      →    📤 ZIP hochladen
   (OCR läuft im                Summe wird live
   Hintergrund)                 angezeigt
                                ⬇ "ZIP erstellen"
                                ⬇ Status → Eingereicht
```

1. **Beleg erhält** (Material, Handwerker, …) → Handy zücken, App öffnen, 📷 antippen, abfotografieren. Der Beleg erscheint in der Liste „Offen" mit erkanntem Betrag.
2. **Falscher Betrag?** Lange auf den Eintrag drücken → Felder anpassen → speichern.
3. **Bei MyBaufi-Auszahlung** → Filter „Offen" → relevante Belege anhaken → unten erscheint die Summe → „Exportieren" → optional Label vergeben („Tranche 3") → ZIP wird heruntergeladen → bei der Bank hochladen.
4. Im Tab **Eingereicht** siehst du immer, was du wann mit welcher Summe abgegeben hast. Bei Bedarf das ZIP nochmal herunterladen oder mit „Zurücksetzen" alle Belege einer Einreichung wieder auf „Offen" stellen.

---

## OCR-Qualität & Claude Vision

**Tesseract** funktioniert verlässlich bei klaren PDF-Rechnungen und sauber abfotografierten Belegen. Schlechte Beleuchtung, schiefe Fotos, gedrängte Tabellen, handschriftliche Korrekturen → da bricht Tesseract gerne ein.

Für diese Fälle gibt es den **Claude-Vision-Fallback**: Wenn die Tesseract-Konfidenz unter `OCR_CONFIDENCE_THRESHOLD` (Default 0,6) liegt oder gar kein Betrag gefunden wurde, schickt das Backend das Bild an `claude-haiku-4-5` mit der Frage nach strukturiertem JSON (Vendor, Brutto, Datum, Nummer). Kosten: ca. 1–2 Cent pro Beleg. Dafür wirklich verlässlich.

`ANTHROPIC_API_KEY` einfach in der `.env` setzen — sonst läuft alles im Tesseract-only-Modus.

API-Key holst du dir hier: https://console.anthropic.com/

---

## E-Rechnung (ZUGFeRD / Factur-X / XRechnung)

Seit 2025 stellen immer mehr Firmen ihre Rechnungen als **E-Rechnung** aus. Die
verbreitete Hybrid-Variante (ZUGFeRD / Factur-X) sieht aus wie ein ganz normales
PDF, trägt die Rechnung aber zusätzlich als maschinenlesbares **XML** in sich.
XRechnung kommt als reine `.xml`-Datei.

Wenn so eine Datei hochgeladen wird, liest Sammelmappe die Werte (Rechnungssteller,
**Brutto-Gesamtbetrag inkl. MwSt**, Datum, Rechnungsnummer) **direkt aus dem XML** —
kein OCR, kein Raten, kein Claude-Aufruf. Das ist exakt, sofort und kostenlos.
Erkannt werden beide EN-16931-Syntaxen: **CII** (UN/CEFACT, ZUGFeRD/Factur-X) und
**UBL** (OASIS, XRechnung). Solche Belege bekommen das Badge **„E-Rechnung"**.

Zusätzlich liest die App die **einzelnen Rechnungspositionen** aus dem XML und zeigt sie
read-only im Beleg-Detail (Beschreibung, Menge + Einheit, Netto-Betrag, MwSt-%). Die
Positionsbeträge sind **netto** und summieren sich nicht zum Brutto-Gesamtbetrag des Belegs.

Ist kein E-Rechnung-XML vorhanden, läuft alles wie gehabt über OCR / Claude.

> **Einordnung:** Die gesetzliche E-Rechnungs-*Pflicht* gilt nur zwischen Unternehmen
> (B2B) — als privater Bauherr bekommst du sie nicht zwingend. Das Feature ist also
> ein **Genauigkeits-Bonus** für die Fälle, in denen ein Lieferant/Bauträger bereits
> ZUGFeRD verschickt, kein Muss. *(Stand 06/2026, keine Steuerberatung.)*

Technisch: das eingebettete XML wird mit `defusedxml` geparst (abgesichert gegen
XXE / Entity-Expansion), denn Belege können von Dritten stammen.

---

## Aufbewahrungsfrist (§ 14b UStG)

Wer als **Privatperson** eine steuerpflichtige Leistung **im Zusammenhang mit einem
Grundstück** bezieht (Bau, Sanierung, Handwerker am Haus), muss die Rechnung **2 Jahre
aufbewahren** (§ 14b Abs. 1 S. 5 UStG) — bei Verstoß droht ein Bußgeld. Aufzubewahren
sind nicht nur die Rechnung, sondern auch **Zahlungsbeleg, Bauvertrag und
Abnahmeprotokoll**.

Sammelmappe zeigt dir darum im Beleg-Detail ein **„Aufbewahren bis"-Datum**
(Rechnungsjahr + 2 Jahre, gerechnet ab Jahresende). *Stand 06/2026, keine
Steuerberatung — im Zweifel mit dem Steuerberater klären.*

> **Gewährleistung (§ 634a BGB):** Unabhängig von der steuerlichen Frist gilt für
> Mängelansprüche an einem **Bauwerk** i.d.R. eine **5-jährige Verjährung ab Abnahme**.
> Wer Bauleistungen erhält, sollte Rechnung, Zahlungsbeleg und Abnahmeprotokoll daher
> mindestens so lange behalten, um Mängel reklamieren zu können. *Keine Rechtsberatung.*

> **Tipp:** Da die App pro Beleg auch den Zahlungsbeleg-Hinweis trägt: lade neben
> der Rechnung am besten gleich den Überweisungsbeleg mit hoch (eigener Eintrag oder
> als Notiz).

---

## § 35a EStG (Handwerkerbonus)

Für **Handwerkerleistungen am selbstgenutzten, bereits bezogenen Haushalt** gibt es eine
Steuerermäßigung von **20 % der Arbeitskosten, höchstens 1.200 € pro Jahr** (§ 35a Abs. 3
EStG). Sammelmappe hilft beim Überblick:

- Pro Beleg trägst du den **Arbeitskosten-Anteil** (nur Lohn/Maschine/Fahrt, **kein
  Material**), die **Zahlungsart** und das **Zahlungsdatum** ein.
- In den Einstellungen setzt du dein **Einzugsdatum**.
- Die **Übersicht** schätzt daraus konservativ die mögliche Steuerermäßigung pro Jahr —
  und zeigt transparent, **warum** ein Beleg nicht zählt.

Berücksichtigt sind die drei klassischen Stolpersteine:

- **Nur unbar:** Barzahlung wird nicht anerkannt; „Zahlungsart unbekannt" zählt ebenfalls nicht.
- **Nicht in der Neubauphase:** Maßnahmen bis zur Fertigstellung/zum Bezug eines neu errichteten
  Haushalts sind **nicht** begünstigt (BMF-Schreiben v. 09.11.2016) — erst danach (Restarbeiten,
  Garten, Carport).
- **Maßgeblich ist das Zahlungsjahr** (§ 11 EStG), nicht das Rechnungsdatum.

> **Wichtig:** § 35a mindert die **Steuer**, nicht das Einkommen; bei zu geringer
> Einkommensteuer verpufft ein Teil. Für die typische **Bauphase vor dem Einzug** greift
> § 35a meist nicht — es ist ein Bonus nach dem Bezug. *Stand 06/2026, **keine
> Steuerberatung** — im Zweifel mit dem Steuerberater klären.*

---

## Datenschutz (DSGVO)

Sammelmappe ist selbstgehostet — deine Belege liegen auf **deinem** Server, nicht in
einer fremden Cloud.

- **Tesseract** (Standard-OCR) und das **E-Rechnung-XML-Parsing** laufen **vollständig
  lokal**. Ohne Anthropic-Key verlässt kein Beleg dein Gerät.
- **Claude Vision** ist optional. Sobald aktiv, wird das Belegbild zur Erkennung an
  **Anthropic in die USA** übertragen (Drittland-Transfer, Art. 44 ff. DSGVO; Anthropic
  ist unter dem EU-US Data Privacy Framework gelistet). Die App weist im
  Einstellungs-Dialog darauf hin.
- **Rein privater Eigengebrauch** fällt i.d.R. unter die Haushaltsausnahme
  (Art. 2 Abs. 2 lit. c DSGVO). **Als Unternehmer/Selbstständiger** solltest du einen
  Anthropic-API-Key über ein **kommerzielles Konto mit Auftragsverarbeitungsvertrag
  (AVV)** nutzen und ggf. sensible Felder vor dem Upload schwärzen.

*Stand 06/2026, keine Rechtsberatung.*

---

## Konfiguration (`.env`)

| Variable                   | Default                          | Bedeutung                                                                |
| -------------------------- | -------------------------------- | ------------------------------------------------------------------------ |
| `HOST`                     | `0.0.0.0`                        | Bind-Address                                                             |
| `PORT`                     | `8080`                           |                                                                          |
| `DATA_DIR`                 | `./data`                         | Hier liegen `app.db`, `invoices/`, `thumbnails/`, `exports/`             |
| `SECRET_KEY`               | _zufällig vom Installer_         | Signiert Session-Cookies. Niemals committen.                             |
| `SESSION_HOURS`            | `720`                            | Cookie-Lebensdauer (30 Tage)                                             |
| `TESSERACT_LANG`           | `deu+eng`                        | Tesseract-Sprachen                                                       |
| `ANTHROPIC_API_KEY`        | _(leer)_                         | Wenn gesetzt: Vision-Fallback aktiv                                      |
| `CLAUDE_MODEL`             | `claude-haiku-4-5-20251001`      | Welches Modell für die Vision-Anfragen                                   |
| `OCR_CONFIDENCE_THRESHOLD` | `0.6`                            | Unterhalb: Fallback auf Claude (wenn API-Key gesetzt)                    |
| `MAX_UPLOAD_MIB`           | `25`                             | Hard-Cap pro Datei                                                       |

---

## Hinter einem Reverse-Proxy (Caddy / nginx)

Damit das Cookie sicher gesetzt wird, terminiere TLS am Proxy und reiche an den Container weiter:

```caddy
belege.deine-domain.de {
  reverse_proxy 10.0.0.20:8080
}
```

In der `.env` dann nichts ändern; FastAPI ist mit `--proxy-headers` gestartet, akzeptiert also `X-Forwarded-*`.

---

## Backup

Alles Wichtige liegt unter `data/`:

- `app.db` — SQLite mit Beleg-Metadaten und Sessions
- `invoices/` — Original-Dateien (so wie hochgeladen)
- `thumbnails/` — kleine JPEG-Vorschauen (regenerierbar)
- `exports/` — frühere ZIP-Bundles

Tipp: einfach den ganzen `data/`-Ordner regelmäßig sichern. Beispiel mit `rsync`:

```bash
rsync -a /opt/sammelmappe/data/  user@nas:/backups/sammelmappe/$(date +%F)/
```

---

## API (für Bastler)

Cookie-authentifiziert, gleiche Session wie das UI. Alle Endpoints unter `/api/*`.

| Methode | Pfad                                  | Zweck                                          |
| ------- | ------------------------------------- | ---------------------------------------------- |
| `POST`  | `/api/invoices`                       | multipart Upload, OCR läuft, gibt Invoice JSON |
| `GET`   | `/api/invoices?status=open&q=...`     | Liste + Aggregate                              |
| `PATCH` | `/api/invoices/{id}`                  | Felder ändern (JSON-Body)                      |
| `DELETE`| `/api/invoices/{id}`                  | Löschen (samt Datei + Thumbnail)               |
| `GET`   | `/api/invoices/{id}/file`             | Original-Download                              |
| `GET`   | `/api/invoices/{id}/thumbnail`        | JPG-Thumbnail                                  |
| `GET`   | `/api/invoices/{id}/lines`            | E-Rechnung-Positionen (aus dem XML, netto)     |
| `GET`   | `/api/section35a`                     | § 35a-Schätzung (Handwerkerbonus) pro Jahr     |
| `POST`  | `/api/export`                         | `{invoice_ids, label, mark_submitted}` → ZIP   |
| `GET`   | `/api/export/{id}/download`           | Re-Download                                    |
| `GET`   | `/api/submissions`                    | Liste vergangener Einreichungen                |
| `POST`  | `/api/submissions/{id}/revert`        | Alle Invoices der Einreichung wieder „Offen"   |
| `GET`   | `/api/stats`                          | Übersicht für Dashboard                        |

Swagger-UI: `http://<host>:8080/docs`

---

## Lizenz

MIT. Mach was draus.
