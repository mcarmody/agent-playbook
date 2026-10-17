#!/usr/bin/env bash
# wait-for.sh — wait for a shell condition to become true, then exit.
#
# Replaces a blocked `sleep N; tail <log>` polling pattern with a condition-based
# until-loop. An agent watching a background job writes one call instead of a
# sleep and a tail:
#
#   wait-for.sh "grep -q DONE /tmp/harvest.log"
#   wait-for.sh "test -f /tmp/out.json" --timeout 90 --interval 10
#
# The CONDITION is any shell command; success (exit 0) ends the wait. It runs in
# a subshell with default shell options (no -u, no pipefail), as at a normal
# prompt, so `exit N` inside it just sets the condition result. On success,
# if the condition referenced a single readable file, its tail is printed for
# convenience. Exits 0 when the condition is met, 124 on timeout.
#
# THE 120-SECOND CAP. The Bash tool's default timeout is 120s, and it kills this
# script with SIGTERM (exit 143) rather than letting the timeout path run — so
# the caller gets "Command timed out after 2m 0s" and never learns WHICH condition
# was still false. The default below is deliberately inside that cap so the
# script's own 124 path always fires first and always names the condition.
#
# For a genuinely longer wait, raise the Bash tool's own timeout to match and pass
# --long to say you have done so. Without it a >110s wait is refused in one second
# instead of dying silently at two minutes. Repeated SIGTERM kills in one window
# each lost the condition name, which is what this layout prevents.
set -uo pipefail

print_usage() {
  cat <<'EOT'
usage: wait-for.sh [--timeout SECONDS] [--interval SECONDS] [--tail FILE] [--long] "<condition-cmd>"
       wait-for.sh --sleep SECONDS
       wait-for.sh -h | --help

  --timeout N   give up after N seconds (default 105, exit 124)
  --interval N  seconds between polls (default 5)
  --tail FILE   print the last lines of FILE when the wait ends
  --long        allow --timeout above 110s (raise the Bash tool timeout to match)
  --sleep N     plain timed delay of N seconds, no condition (must be the first arg)

The condition runs in a subshell with default shell options (no -u, no pipefail);
`exit N` inside it just sets the condition result.

Options may appear before or after the condition. Exit: 0 met, 124 timeout, 2 usage.
EOT
}

usage() {
  print_usage >&2
  exit 2
}

[ $# -ge 1 ] || usage

# --sleep N: a plain timed delay, for when there is genuinely nothing to poll
# (e.g. "give the deploy 5 minutes, then check once"). Foreground `sleep` is
# rejected in some harnesses, so an agent reaching for it loses a turn to the
# rejection; this is the permitted equivalent of a `sleep N; <cmd>`.
# Prefer a real condition when one
# exists — a condition finishes as soon as it is true, a delay always burns
# the full N.
if [ "$1" = "--sleep" ]; then
  DELAY="${2:?--sleep needs a value}"
  start=$(date +%s)
  while [ $(( $(date +%s) - start )) -lt "$DELAY" ]; do
    sleep 5
  done
  exit 0
fi

# Inside the Bash tool's 120s cap on purpose — see THE 120-SECOND CAP above.
TIMEOUT=105
HARNESS_CAP=110
LONG_OK=0
INTERVAL=5
TAIL_FILE=""
CONDITION=""
HAVE_COND=0

# Options may appear in any position; the single non-flag argument is the
# condition. Anything starting with "-" that is not a known option is rejected
# here and never reaches eval (a bare --help used to be eval'd as the condition
# and burn the full timeout).
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)  print_usage; exit 0 ;;
    --timeout)  TIMEOUT="${2:?--timeout needs a value}"; shift 2 ;;
    --interval) INTERVAL="${2:?--interval needs a value}"; shift 2 ;;
    --tail)     TAIL_FILE="${2:?--tail needs a value}"; shift 2 ;;
    --long)     LONG_OK=1; shift ;;
    -*)         echo "wait-for.sh: unknown option '$1'" >&2; usage ;;
    *)
      if [ "$HAVE_COND" -eq 1 ]; then
        echo "wait-for.sh: more than one condition ('$CONDITION' and '$1')" >&2; usage
      fi
      CONDITION="$1"; HAVE_COND=1; shift ;;
  esac
done

[ "$HAVE_COND" -eq 1 ] || { echo "wait-for.sh: no condition given" >&2; usage; }

# Fail fast rather than at two minutes. A --timeout above the harness cap is
# unsatisfiable by construction: the Bash tool SIGTERMs us first, so the wait
# neither completes nor reports what it was waiting for.
if [ "$TIMEOUT" -gt "$HARNESS_CAP" ] && [ "$LONG_OK" -eq 0 ]; then
  cat >&2 <<EOF
