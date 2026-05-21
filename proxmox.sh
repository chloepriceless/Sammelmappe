#!/usr/bin/env bash
# Sammelmappe — Proxmox VE LXC builder & installer
#
# Run this script ON the Proxmox VE node (NOT inside a container):
#
#   # Option A — direct from a clone on the node:
#   git clone https://github.com/<your-fork>/Sammelmappe.git
#   cd Sammelmappe
#   bash proxmox.sh
#
#   # Option B — one-liner (after you publish the script):
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/<your-fork>/Sammelmappe/main/proxmox.sh)"
#
# What it does:
#   1. Verifies it's running on a Proxmox host
#   2. Asks Default (sensible presets) or Advanced (full control)
#   3. Downloads the Debian 12 LXC template if needed
#   4. Creates an unprivileged LXC, starts it
#   5. Pushes the app code into the container (or git-clones it)
#   6. Runs install.sh inside → Tesseract, Python venv, systemd, password setup page
#   7. Prints the final IP + URL

set -euo pipefail

# ---- pretty output ----------------------------------------------------------
if [[ -t 1 ]]; then
  RED=$'\033[1;31m'; GREEN=$'\033[1;32m'; YELLOW=$'\033[1;33m'; BLUE=$'\033[1;34m'
  CYAN=$'\033[1;36m'; BOLD=$'\033[1m'; DIM=$'\033[2m'; NC=$'\033[0m'
else
  RED=; GREEN=; YELLOW=; BLUE=; CYAN=; BOLD=; DIM=; NC=
fi
msg_info()  { printf "  ${BLUE}➜${NC} %s\n" "$*"; }
msg_ok()    { printf "  ${GREEN}✓${NC} %s\n" "$*"; }
msg_warn()  { printf "  ${YELLOW}!${NC} %s\n" "$*"; }
msg_error() { printf "  ${RED}✗${NC} %s\n" "$*" >&2; }
die()       { msg_error "$*"; exit 1; }

