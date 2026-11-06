#!/usr/bin/env bash
# Set up Neat Apps Launcher: Python environment, TLS certificate, the
# `neat-apps` shell alias, and a desktop icon.
#
#   ./setup.sh                 interactive defaults (asks nothing it doesn't need)
#   ./setup.sh --no-alias      skip the shell alias
#   ./setup.sh --no-desktop    skip the desktop icon
#   ./setup.sh --no-prompt     never ask (used by run.sh on first start)
#   ./setup.sh --check         report what is set up, change nothing
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=src/common/ui.sh
source "${ROOT}/src/common/ui.sh"

alias_on=1 desktop_on=1 check=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-alias) alias_on=0 ;;
    --no-desktop|--no-desktop-icon) desktop_on=0 ;;
    --no-prompt) ;;
    --check) check=1 ;;
    -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
  shift
done
[[ "${CREATE_ALIAS:-1}" == "0" ]] && alias_on=0
[[ "${CREATE_DESKTOP_ICON:-1}" == "0" ]] && desktop_on=0

VENV="${ROOT}/.venv"
RUN="${ROOT}/run.sh"
APP_ID="neat-apps-launcher"
ICON="${ROOT}/src/launcher/static/icons/neat-logo.png"

[[ -z "${NO_BANNER:-}" ]] && banner

if [[ ${check} -eq 1 ]]; then
  section "Check"
  [[ -x "${VENV}/bin/python" ]] && "${VENV}/bin/python" -c "import flask_socketio, psutil, yaml" 2>/dev/null \
    && ok "Python environment: ${VENV}" || warn "Python environment missing (run ./setup.sh)"
  [[ -f "${ROOT}/.local-certs/launcher.crt" ]] && ok "TLS certificate" || info "TLS certificate is made on first start"
  grep -qs "alias neat-apps=" ~/.bashrc ~/.bash_profile && ok "neat-apps alias" || info "no neat-apps alias"
  [[ -f "${XDG_DATA_HOME:-${HOME}/.local/share}/applications/${APP_ID}.desktop" ]] && ok "desktop icon" || info "no desktop icon"
  exit 0
fi

# ---------------------------------------------------------------- python
section "Python environment"
command -v python3 >/dev/null 2>&1 || die "python3 is required."
if [[ ! -x "${VENV}/bin/python" ]]; then
  step "Creating ${VENV}"
  python3 -m venv "${VENV}" || die "python3 -m venv failed (install python3-venv)."
fi
step "Installing packages"
"${VENV}/bin/python" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
"${VENV}/bin/python" -m pip install -q -r "${ROOT}/requirements.txt"
ok "Flask, Flask-SocketIO, psutil and PyYAML are ready."

# ---------------------------------------------------------------- certificate
section "Certificate"
if [[ -f "${ROOT}/.local-certs/launcher.crt" ]]; then
  ok "Self-signed certificate already in .local-certs/"
elif command -v openssl >/dev/null 2>&1; then
  mkdir -p "${ROOT}/.local-certs"
  host="$(hostname)"
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout "${ROOT}/.local-certs/launcher.key" -out "${ROOT}/.local-certs/launcher.crt" \
    -subj "/CN=${host}" -addext "subjectAltName=DNS:${host},DNS:localhost,IP:127.0.0.1" >/dev/null 2>&1
  chmod 600 "${ROOT}/.local-certs/launcher.key"
  ok "Self-signed certificate created for ${host}"
else
  warn "openssl not found: the launcher will serve plain HTTP."
fi

