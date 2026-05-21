#!/usr/bin/env bash
# Sammelmappe — LXC installer in the spirit of Proxmox community-scripts.
# Run this INSIDE a Debian 12 / Ubuntu 22.04+ LXC container as root.
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/chloepriceless/Sammelmappe/main/install.sh)"
#
# Or after `git clone`:
#   sudo ./install.sh

set -euo pipefail

# ---- pretty output ----------------------------------------------------------
RED=$'\033[1;31m'; GREEN=$'\033[1;32m'; YELLOW=$'\033[1;33m'; BLUE=$'\033[1;34m'; BOLD=$'\033[1m'; NC=$'\033[0m'
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

                  Sammelmappe — Bau-Belege bündeln und gemeinsam einreichen

EOF
}

# ---- preflight --------------------------------------------------------------
preflight() {
  [[ $EUID -eq 0 ]] || die "Bitte als root ausführen (sudo ./install.sh)"
  command -v apt-get >/dev/null || die "Dieser Installer setzt apt-get voraus (Debian/Ubuntu)."
  source /etc/os-release || true
  msg_ok "OS erkannt: ${PRETTY_NAME:-unbekannt}"
}

# ---- defaults / interactive -------------------------------------------------
APP_USER="${APP_USER:-sammelmappe}"
APP_DIR="${APP_DIR:-/opt/sammelmappe}"
APP_PORT="${APP_PORT:-8080}"
SERVICE_NAME="${SERVICE_NAME:-sammelmappe}"
REPO_URL="${REPO_URL:-https://github.com/chloepriceless/Sammelmappe.git}"

ask_settings() {
  if [[ -t 0 && -t 1 ]]; then
    read -r -p "  Port [${APP_PORT}]: " inp; APP_PORT="${inp:-$APP_PORT}"
    read -r -p "  Anthropic API Key (leer = Tesseract-only): " ANTHROPIC_API_KEY || true
  fi
  ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
}

# ---- system packages --------------------------------------------------------
install_system_deps() {
  msg_info "System aktualisieren …"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip python3-dev build-essential \
      tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng \
      poppler-utils \
      libjpeg-dev zlib1g-dev libfreetype6-dev liblcms2-dev libwebp-dev libtiff-dev \
      libpq-dev \
      curl ca-certificates git \
    >/dev/null
  msg_ok "Pakete installiert (Tesseract DEU/ENG, Poppler, Python 3)"
}

# ---- app user ---------------------------------------------------------------
create_user() {
  if ! id -u "$APP_USER" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "/var/lib/$APP_USER" --shell /usr/sbin/nologin "$APP_USER"
    msg_ok "User '${APP_USER}' angelegt"
  else
    msg_ok "User '${APP_USER}' existiert bereits"
  fi
}

# ---- code -------------------------------------------------------------------
fetch_code() {
  local SRC; SRC="$(dirname "$(readlink -f "$0")")"
  if [[ "$SRC" == "$APP_DIR" && -f "$APP_DIR/app/main.py" ]]; then
    msg_ok "Code liegt bereits in $APP_DIR"
  elif [[ -d "$APP_DIR/.git" ]]; then
    msg_info "Repo existiert — pulle aktuellsten Stand …"
    git -C "$APP_DIR" pull --ff-only
  elif [[ -f "$SRC/app/main.py" ]]; then
    msg_info "Code aus $SRC nach $APP_DIR kopieren …"
    mkdir -p "$APP_DIR"
    apt-get install -y --no-install-recommends rsync >/dev/null
    rsync -a --delete \
      --exclude='.git' --exclude='data/' --exclude='__pycache__' \
      --exclude='.venv/' --exclude='.pytest_cache' \
      "$SRC"/ "$APP_DIR"/
  else
    msg_info "Repo klonen …"
    git clone --depth=1 "$REPO_URL" "$APP_DIR"
  fi
  mkdir -p "$APP_DIR/data/invoices" "$APP_DIR/data/thumbnails" "$APP_DIR/data/exports"
  chown -R "$APP_USER:$APP_USER" "$APP_DIR"
  msg_ok "Code in $APP_DIR"
}

