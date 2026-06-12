# Design: §14b-Aufbewahrungs-Warnung (proaktiver Frist-Hinweis)

**Datum:** 2026-06-12 · **Backlog-Task:** „Aufbewahrung-Warnung — proaktiver Hinweis wenn §14b-Frist naht"

## Ziel
Der Nutzer soll proaktiv sehen, wenn die 2-Jahres-Aufbewahrungsfrist (§14b UStG)
eines Belegs **bald endet** oder **abgelaufen** ist — ohne jeden Beleg einzeln zu
öffnen. Inhaltlich wichtig: Fristende heißt NICHT „wegwerfen" — die §634a-Gewähr-
leistung (5 J. ab Abnahme) spricht oft fürs Weiter-Aufbewahren. Der Hinweis bleibt
informativ, keine Lösch-Empfehlung und erst recht keine Auto-Löschung.

## Entscheidung
1. **Pure-Function im Backend** (`app/utils.py`):
   `retention_status(until, today=None) -> None | "active" | "expiring_soon" | "expired"`
   - Schwelle `RETENTION_WARN_DAYS = 90` (ein Quartal Vorlauf; nicht konfigurierbar — R12).
   - Grenzen: `today > until` → expired; `0 <= (until-today).days <= 90` → expiring_soon.
2. **Serializer**: `_invoice_to_dict` liefert zusätzlich `retention_status`
   (Frontend rechnet keine Datums-Logik selbst nach — eine Quelle der Wahrheit).
3. **Stats-Endpoint**: neuer Block `retention` mit
   `{warn_days, expiring_soon, expired, next_expiry}` — in Python über alle Belege
   gerechnet (gleiches Muster wie §35a; Heim-App-Skala, kein SQL-Date-Gefrickel
   über zwei Fallback-Spalten invoice_date/created_at).
4. **Frontend**:
   - Statistik-Tab: zusätzliche Stat-Karte „Aufbewahrung" NUR wenn
     `expiring_soon + expired > 0` (kein Dauer-Rauschen im Normalfall).
   - Beleg-Detail: farbiger Zusatz an der bestehenden „Aufbewahren bis"-Zeile
     („Frist endet bald" / „Frist abgelaufen — §634a beachten").
   - Karten-Badge in der Liste: NICHT (verworfene Alternative, s.u.).

## Verworfene Alternativen
- **Frontend-only** (Datum liegt ja schon im JSON): nicht testbar in diesem Repo
  (keine Browser-Tests) → verletzt R31. Backend-Status ist mit pytest abdeckbar.
- **Karten-Badge pro Beleg in der Liste**: Badge-Slot ist schon belegt
  (Betrag?/Prüfen/Eingereicht — Prioritätskette) und Frist-Info ist auf Karten-
  Ebene Rauschen; die Stats-Karte + Detail-Zeile reichen als proaktiver Kanal.
- **Konfigurierbare Warn-Schwelle**: kein echter Bedarf, R12 (kein Gold-Plating).
- **Push/Mail-Benachrichtigung**: wäre nach außen + neue Infra; weit über Nutzen.

## Codex-Refute (2026-06-12) — Ergebnis
Übernommen: (1) Status + Anzeige-Datum in `_invoice_to_dict` aus EINER
`retention_until_date`-Berechnung (vorher doppelt → Drift-Risiko), (2) Frontend
koerziert Zähler mit `Number()` (defensiv gg. API-Shape-Drift), (3) Wording
„Frist abgelaufen" → „**Mindestfrist erreicht/über Mindestfrist**" — §14b ist
eine MINDESTfrist, „abgelaufen" klang nach Entsorgungs-Empfehlung.
Bewusst NICHT übernommen:
- **Timezone (`date.today()` = Server-Lokalzeit):** Heim-LXC läuft in
  Europe/Berlin; Worst Case wäre ein ±1-Tag-Versatz um Mitternacht am
  Jahreswechsel bei einem rein informativen Hinweis. Zentrale TZ-Behandlung
  wäre eine eigene Baustelle (created_at ist heute schon naive UTC) — nicht
  hier anfangen.
- **created_at-Fallback „legally misleading":** Bestandsverhalten seit v1.1.0
  (retention_until nutzt denselben Fallback); der Diff ändert daran nichts.
- **„Critical: fehlender date-Import in stats.py":** Falsch-Positiv —
  `from datetime import date` ist Zeile 1, Suite importiert + läuft grün.

## Tests
- `retention_status`: None-Basis, aktiv, exakt 90 Tage, 1 Tag vor Ablauf,
  Ablauftag selbst (noch expiring_soon, nicht expired), Tag danach (expired).
- Serializer-Feld `retention_status` vorhanden + konsistent zu `retention_until`.
- `/api/stats`: `retention`-Block zählt korrekt (frischer Beleg = active,
  alter Beleg = expired, next_expiry = frühestes Fristende).
