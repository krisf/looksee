#!/usr/bin/env bash
# Headless GUI smoke runner — the ONLY sanctioned way to run the GTK4 GUI
# headlessly (PLAN R1).  Recipe: xvfb-run with GSK_RENDERER=cairo,
# LIBGL_ALWAYS_SOFTWARE=1, GDK_BACKEND=x11 and WAYLAND_DISPLAY unset;
# otherwise every GTK4 app dies silently under Xvfb (incident ledger #1).
#
# usage: scripts/headless-smoke.sh <python-args...>
#          e.g. scripts/headless-smoke.sh src/looksee.py testdata/1.png
#
# env:
#   LOOKSEE_SMOKE_MODE  alive (default) — app must SURVIVE the whole window
#                         (exit via SIGTERM from `timeout`, i.e. rc 124/143,
#                         and total elapsed >= 6s).
#                       quit — app must exit 0 on its own before the watchdog.
#   LOOKSEE_SMOKE_SECS  alive: survival window (default 7 > required 6).
#                       quit:  watchdog cap    (default 30).
#   LOOKSEE_SMOKE_LOG   stderr capture path (default scratch/smoke-stderr.log).
#   LOOKSEE_SMOKE_INTERP interpreter prefix for the command
#                       (default /usr/bin/python3 — R3; set to /bin/sh only
#                       to smoke a packaged /usr/bin/looksee wrapper).
set -u

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${LOOKSEE_SMOKE_MODE:-alive}"
secs="${LOOKSEE_SMOKE_SECS:-}"
if [[ -z "$secs" ]]; then
  case "$mode" in
    alive) secs=7 ;;
    quit)  secs=30 ;;
    *) echo "headless-smoke: bad LOOKSEE_SMOKE_MODE=$mode" >&2; exit 2 ;;
  esac
fi
log="${LOOKSEE_SMOKE_LOG:-$here/scratch/smoke-stderr.log}"
mkdir -p "$(dirname "$log")"

export GSK_RENDERER=cairo LIBGL_ALWAYS_SOFTWARE=1 GDK_BACKEND=x11
# memory gsettings: /run/user dconf is read-only in the sandbox (noise only).
export GSETTINGS_BACKEND=memory
unset WAYLAND_DISPLAY
# Keep xvfb-run's scratch files (auth cookie, temp dirs) inside the repo:
# the ambient TMPDIR may be read-only, and R2 wants writes repo-local.
export TMPDIR="$here/scratch/xvfb-tmp"
mkdir -p "$TMPDIR"

start=$(date +%s)
interp="${LOOKSEE_SMOKE_INTERP:-/usr/bin/python3}"
xvfb-run -a --server-args="-screen 0 1600x1000x24" \
  timeout -s TERM "$secs" "$interp" "$@" >"$log.out" 2>"$log"
rc=$?
elapsed=$(( $(date +%s) - start ))

status=PASS
if grep -Eq 'Traceback|Fatal' "$log"; then
  status=FAIL
  echo "headless-smoke: stderr contains Traceback/Fatal" >&2
fi
if [[ "$mode" == alive ]]; then
  if [[ $rc -ne 124 && $rc -ne 143 ]] || [[ $elapsed -lt 6 ]]; then
    status=FAIL
    echo "headless-smoke(alive): expected rc 124/143 after >=6s survival, got rc=$rc elapsed=${elapsed}s" >&2
  fi
else
  if [[ $rc -ne 0 ]]; then
    status=FAIL
    echo "headless-smoke(quit): expected clean exit 0 before ${secs}s watchdog, got rc=$rc elapsed=${elapsed}s" >&2
  fi
fi

if [[ "$status" == FAIL ]]; then
  echo "--- SMOKE FAIL (mode=$mode rc=$rc elapsed=${elapsed}s) stderr follows ---" >&2
  cat "$log" >&2
  exit 1
fi
echo "SMOKE OK (mode=$mode elapsed=${elapsed}s) — stderr captured at ${log#"$here/"}"
