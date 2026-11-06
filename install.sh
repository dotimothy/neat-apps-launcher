#!/usr/bin/env bash
# Install Neat Apps Launcher on a SiMa.ai Modalix board.
#
#   cd /media/nvme && curl -fsSL https://raw.githubusercontent.com/dotimothy/neat-apps-launcher/main/install.sh | bash
#
# Fetches the source into ./neat-apps-launcher and runs ./setup.sh, which
# installs the `neat-apps` alias and a desktop icon.
#
# Environment:
#   STUDIO_DIR      Where to install. Default: ./neat-apps-launcher
#   STUDIO_BRANCH   Branch or tag. Default: main
#   STUDIO_REPO     owner/name. Default: dotimothy/neat-apps-launcher
#   GITHUB_TOKEN    Token for a private repository.
#   STUDIO_ARCHIVE  A local .tar.gz (or URL) to install from instead.
#   STUDIO_SETUP=0  Only fetch the source; skip ./setup.sh.
#   STUDIO_START=1  Start the launcher in the background when done.
set -euo pipefail

main() {
  local repo="${STUDIO_REPO:-dotimothy/neat-apps-launcher}"
  local branch="${STUDIO_BRANCH:-main}"
  local dir="${1:-${STUDIO_DIR:-neat-apps-launcher}}"
  local archive="${STUDIO_ARCHIVE:-}"
  say()  { printf '%s\n' "$*"; }
  fail() { printf 'install: %s\n' "$*" >&2; exit 1; }

  command -v tar >/dev/null 2>&1 || fail "tar is required."
  if [[ -e "${dir}" ]]; then
    [[ -f "${dir}/run.sh" ]] && fail "${dir} already holds an install. Update it with: neat-apps update"
    [[ -d "${dir}" && -z "$(ls -A "${dir}" 2>/dev/null)" ]] || fail "${dir} exists and is not empty. Choose another place with STUDIO_DIR=/path."
  fi

  local tmp; tmp="$(mktemp -d)" || fail "mktemp failed."
  # shellcheck disable=SC2064
  trap "rm -rf '${tmp}'" EXIT
  if [[ -n "${archive}" && -f "${archive}" ]]; then
    cp "${archive}" "${tmp}/src.tar.gz"
  else
    local url="${archive}"; local -a auth=()
    if [[ -z "${url}" ]]; then
      if [[ -n "${GITHUB_TOKEN:-}" ]]; then url="https://api.github.com/repos/${repo}/tarball/${branch}"
      else url="https://github.com/${repo}/archive/${branch}.tar.gz"; fi
    fi
    [[ -n "${GITHUB_TOKEN:-}" ]] && auth=( -H "Authorization: Bearer ${GITHUB_TOKEN}" )
    say "Fetching Neat Apps Launcher (${repo}, ${branch})"
    curl -fsSL ${auth[@]+"${auth[@]}"} "${url}" -o "${tmp}/src.tar.gz" || fail "download failed: ${url}"
  fi
  tar -tzf "${tmp}/src.tar.gz" > "${tmp}/list" 2>/dev/null || fail "not a readable .tar.gz archive."
  grep -qE '^[^/]+/run\.sh$' "${tmp}/list" || fail "that archive is not Neat Apps Launcher."
  mkdir -p "${dir}"
  tar -xzf "${tmp}/src.tar.gz" -C "${dir}" --strip-components=1 || fail "extract failed."
  chmod +x "${dir}/run.sh" "${dir}/setup.sh" "${dir}/src/common/desktop-launch.sh" 2>/dev/null || true
  say "Installed in ${dir}"

  if [[ "${STUDIO_SETUP:-1}" == "1" ]]; then
    ( cd "${dir}" && ./setup.sh --no-prompt )
  fi
  if [[ "${STUDIO_START:-0}" == "1" ]]; then
    ( cd "${dir}" && ./run.sh --daemon )
  else
    say ""
    say "Start it with:  neat-apps   (open a new shell first, or: ${dir}/run.sh)"
  fi
}

main "$@"
