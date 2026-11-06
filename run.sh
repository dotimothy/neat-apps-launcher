#!/usr/bin/env bash
# Neat Apps Launcher
#
#   ./run.sh                  start the launcher in this terminal (Ctrl+C stops it)
#   ./run.sh --daemon         start it in the background
#   ./run.sh --open-browser   start it (or find it running) and open the browser
#   ./run.sh stop             stop a running launcher (apps keep running)
#   ./run.sh status           is it running, and where
#   ./run.sh update           update the launcher from GitHub
#   ./run.sh logs             follow the background launcher's log
#
# Options: --port N (default 8091), --host H (default 0.0.0.0), --no-https.
# Alias installed by setup.sh: neat-apps.
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=src/common/ui.sh
source "${ROOT}/src/common/ui.sh"

VENV="${ROOT}/.venv"
PY="${VENV}/bin/python"
PID_FILE="${ROOT}/.neat-apps-launcher.pid"
URL_FILE="${ROOT}/.neat-apps-launcher.url"
LOG_DIR="${ROOT}/logs"
DAEMON_LOG="${LOG_DIR}/launcher.log"
REPO="${NEAT_LAUNCHER_REPO:-dotimothy/neat-apps-launcher}"
BRANCH="${NEAT_LAUNCHER_BRANCH:-main}"
export LAUNCHER_PORT="${LAUNCHER_PORT:-8091}"
export LAUNCHER_HOST="${LAUNCHER_HOST:-0.0.0.0}"
export LAUNCHER_HTTPS="${LAUNCHER_HTTPS:-1}"
[[ -f "${ROOT}/config.local.yaml" && -z "${NEAT_LAUNCHER_CONFIG:-}" ]] && export NEAT_LAUNCHER_CONFIG="${ROOT}/config.local.yaml"

cmd="run" daemon=0 open_browser=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    run|start) cmd="run"; shift ;;
    stop|status|update|logs) cmd="$1"; shift ;;
    --daemon|-d) daemon=1; shift ;;
    --open-browser) open_browser=1; shift ;;
    --port) LAUNCHER_PORT="${2:?--port needs a number}"; shift 2 ;;
    --host) LAUNCHER_HOST="${2:?--host needs an address}"; shift 2 ;;
    --no-https) LAUNCHER_HTTPS=0; shift ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done

scheme() { [[ "${LAUNCHER_HTTPS}" == "0" ]] && echo http || echo https; }
lan_ip() {
  local ip
  ip="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v ':' | grep -m1 . || true)"
  echo "${ip:-localhost}"
}
local_url() { echo "$(scheme)://localhost:${LAUNCHER_PORT}/"; }
lan_url() { echo "$(scheme)://$(lan_ip):${LAUNCHER_PORT}/"; }

running_pid() {
  local pid
  [[ -f "${PID_FILE}" ]] || return 1
  pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null && grep -q "src/launcher/main.py" "/proc/${pid}/cmdline" 2>/dev/null \
    && { echo "${pid}"; return 0; }
  rm -f "${PID_FILE}" "${URL_FILE}"
  return 1
}

answers() { curl -ks --max-time 2 -o /dev/null -w '%{http_code}' "$1health" 2>/dev/null | grep -q '^200$'; }

open_url() {
  local url="$1" opener=""
  for o in xdg-open sensible-browser x-www-browser firefox chromium chromium-browser; do
    command -v "$o" >/dev/null 2>&1 && { opener="$o"; break; }
  done
  if [[ -z "${opener}" ]]; then info "Open ${C_BOLD}${url}${C_RESET} in a browser."; return; fi
  [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]] && export DISPLAY=:0
  (setsid "${opener}" "${url}" >/dev/null 2>&1 &)
}

wait_and_open() {
  local url; url="$(local_url)"
  for _ in $(seq 1 120); do
    if answers "${url}"; then open_url "${url}"; return 0; fi
    sleep 0.5
  done
  warn "The launcher did not answer in time; open ${url} by hand."
}

ensure_env() {
  if [[ ! -x "${PY}" ]] || ! "${PY}" -c "import flask_socketio, psutil, yaml" 2>/dev/null; then
    step "First run: setting up the launcher"
    NO_BANNER=1 "${ROOT}/setup.sh" --no-prompt
  fi
}

do_stop() {
  local pid
  if ! pid="$(running_pid)"; then info "The launcher is not running."; return 0; fi
  kill -TERM "${pid}" 2>/dev/null || true
  for _ in $(seq 1 40); do kill -0 "${pid}" 2>/dev/null || break; sleep 0.25; done
  kill -0 "${pid}" 2>/dev/null && kill -KILL "${pid}" 2>/dev/null || true
  rm -f "${PID_FILE}" "${URL_FILE}"
  ok "Launcher stopped (apps and Insight keep running; stop them from the Manager page)."
}

