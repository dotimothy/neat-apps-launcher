# Shared terminal output for run.sh / setup.sh: the Neat sparkle banner and
# colourised status lines (same palette and glyphs as the studios). Plain text
# when stdout is not a TTY, TERM is dumb, or NO_COLOR is set.
# shellcheck shell=bash
if [[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-}" != "dumb" ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  P_TEAL=$'\033[38;2;61;179;138m'; P_GREEN=$'\033[38;2;74;168;54m'; P_LIME=$'\033[38;2;154;190;30m'
  P_BLUE=$'\033[38;2;58;125;216m'; P_ORANGE=$'\033[38;2;223;108;30m'; P_INK=$'\033[38;2;60;66;74m'
  C_ACCENT="${P_LIME}"; C_MUTED=$'\033[38;2;140;150;160m'
  C_OK=$'\033[38;2;53;196;137m'; C_WARN=$'\033[38;2;224;173;74m'; C_ERR=$'\033[38;2;239;91;98m'
else
  C_RESET=''; C_BOLD=''; C_DIM=''
  P_TEAL=''; P_GREEN=''; P_LIME=''; P_BLUE=''; P_ORANGE=''; P_INK=''
  C_ACCENT=''; C_MUTED=''; C_OK=''; C_WARN=''; C_ERR=''
fi

step() { printf '\n%s\n' "${C_ACCENT}${C_BOLD}▸${C_RESET} $*"; }
info() { printf '%s\n' "${C_MUTED}·${C_RESET} $*"; }
ok()   { printf '%s\n' "${C_OK}✔${C_RESET} $*"; }
warn() { printf '%s\n' "${C_WARN}⚠${C_RESET} $*" >&2; }
errln(){ printf '%s\n' "${C_ERR}✘${C_RESET} $*" >&2; }
die()  { errln "$*"; exit 1; }

banner() {
  printf '\n'
  printf '        %s▲%s\n'                   "${P_TEAL}"   "${C_RESET}"
  printf '       %s███%s\n'                  "${P_GREEN}"  "${C_RESET}"
  printf '      %s█████%s\n'                 "${P_GREEN}"  "${C_RESET}"
  printf '   %s◀████%s█%s████▶%s\n'          "${P_BLUE}" "${P_INK}" "${P_LIME}" "${C_RESET}"
  printf '      %s█████%s\n'                 "${P_ORANGE}" "${C_RESET}"
  printf '       %s███%s\n'                  "${P_ORANGE}" "${C_RESET}"
  printf '        %s▼%s\n'                   "${P_ORANGE}" "${C_RESET}"
  printf '\n'
  printf '   %sNEAT%s %sApps Launcher%s\n' "${C_BOLD}" "${C_RESET}" "${C_ACCENT}${C_BOLD}" "${C_RESET}"
  printf '   %sEvery Neat example app on SiMa.ai Modalix, live in Insight%s\n' "${C_MUTED}" "${C_RESET}"
  printf '\n'
}

spacer() {
  local palette=("${P_TEAL}" "${P_GREEN}" "${P_LIME}" "${P_BLUE}" "${P_ORANGE}") line="   " i
  for ((i = 0; i < 12; i++)); do line+="${palette[i % 5]}✦${C_RESET} "; done
  printf '%s\n' "${line}"
}

section() { printf '\n'; spacer; printf '   %s%s%s\n' "${C_BOLD}" "$1" "${C_RESET}"; }