header() {
  cat <<'EOF'

   ____                                _                                 _____
  / ___|  __ _ _ __ ___  _ __ ___   __| |_ __ ___   ___  ___ _   _  ___ |__  /___
  \___ \ / _` | '_ ` _ \| '_ ` _ \ / _` | '_ ` _ \ / _ \/ __| | | |/ _ \  / // _ \
   ___) | (_| | | | | | | | | | | | (_| | | | | | |  __/\__ \ |_| | (_) |/ /|  __/
  |____/ \__,_|_| |_| |_|_| |_| |_|\__,_|_| |_| |_|\___||___/\__,_|\___//____\___|

                       Sammelmappe — Proxmox LXC builder & installer

EOF
}

# ---- preflight --------------------------------------------------------------
preflight() {
  [[ $EUID -eq 0 ]] || die "Bitte als root ausführen (z. B. via Proxmox-Shell)."
  command -v pveversion >/dev/null || die "Dieses Script muss auf einer Proxmox-VE-Node laufen (pveversion nicht gefunden)."
  command -v pct >/dev/null || die "'pct' fehlt — keine Proxmox-Installation erkannt."
  msg_ok "Proxmox erkannt: $(pveversion | head -n1)"
}

# ---- default values ---------------------------------------------------------
APP_NAME="Sammelmappe"
HOSTNAME="${HOSTNAME:-sammelmappe}"
CT_DISK="${CT_DISK:-4}"           # GB
CT_RAM="${CT_RAM:-1024}"          # MiB
CT_SWAP="${CT_SWAP:-512}"         # MiB
CT_CORES="${CT_CORES:-2}"
CT_STORAGE="${CT_STORAGE:-}"      # rootfs storage (auto-pick)
CT_TEMPLATE_STORAGE="${CT_TEMPLATE_STORAGE:-local}"
CT_BRIDGE="${CT_BRIDGE:-vmbr0}"
CT_IP="${CT_IP:-dhcp}"
CT_GW="${CT_GW:-}"
CT_UNPRIVILEGED="${CT_UNPRIVILEGED:-1}"
CT_NESTING="${CT_NESTING:-0}"
APP_PORT="${APP_PORT:-8080}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
TEMPLATE_NAME="${TEMPLATE_NAME:-debian-12-standard}"
REPO_URL="${REPO_URL:-https://github.com/chloepriceless/Sammelmappe.git}"
PASSWORD=""

# ---- helpers ----------------------------------------------------------------

# Pick a free CT ID (highest existing + 1, starting at 100)
next_ctid() {
  local ids cur=100
  ids="$(pct list 2>/dev/null | awk 'NR>1 {print $1}' | sort -n || true)"
  for id in $ids; do
    (( id >= cur )) && cur=$((id + 1))
  done
  echo "$cur"
}

# Pick a storage that supports rootfs (rootdir or images)
pick_storage() {
  local s
  while read -r s; do
    echo "$s"
    return
  done < <(pvesm status -content rootdir 2>/dev/null | awk 'NR>1 && $3=="active" {print $1}')
  # fallback
  pvesm status 2>/dev/null | awk 'NR>1 && $3=="active" {print $1; exit}'
}

# Find the latest matching template name
find_template_file() {
  pveam list "$CT_TEMPLATE_STORAGE" 2>/dev/null | awk 'NR>1 {print $1}' | grep -E "/${TEMPLATE_NAME}_.*\.tar\.(zst|gz)$" | sort -V | tail -n1
}

download_template() {
  local existing
  existing="$(find_template_file || true)"
  if [[ -n "$existing" ]]; then
    msg_ok "Template vorhanden: $(basename "$existing")"
    TEMPLATE_FILE="$existing"
    return
  fi
  msg_info "Aktualisiere Template-Liste …"
  pveam update >/dev/null
  local latest
  latest="$(pveam available 2>/dev/null | awk -v t="$TEMPLATE_NAME" '$2 ~ "^"t"_" {print $2}' | sort -V | tail -n1)"
  [[ -n "$latest" ]] || die "Konnte Template '$TEMPLATE_NAME' nicht finden."
  msg_info "Lade Template: $latest"
  pveam download "$CT_TEMPLATE_STORAGE" "$latest"
  TEMPLATE_FILE="$(find_template_file)"
  [[ -n "$TEMPLATE_FILE" ]] || die "Template-Download fehlgeschlagen."
  msg_ok "Template geladen"
}

ask() {
  local prompt="$1" var_default="$2" answer
  read -r -p "  $prompt [${var_default}]: " answer || true
  echo "${answer:-$var_default}"
}

ask_secret() {
  local prompt="$1" answer
  read -r -s -p "  $prompt: " answer || true
  echo ""
  printf '%s' "$answer"
}

ask_settings() {
  local mode
  echo
  printf "  ${BOLD}Installations-Modus:${NC}\n"
  printf "    [${CYAN}1${NC}] Default  — sensible Voreinstellungen, nur die wichtigsten Fragen\n"
  printf "    [${CYAN}2${NC}] Advanced — alle Optionen einstellbar\n"
  read -r -p "  Auswahl [1]: " mode || true
  mode="${mode:-1}"

  CT_ID="$(next_ctid)"
  CT_STORAGE="${CT_STORAGE:-$(pick_storage)}"
  [[ -n "$CT_STORAGE" ]] || die "Konnte keinen aktiven Storage finden."

  if [[ "$mode" == "2" ]]; then
    CT_ID=$(ask "Container-ID"               "$CT_ID")
    HOSTNAME=$(ask "Hostname"                "$HOSTNAME")
    CT_CORES=$(ask "CPU-Kerne"               "$CT_CORES")
    CT_RAM=$(ask "RAM (MiB)"                 "$CT_RAM")
    CT_SWAP=$(ask "Swap (MiB)"               "$CT_SWAP")
    CT_DISK=$(ask "Disk (GiB)"               "$CT_DISK")
    CT_STORAGE=$(ask "Storage für rootfs"    "$CT_STORAGE")
    CT_TEMPLATE_STORAGE=$(ask "Storage für Templates" "$CT_TEMPLATE_STORAGE")
    CT_BRIDGE=$(ask "Netzwerk-Bridge"        "$CT_BRIDGE")
    CT_IP=$(ask "IP (dhcp oder z. B. 10.0.0.42/24)" "$CT_IP")
    if [[ "$CT_IP" != "dhcp" ]]; then
      CT_GW=$(ask "Gateway"                  "${CT_GW:-10.0.0.1}")
    fi
    CT_UNPRIVILEGED=$(ask "Unprivileged (1/0)" "$CT_UNPRIVILEGED")
    APP_PORT=$(ask "Port der Web-UI"         "$APP_PORT")
  else
    HOSTNAME=$(ask "Hostname"   "$HOSTNAME")
    APP_PORT=$(ask "Port"       "$APP_PORT")
  fi

  echo
  printf "  ${BOLD}Claude Vision (OCR-Fallback, optional aber empfohlen):${NC}\n"
  printf "  ${DIM}Bei niedriger OCR-Konfidenz fragt der Container Claude per API.\n"
  printf "  Pro Rechnung ca. 1–2 Cent. Leer lassen = nur Tesseract.${NC}\n"
  ANTHROPIC_API_KEY=$(ask "Anthropic API Key (leer = überspringen)" "${ANTHROPIC_API_KEY}")

  echo
  printf "  ${BOLD}Login-Passwort der App${NC} (das setzt du sonst beim ersten Aufruf im Browser):\n"
  while :; do
    PASSWORD="$(ask_secret 'Passwort (leer = später im Browser setzen)')"
    if [[ -z "$PASSWORD" ]]; then break; fi
    if [[ ${#PASSWORD} -lt 6 ]]; then msg_warn "Mindestens 6 Zeichen."; continue; fi
    local confirm
    confirm="$(ask_secret 'Passwort bestätigen')"
    [[ "$PASSWORD" == "$confirm" ]] && break
    msg_warn "Passwörter stimmen nicht überein, nochmal."
  done
}

confirm_settings() {
  echo
  printf "  ${BOLD}Zusammenfassung${NC}\n"
  printf "    Container-ID:    ${CYAN}%s${NC}\n" "$CT_ID"
  printf "    Hostname:        %s\n" "$HOSTNAME"
  printf "    CPU/RAM/Disk:    %s Kerne / %s MiB / %s GiB\n" "$CT_CORES" "$CT_RAM" "$CT_DISK"
  printf "    Storage:         %s\n" "$CT_STORAGE"
  printf "    Netzwerk:        bridge=%s, ip=%s\n" "$CT_BRIDGE" "$CT_IP"
  printf "    App-Port:        %s\n" "$APP_PORT"
  printf "    Claude Vision:   %s\n" "$([[ -n "$ANTHROPIC_API_KEY" ]] && echo 'aktiv' || echo 'aus')"
  printf "    Login-Passwort:  %s\n" "$([[ -n "$PASSWORD" ]] && echo 'vorab gesetzt' || echo 'später im Browser')"
  echo
  read -r -p "  Mit dieser Konfiguration anlegen? [J/n] " yn || true
  case "${yn:-J}" in [nN]*) die "Abgebrochen."; esac
}

# ---- container creation -----------------------------------------------------

create_container() {
  msg_info "Erstelle LXC #${CT_ID} …"
  local NET_OPT="name=eth0,bridge=${CT_BRIDGE}"
  if [[ "$CT_IP" == "dhcp" ]]; then
    NET_OPT="${NET_OPT},ip=dhcp,ip6=auto"
  else
    NET_OPT="${NET_OPT},ip=${CT_IP}"
    [[ -n "$CT_GW" ]] && NET_OPT="${NET_OPT},gw=${CT_GW}"
  fi

  # Generate a random root password (only used inside the CT, never displayed; user logs in via App)
  local ROOTPW; ROOTPW="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)"

  pct create "$CT_ID" "$TEMPLATE_FILE" \
    --hostname "$HOSTNAME" \
    --cores "$CT_CORES" \
    --memory "$CT_RAM" \
    --swap "$CT_SWAP" \
    --rootfs "${CT_STORAGE}:${CT_DISK}" \
    --net0 "$NET_OPT" \
    --onboot 1 \
    --unprivileged "$CT_UNPRIVILEGED" \
    --features "nesting=${CT_NESTING}" \
    --password "$ROOTPW" \
    --ostype debian \
    --description "Sammelmappe — installed by proxmox.sh" \
    --tags "sammelmappe;invoice;ocr" \
    >/dev/null
  msg_ok "Container angelegt"

  msg_info "Starte Container …"
  pct start "$CT_ID" >/dev/null
  # Wait for the container to be up (network sometimes takes a moment)
  for i in {1..30}; do
    if pct exec "$CT_ID" -- /bin/true >/dev/null 2>&1; then break; fi
    sleep 1
  done
  pct exec "$CT_ID" -- /bin/true >/dev/null 2>&1 || die "Container reagiert nicht."
  msg_ok "Container läuft"
}

wait_for_network() {
  msg_info "Warte auf Netzwerk im Container …"
  for i in {1..60}; do
    if pct exec "$CT_ID" -- bash -lc 'getent hosts deb.debian.org >/dev/null 2>&1 && curl -fsS --max-time 5 https://deb.debian.org/debian/dists/stable/Release -o /dev/null'; then
      msg_ok "Netzwerk OK"
      return
    fi
    sleep 1
  done
  die "Container hat kein funktionierendes Netzwerk — prüfe Bridge / IP / DNS."
}

# ---- push code & install ----------------------------------------------------

push_code() {
  local SRC; SRC="$(cd "$(dirname "$0")" && pwd)"
  if [[ -f "$SRC/app/main.py" ]]; then
    msg_info "Kopiere Code aus $SRC in den Container …"
    pct exec "$CT_ID" -- mkdir -p /opt/sammelmappe
    # Tar over stdin — works regardless of which storage type the rootfs uses
    tar -C "$SRC" --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
                  --exclude='.pytest_cache' --exclude='data' -czf - . \
      | pct exec "$CT_ID" -- tar -C /opt/sammelmappe -xzf -
    msg_ok "Code übertragen"
  else
    msg_info "Lokale Quellen nicht gefunden — klone $REPO_URL im Container …"
    pct exec "$CT_ID" -- bash -lc "apt-get -qq update && apt-get -y -qq install git ca-certificates"
    pct exec "$CT_ID" -- bash -lc "rm -rf /opt/sammelmappe && git clone --depth=1 $REPO_URL /opt/sammelmappe"
    msg_ok "Repo geklont"
  fi
}

run_install_inside() {
  msg_info "Führe install.sh im Container aus (System-Pakete, venv, systemd) …"
  # Pass settings into the inner installer via env vars
  pct exec "$CT_ID" --                                                  \
    env APP_PORT="$APP_PORT" ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"     \
        DEBIAN_FRONTEND=noninteractive                                  \
    bash /opt/sammelmappe/install.sh
  msg_ok "install.sh fertig"
}

preset_password() {
  [[ -n "$PASSWORD" ]] || { msg_info "Passwort wird beim ersten Browser-Aufruf gesetzt."; return; }
  msg_info "Setze Login-Passwort vorab im Container …"
  # Use the app's own auth.set_password through python so the hashing/storage is consistent.
  pct exec "$CT_ID" -- bash -lc "cd /opt/sammelmappe && sudo -u sammelmappe ./.venv/bin/python -c '
import os, sys
os.environ[\"DATA_DIR\"]=\"/opt/sammelmappe/data\"
from app.db import init_db
init_db()
from app.auth import set_password
set_password(sys.argv[1])
print(\"ok\")
' '$PASSWORD' >/dev/null"
  msg_ok "Passwort gesetzt"
}

# ---- IP detection -----------------------------------------------------------
get_ip() {
  local ip=""
  for i in {1..20}; do
    ip="$(pct exec "$CT_ID" -- bash -lc "hostname -I 2>/dev/null | awk '{print \$1}'" 2>/dev/null || true)"
    [[ -n "$ip" && "$ip" != "127.0.0.1" ]] && { echo "$ip"; return; }
    sleep 1
  done
  echo "(IP konnte nicht ermittelt werden — siehe pct config $CT_ID)"
}

# ---- summary ----------------------------------------------------------------
print_summary() {
  local IP; IP="$(get_ip)"
  cat <<EOF

  ${BOLD}${GREEN}Fertig.${NC}

    Container:  ${CYAN}${CT_ID}${NC}  ($HOSTNAME)
    URL:        ${CYAN}http://${IP}:${APP_PORT}/${NC}

  ${BOLD}Erste Schritte:${NC}
    1. URL im Browser öffnen
    2. ${PASSWORD:+Passwort eintragen — du hast es eben gesetzt.}${PASSWORD:-Passwort beim ersten Aufruf festlegen.}
    3. Auf dem iPhone: in Safari öffnen → Teilen → „Zum Home-Bildschirm"
       Damit ist die App in einer Sekunde griffbereit.

  ${BOLD}Verwaltung (von der Proxmox-Shell):${NC}
    pct exec ${CT_ID} -- systemctl status sammelmappe
    pct exec ${CT_ID} -- journalctl -u sammelmappe -f
    pct restart ${CT_ID}
    pct stop ${CT_ID}    # zum Anhalten
    pct destroy ${CT_ID} # zum komplett Entfernen

EOF
}

# ---- main -------------------------------------------------------------------
header
preflight
ask_settings
confirm_settings
download_template
create_container
wait_for_network
push_code
run_install_inside
preset_password
print_summary