do_update() {
  section "Updating Neat Apps Launcher"
  if [[ -d "${ROOT}/.git" ]]; then
    step "git pull (${BRANCH})"
    git -C "${ROOT}" pull --ff-only
  else
    local tmp; tmp="$(mktemp -d)"
    # shellcheck disable=SC2064
    trap "rm -rf '${tmp}'" RETURN
    step "Downloading ${REPO} (${BRANCH})"
    curl -fsSL "https://github.com/${REPO}/archive/${BRANCH}.tar.gz" -o "${tmp}/src.tar.gz"
    mkdir -p "${tmp}/src"
    tar -xzf "${tmp}/src.tar.gz" -C "${tmp}/src" --strip-components=1
    [[ -f "${tmp}/src/run.sh" ]] || die "that archive is not the launcher"
    (cd "${tmp}/src" && tar -cf - --exclude=.venv --exclude=logs --exclude=.local-certs --exclude=config.local.yaml --exclude=runs --exclude=.cache .) \
      | (cd "${ROOT}" && tar -xf -)
    curl -fsSL "https://api.github.com/repos/${REPO}/commits/${BRANCH}" 2>/dev/null \
      | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({"sha": d["sha"], "date": d["commit"]["committer"]["date"]}))' \
      > "${ROOT}/.neat-launcher-version" 2>/dev/null || rm -f "${ROOT}/.neat-launcher-version"
  fi
  if [[ -x "${PY}" ]]; then
    step "Updating Python packages"
    "${PY}" -m pip install -q -r "${ROOT}/requirements.txt"
  fi
  ok "Updated to $(cat "${ROOT}/VERSION" 2>/dev/null || echo latest)."
  running_pid >/dev/null && info "Restart to apply: ${C_BOLD}neat-apps stop && neat-apps --daemon${C_RESET}"
  return 0
}

do_status() {
  local pid
  if pid="$(running_pid)"; then
    ok "Running (pid ${pid}) at ${C_BOLD}$(cat "${URL_FILE}" 2>/dev/null || lan_url)${C_RESET}"
  else
    info "Not running. Start it with ${C_BOLD}neat-apps${C_RESET}."
    return 3
  fi
}

case "${cmd}" in
  stop) do_stop; exit 0 ;;
  status) do_status; exit $? ;;
  update) do_update; exit 0 ;;
  logs) exec tail -n 200 -F "${DAEMON_LOG}" ;;
esac

banner
if pid="$(running_pid)"; then
  ok "Already running (pid ${pid}) at ${C_BOLD}$(cat "${URL_FILE}" 2>/dev/null || lan_url)${C_RESET}"
  [[ ${open_browser} -eq 1 ]] && open_url "$(local_url)"
  exit 0
fi
if command -v ss >/dev/null 2>&1 && ss -ltn "sport = :${LAUNCHER_PORT}" 2>/dev/null | grep -q LISTEN; then
  die "port ${LAUNCHER_PORT} is in use by another program. Pick another with --port."
fi

ensure_env
mkdir -p "${LOG_DIR}"
lan_url > "${URL_FILE}"
args=("${ROOT}/src/launcher/main.py" --host "${LAUNCHER_HOST}" --port "${LAUNCHER_PORT}")
[[ "${LAUNCHER_HTTPS}" == "0" ]] && args+=(--no-https)

info "Apps:      ${C_DIM}${NEAT_APPS_ROOT:-/media/nvme}/prebuilt-apps${C_RESET}"
info "Open:      ${C_BOLD}$(lan_url)${C_RESET}  (on this board: $(local_url))"
[[ "${LAUNCHER_HTTPS}" != "0" ]] && info "The certificate is self-signed: accept it once in the browser."

if [[ ${daemon} -eq 1 ]]; then
  setsid nohup "${PY}" "${args[@]}" >> "${DAEMON_LOG}" 2>&1 < /dev/null &
  echo $! > "${PID_FILE}"
  for _ in $(seq 1 60); do answers "$(local_url)" && break; sleep 0.5; done
  if answers "$(local_url)"; then ok "Launcher running in the background (pid $(cat "${PID_FILE}")). Log: ${DAEMON_LOG}"
  else warn "Started, but not answering yet. Check ${C_BOLD}./run.sh logs${C_RESET}."; fi
  [[ ${open_browser} -eq 1 ]] && open_url "$(local_url)"
  exit 0
fi

[[ ${open_browser} -eq 1 ]] && wait_and_open &
echo $$ > "${PID_FILE}"
trap 'rm -f "${PID_FILE}" "${URL_FILE}"' EXIT
ok "Starting… press ${C_BOLD}Ctrl+C${C_RESET} to stop the launcher (apps keep running)."
printf '\n'
# exec keeps the pid in the pid file (the python process replaces this shell).
trap - EXIT
exec "${PY}" "${args[@]}"
