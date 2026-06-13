# CODEX Summary — 2026-06-13

## Gebaut

- Kein neuer Produktcode in diesem Lauf: README, `.planning` und der lokale Ledger wurden geprüft. Der oberste klar definierte, nicht blockierte Backlog-Punkt aus dem Handover (`§14b-Aufbewahrung-Warnung`) ist bereits auf `main` als v1.6.0 umgesetzt.
- Verifikation ausgeführt: `.venv/bin/pytest -q` → 120 passed, 9 bekannte Deprecation-Warnings aus SQLAlchemy/`datetime.utcnow()`.

## Bewusst ausgelassen

- `§35a Arbeit/Material-Auto-Split aus Line-Items`: laut Ledger geparkt, weil reale E-Rechnungs-Samples fehlen; ohne Samples wäre die Klassifikation nur geraten.
- `OCR-Qualität`: blockiert, weil reale Fehlrechnungs-Samples bzw. ein synthetischer Bild-Test-Harness fehlen.
- Offene UI-Frage zu Einträgen: blockiert durch ausstehende Entscheidung von Christin/Hub.
- Kein Deploy, wie beauftragt.