# ---------------------------------------------------------------- aliases
add_alias() {
  local name="$1" rc="$2"
  if grep -qs "^alias ${name}=" "${rc}"; then
    sed -i "s|^alias ${name}=.*|alias ${name}='${RUN}'|" "${rc}"
  else
    printf "alias %s='%s'\n" "${name}" "${RUN}" >> "${rc}"
  fi
}
section "Shortcuts"
chmod +x "${RUN}" "${ROOT}/setup.sh" "${ROOT}/src/common/desktop-launch.sh" 2>/dev/null || true
if [[ ${alias_on} -eq 1 ]]; then
  # Login shells on the board read ~/.bash_profile (the studios put theirs
  # there too); interactive shells read ~/.bashrc. Write both.
  for rc in "${HOME}/.bashrc" "${HOME}/.bash_profile"; do
    [[ -f "${rc}" || "${rc}" == "${HOME}/.bashrc" ]] || continue
    touch "${rc}"
    add_alias neat-apps "${rc}"
  done
  ok "Alias ${C_BOLD}neat-apps${C_RESET} starts the launcher (open a new shell first)."
  info "Also: neat-apps stop | status | update | logs | --daemon | --open-browser"
else
  info "Shell alias skipped."
fi

# ---------------------------------------------------------------- desktop icon
desktop_entry() {
  local launch="${ROOT}/src/common/desktop-launch.sh" exec_line term="Terminal=false"
  if command -v x-terminal-emulator >/dev/null 2>&1; then
    exec_line="Exec=x-terminal-emulator -T \"Neat Apps Launcher\" -e \"${launch}\""
  else
    exec_line="Exec=\"${launch}\""; term="Terminal=true"
  fi
  printf '%s\n' "[Desktop Entry]" "Version=1.0" "Type=Application" "Name=Neat Apps Launcher" \
    "Comment=Run the Neat example apps on SiMa.ai Modalix, live in Neat Insight" \
    "${exec_line}" "Path=${ROOT}" "Icon=${ICON}" "${term}" "Categories=Development;" \
    "Keywords=SiMa;Modalix;Neat;Apps;Examples;Insight;Launcher;AI;" "StartupNotify=false"
}

trust_launcher() {
  local file="$1" bus="${DBUS_SESSION_BUS_ADDRESS:-}"
  command -v gio >/dev/null 2>&1 || return 1
  [[ -z "${bus}" && -S "/run/user/$(id -u)/bus" ]] && bus="unix:path=/run/user/$(id -u)/bus"
  [[ -n "${bus}" ]] || return 1
  DBUS_SESSION_BUS_ADDRESS="${bus}" gio set -t string "${file}" \
    metadata::xfce-exe-checksum "$(sha256sum "${file}" | cut -d' ' -f1)" >/dev/null 2>&1 || return 1
  DBUS_SESSION_BUS_ADDRESS="${bus}" gio set -t string "${file}" metadata::trusted true >/dev/null 2>&1 || true
}

if [[ ${desktop_on} -eq 1 ]]; then
  apps_dir="${XDG_DATA_HOME:-${HOME}/.local/share}/applications"
  desktop_dir="${XDG_DESKTOP_DIR:-${HOME}/Desktop}"
  if [[ -d "${desktop_dir}" || -d /usr/share/xsessions ]]; then
    mkdir -p "${apps_dir}"
    targets=("${apps_dir}/${APP_ID}.desktop")
    [[ -d "${desktop_dir}" ]] && targets+=("${desktop_dir}/${APP_ID}.desktop")
    trusted=1
    for t in "${targets[@]}"; do
      desktop_entry > "${t}"
      chmod 755 "${t}"
      trust_launcher "${t}" || trusted=0
    done
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "${apps_dir}" >/dev/null 2>&1 || true
    ok "Desktop icon: ${C_BOLD}Neat Apps Launcher${C_RESET} (desktop and applications menu)"
    [[ ${trusted} -eq 1 ]] || info "On the first double-click, choose ${C_BOLD}Mark Executable${C_RESET} (or Launch Anyway)."
  else
    info "No desktop on this board: icon skipped."
  fi
else
  info "Desktop icon skipped."
fi

section "Done"
ok "Neat Apps Launcher is set up."
info "Start it: ${C_BOLD}neat-apps${C_RESET} (or ${C_BOLD}./run.sh${C_RESET}), or double-click the desktop icon."
printf '\n'
