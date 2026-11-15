#!/usr/bin/env bash
# Restart the pair after an unplanned stop: a memguard trip or a container that exited on its own.
# A deliberate ./stop.sh is left alone (its archive has no memguard trip and it removes the containers).
# Pause it with: touch .state/watchdog.pause   (rm to resume)
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
PORT="$(bash -c 'source scripts/config.sh >/dev/null 2>&1; echo "${PORT:-8020}"')"
URL="http://127.0.0.1:${PORT:-8020}"
INTERVAL="${WATCHDOG_INTERVAL_S:-30}"
FAILS_NEEDED="${WATCHDOG_FAILS:-3}"
BACKOFF="${WATCHDOG_BACKOFF_S:-600}"
STATE=".state/watchdog.last-handled"
mkdir -p .state

log() { printf '%s [watchdog] %s\n' "$(date -u +%FT%TZ)" "$*"; }

lock_busy() { ( exec 8>.state/start.lock; ! flock -n 8 ); }

# Prints a reason when the pair needs a restart; nothing otherwise.
restart_reason() {
  local st
  st="$(docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' glm53-spark 2>/dev/null)" || st=""
  if [[ -n "$st" && "$st" != running* ]]; then echo "container glm53-spark is '$st'"; return; fi
  local newest
  newest="$(ls -t logs/glm53-spark-*.tar.gz 2>/dev/null | head -1)"
  [[ -n "$newest" ]] || return
  [[ "$(cat "$STATE" 2>/dev/null)" == "$newest" ]] && return
  if tar -xOzf "$newest" ./memguard.log 2>/dev/null | grep -q 'stopping the server'; then
    echo "memguard stopped the pair ($newest)"
  fi
}

log "watching $URL every ${INTERVAL}s"
fails=0
while sleep "$INTERVAL"; do
  if curl --fail --silent --max-time 10 "$URL/health" >/dev/null 2>&1; then fails=0; continue; fi
  [[ -e .state/watchdog.pause ]] && continue
  lock_busy && { fails=0; continue; }          # a start or stop is in progress
  (( ++fails < FAILS_NEEDED )) && continue
  fails=0
  reason="$(restart_reason)"
  [[ -n "$reason" ]] || continue               # down on purpose: leave it
  log "restarting: $reason"
  newest="$(ls -t logs/glm53-spark-*.tar.gz 2>/dev/null | head -1)"
  [[ -n "$newest" ]] && echo "$newest" > "$STATE"
  if ./start.sh </dev/null; then
    log "start.sh finished: server is up"
  else
    log "start.sh failed (exit $?); waiting ${BACKOFF}s before trying again"
    sleep "$BACKOFF"
  fi
done