wait-for.sh: --timeout $TIMEOUT exceeds the ${HARNESS_CAP}s Bash-tool cap and cannot complete.

The harness kills this script at 120s with SIGTERM (exit 143) before its own
timeout path can name the condition. Either:
  - lower --timeout to <= $HARNESS_CAP, or
  - raise the Bash tool's timeout to cover $TIMEOUT seconds and re-run with --long.

Refusing now instead of dying silently in two minutes.
EOF
  exit 2
fi

# pgrep -f self-match guard.
#
# The CONDITION string sits in this script's own argv, and in the argv of the
# `bash -c` that launched it. So a condition like
#   ! pgrep -f 'some-job.sh --job reflection'
# has `pgrep -f` match wait-for.sh itself, forever — the wait can never end no
# matter what the real process does. That deadlocked a real waiter for 90 minutes
# until it was killed by hand. The same `-f` self-match trap applies to any
# kill-side helper that matches on argv.
#
# The condition is eval'd in a subshell of this shell (which inherits functions),
# so a function named `pgrep` shadows the binary for it. It drops any match that is in this process's own family tree —
# both directions:
#
#   ancestors   — wait-for.sh itself, the `timeout`/`bash -c` that launched it,
#                 the agent harness above that. All carry the pattern in argv.
#   descendants — every `$(...)` command substitution bash forks here starts as
#                 a copy of wait-for.sh, argv and all, before it execs. Those
#                 transient subshells match too, and filtering ancestors alone
#                 does not catch them (found the hard way).
#
# Only the default PID-listing output is filtered — `pgrep -c` (count) and
# `pgrep -d` (custom delimiter) are passed through unfiltered and will still
# self-match. Use a real condition, not a count, when it matters.
ppid_of() {   # read ppid from /proc rather than forking `ps` — a fork here is
              # itself a self-matching process, which is the bug we are fixing
  local stat
  [ -r "/proc/$1/stat" ] || return 1
  stat=$(< "/proc/$1/stat")
  stat=${stat#*) }          # skip pid and the (comm) field, which may contain spaces
  set -- $stat
  printf '%s\n' "$2"        # state, ppid
}

pgrep() {
  local kin p out pid keep filtered
  kin=" "
  p=$$
  while [ -n "$p" ] && [ "$p" != "0" ] && [ "$p" != "1" ]; do
    kin="$kin$p "
    p=$(ppid_of "$p") || break
  done

  out=$(command pgrep "$@" 2>/dev/null) || return 1
  [ -n "$out" ] || return 1

  case " $* " in
    *" -c "*|*" -d "*) printf '%s\n' "$out"; return 0 ;;
  esac

  local selfcmd
  selfcmd=$(tr '\0' ' ' < "/proc/$$/cmdline" 2>/dev/null)

  filtered=""
  for pid in $out; do
    case "$kin" in *" $pid "*) continue ;; esac   # self or ancestor

    # Already gone. Every `$(...)` fork here is briefly a copy of wait-for.sh
    # and shows up in our own pgrep output before it execs or exits, so a
    # candidate that has vanished by the time we classify it is ours, not the
    # process being waited on. Failing open here is what kept the guard broken.
    [ -r "/proc/$pid/stat" ] || continue

    # A fork of this very script: same argv, different pid, not yet exec'd.
    [ "$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)" = "$selfcmd" ] && continue

    keep=1                                        # walk up: descendant of self?
    p=$pid
    while [ -n "$p" ] && [ "$p" != "0" ] && [ "$p" != "1" ]; do
      p=$(ppid_of "$p") || break
      [ "$p" = "$$" ] && { keep=0; break; }
    done
    [ "$keep" = 1 ] && filtered="$filtered$pid
"
  done

  [ -n "$filtered" ] || return 1
  printf '%s' "$filtered"
}

start=$(date +%s)
while true; do
  # Subshell with default shell options: `exit N` inside the condition only sets
  # its result, and our `set -u`/pipefail do not leak into it.
  if ( set +u +o pipefail; eval "$CONDITION" ) >/dev/null 2>&1; then
    echo "wait-for: condition met after $(( $(date +%s) - start ))s: $CONDITION"
    [ -n "$TAIL_FILE" ] && [ -r "$TAIL_FILE" ] && { echo "--- tail $TAIL_FILE ---"; tail -n 20 "$TAIL_FILE"; }
    exit 0
  fi
  if [ $(( $(date +%s) - start )) -ge "$TIMEOUT" ]; then
    echo "wait-for: TIMEOUT after ${TIMEOUT}s waiting for: $CONDITION" >&2
    [ -n "$TAIL_FILE" ] && [ -r "$TAIL_FILE" ] && { echo "--- tail $TAIL_FILE ---" >&2; tail -n 20 "$TAIL_FILE" >&2; }
    exit 124
  fi
  sleep "$INTERVAL"
done
