#!/usr/bin/env bash
# Started by the desktop icon inside a terminal window: starts the launcher in
# the background (or finds it running) and opens it in the browser. The
# launcher keeps running after this window closes; stop it with
# `neat-apps stop`.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}" || exit 1
"${ROOT}/run.sh" --daemon --open-browser
status=$?
if [[ ${status} -ne 0 ]]; then
  printf '\nNeat Apps Launcher could not start (status %s).\n' "${status}"
  read -r -p "Press Enter to close this window… " _ || true
else
  sleep 2
fi
exit "${status}"
