#!/usr/bin/env bash
# browser-reaper.sh — Kill orphaned Hermes agent-browser Chromium trees
#
# Hermes's browser tool cleans up its Chromium sessions via a Python atexit
# handler, but `systemctl restart hermes-gateway` (and any hard crash) sends
# SIGTERM straight to the interpreter with no handler installed for that
# path, so the atexit cleanup never runs and the orphan reaper never gets a
# chance to sweep. Orphaned chrome trees (reparented to PID 1) then pile up
# silently until they exhaust memory/swap. This is a stopgap outside Hermes
# until that's fixed upstream.
#
# Runs every 15 min via cron. Zero LLM tokens.

MIN_AGE_SECONDS=900  # only reap trees older than 15 min, to avoid racing a session that's mid-launch
NOW=$(date +%s)

for pid in $(pgrep -f "agent-browser-chrome"); do
    ppid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    [ "$ppid" != "1" ] && continue

    start=$(stat -c %Y "/proc/$pid" 2>/dev/null) || continue
    age=$((NOW - start))
    [ "$age" -lt "$MIN_AGE_SECONDS" ] && continue

    # Grandchildren (zygote, gpu-process, renderer) aren't direct children of
    # $pid, but every process in the tree shares the same --user-data-dir, so
    # match on that to kill the whole tree regardless of depth.
    user_data_dir=$(tr '\0' '\n' < "/proc/$pid/cmdline" 2>/dev/null | grep -m1 '^--user-data-dir=')
    [ -z "$user_data_dir" ] && continue

    echo "$(date -u +%FT%TZ) reaping orphaned chrome tree, top pid=$pid age=${age}s dir=$user_data_dir"
    pkill -9 -f -- "$user_data_dir" 2>/dev/null
done
