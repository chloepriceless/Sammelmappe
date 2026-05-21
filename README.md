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
- **🧾 Liste mit Live-Summe** — markiere beliebige offene Belege, die Summe wird oben live mitgeführt.
- **📤 Ein-Klick Export** — alle ausgewählten Belege als ZIP, inkl. `uebersicht.csv` (Position, Datei, Steller, Datum, Betrag) und `README.txt` mit Gesamtsumme. Genau das, was du bei MyBaufi hochlädst.
- **✅ Status-Tracking** — exportierte Belege werden automatisch als „Eingereicht" markiert. Alles andere bleibt „Offen". Filter-Chips oben.
- **↩️ Einreichungs-Historie** — alle vergangenen Exporte mit ZIP-Re-Download und „Zurücksetzen" (falls die Bank mal eine Tranche ablehnt).
- **🔁 Duplikat-Erkennung** — gleiche Datei zweimal hochgeladen → 409 + Hinweis.
- **✏️ Manuelle Korrektur** — jedes Feld lässt sich in der Edit-Maske überschreiben (Vendor, Betrag, Datum, Nummer, Kategorie, Notiz).
- **🏷️ Kategorien** — Material, Handwerker, Sanitär, Elektro, Heizung, Fenster/Türen, Boden, Dach, Garten, Planung, Gebühren, Sonstiges.
- **📊 Übersicht** — Summe Offen vs. Eingereicht, pro Kategorie, Anzahl Einreichungen.
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
| `POST`  | `/api/export`                         | `{invoice_ids, label, mark_submitted}` → ZIP   |
| `GET`   | `/api/export/{id}/download`           | Re-Download                                    |
| `GET`   | `/api/submissions`                    | Liste vergangener Einreichungen                |
| `POST`  | `/api/submissions/{id}/revert`        | Alle Invoices der Einreichung wieder „Offen"   |
| `GET`   | `/api/stats`                          | Übersicht für Dashboard                        |

Swagger-UI: `http://<host>:8080/docs`

---

## Lizenz

MIT. Mach was draus.