# ---- venv -------------------------------------------------------------------
setup_venv() {
  msg_info "Python venv + Dependencies installieren (das dauert kurz) …"
  runuser -u "$APP_USER" -- python3 -m venv "$APP_DIR/.venv"
  runuser -u "$APP_USER" -- "$APP_DIR/.venv/bin/pip" install --upgrade pip wheel >/dev/null
  runuser -u "$APP_USER" -- "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" >/dev/null
  msg_ok "venv bereit ($APP_DIR/.venv)"
}

# ---- env --------------------------------------------------------------------
write_env() {
  if [[ -f "$APP_DIR/.env" ]]; then
    msg_warn ".env existiert — bleibt unverändert"
    return
  fi
  local SECRET_KEY; SECRET_KEY="$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")"
  cat > "$APP_DIR/.env" <<EOF
HOST=0.0.0.0
PORT=${APP_PORT}
DATA_DIR=${APP_DIR}/data
SECRET_KEY=${SECRET_KEY}
SESSION_HOURS=720
TESSERACT_CMD=tesseract
TESSERACT_LANG=deu+eng
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
CLAUDE_MODEL=claude-haiku-4-5-20251001
OCR_CONFIDENCE_THRESHOLD=0.6
MAX_UPLOAD_MIB=25
EOF
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  msg_ok ".env geschrieben (mit zufälligem SECRET_KEY)"
}

# ---- systemd ----------------------------------------------------------------
write_systemd() {
  # Detect whether we're inside an LXC. Mount-namespace hardening (ProtectSystem,
  # PrivateTmp, ReadWritePaths, etc.) requires CAP_SYS_ADMIN inside the namespace,
  # which unprivileged LXCs don't have — the service would fail with
  # "Failed at step NAMESPACE". User isolation + NoNewPrivileges still apply.
  local IN_LXC=0
  if command -v systemd-detect-virt >/dev/null && [[ "$(systemd-detect-virt -c 2>/dev/null)" == "lxc" ]]; then
    IN_LXC=1
  fi

  local HARDENING
  if [[ $IN_LXC -eq 1 ]]; then
    HARDENING=$'# (LXC: mount-namespace hardening skipped — already isolated by the container)\nNoNewPrivileges=yes\nLockPersonality=yes\nRestrictRealtime=yes'
  else
    HARDENING=$'NoNewPrivileges=yes\nPrivateTmp=yes\nProtectSystem=strict\nProtectHome=yes\nReadWritePaths='"${APP_DIR}/data"$'\nProtectKernelTunables=yes\nProtectKernelModules=yes\nProtectControlGroups=yes\nLockPersonality=yes\nRestrictRealtime=yes'
  fi

  cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Sammelmappe — Bau-Belege bündeln und gemeinsam einreichen
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/uvicorn app.main:app --host \${HOST} --port \${PORT} --proxy-headers
Restart=on-failure
RestartSec=3

${HARDENING}

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}.service" >/dev/null
  msg_ok "systemd-Service ${SERVICE_NAME}.service eingerichtet$([[ $IN_LXC -eq 1 ]] && echo ' (LXC mode)')"
}

# ---- start ------------------------------------------------------------------
start_service() {
  systemctl restart "${SERVICE_NAME}.service"
  sleep 1
  if systemctl is-active --quiet "${SERVICE_NAME}.service"; then
    msg_ok "Service läuft"
  else
    msg_error "Service ist nicht gestartet — Logs: journalctl -u ${SERVICE_NAME} -e"
    exit 1
  fi
}

# ---- summary ----------------------------------------------------------------
print_summary() {
  local IP
  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  IP="${IP:-localhost}"
  cat <<EOF

  ${BOLD}${GREEN}Installation abgeschlossen.${NC}

  ${BOLD}Öffne im Browser:${NC}
    http://${IP}:${APP_PORT}/

  Beim ersten Aufruf legst du dein Passwort fest.
  Auf dem iPhone: in Safari öffnen → ${BOLD}Teilen → „Zum Home-Bildschirm"${NC} → wie eine App benutzen.

  ${BOLD}Service-Verwaltung:${NC}
    systemctl status   ${SERVICE_NAME}
    systemctl restart  ${SERVICE_NAME}
    journalctl -u ${SERVICE_NAME} -f

  ${BOLD}Konfiguration:${NC}
    ${APP_DIR}/.env

EOF
}

# ---- main -------------------------------------------------------------------
header
preflight
ask_settings
install_system_deps
create_user
fetch_code
setup_venv
write_env
write_systemd
start_service
print_summary
