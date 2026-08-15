# Shell macros for the remote-scripted backend.
#
# Source this file in your shell:
#
#     source ~/projects/Pivot-Code-agent/scripts/pivot-remote-macros.sh
#
# Then drive an Pivot session from your terminal:
#
#     pivot-pending-last           # see Pivot's latest message
#     pivot-bash 'ls -la'          # call the Bash tool
#     pivot-wait                   # block until next pending call
#     pivot-text "I'm done."       # text-only turn
#     pivot-exit                   # ExitTask
#
# Default port is 8430. Override with ALAN_PORT=8431 before sourcing,
# or with ALAN_PORT=8431 prefixed on individual calls.
#
# Requires: curl, jq.

: "${ALAN_PORT:=8430}"
export ALAN_PORT

_alan_url() {
    echo "http://127.0.0.1:${ALAN_PORT}"
}

pivot-help() {
    cat <<EOF
Remote-scripted Pivot macros
---------------------------
  pivot-pending             pretty-print the current pending payload
  pivot-pending-last        just the most recent message
  pivot-pending-system      just the system prompt array
  pivot-pending-tools       just the available tool names
  pivot-session             session metadata (id, cwd, port, calls_served)
  pivot-health              {"ok": true} if the server is up
  pivot-wait                poll /api/pending until 200; returns when ready

  pivot-text TEXT           text-only response
  pivot-tool NAME INPUT_JSON [TEXT]
                           generic tool call, e.g.
                           pivot-tool Read '{"file_path":"solution.py"}'
  pivot-bash 'cmd'          shortcut for Bash tool
  pivot-read PATH           shortcut for Read tool
  pivot-submit              SubmitSolution
  pivot-exit                ExitTask (ends the session)

Port: ${ALAN_PORT} (override with ALAN_PORT=NNNN).
EOF
}

# ── Reads ────────────────────────────────────────────────────────────────

pivot-health() {
    curl -s "$(_alan_url)/api/health"
    echo
}

pivot-session() {
    curl -s "$(_alan_url)/api/session" | jq
}

pivot-pending() {
    curl -s "$(_alan_url)/api/pending" | jq
}

pivot-pending-last() {
    curl -s "$(_alan_url)/api/pending" | jq '.messages[-1]'
}

pivot-pending-system() {
    curl -s "$(_alan_url)/api/pending" | jq '.system'
}

pivot-pending-tools() {
    curl -s "$(_alan_url)/api/pending" | jq '.tools | map(.name)'
}

pivot-wait() {
    local i=0
    while [ "$(curl -s -o /dev/null -w '%{http_code}' "$(_alan_url)/api/pending")" != "200" ]; do
        i=$((i + 1))
        if [ $i -gt 200 ]; then
            echo "[pivot-wait] gave up after ~60s — server may be down" >&2
            return 1
        fi
        sleep 0.3
    done
    echo "[pivot-wait] ready"
}

# ── Writes ────────────────────────────────────────────────────────────────

pivot-text() {
    if [ "$#" -lt 1 ]; then
        echo "usage: pivot-text 'message'" >&2
        return 2
    fi
    local payload
    payload=$(jq -n --arg t "$1" '{text:$t}')
    curl -s -X POST "$(_alan_url)/api/respond" \
        -H 'Content-Type: application/json' -d "$payload"
    echo
}

pivot-tool() {
    if [ "$#" -lt 2 ]; then
        echo "usage: pivot-tool TOOL_NAME 'JSON_INPUT' [TEXT]" >&2
        return 2
    fi
    local name="$1"
    local input="$2"
    local text="${3:-}"
    local payload
    payload=$(jq -n \
        --arg t "$text" \
        --arg n "$name" \
        --argjson i "$input" \
        '{text:$t, tool_calls:[{name:$n, input:$i}]}')
    curl -s -X POST "$(_alan_url)/api/respond" \
        -H 'Content-Type: application/json' -d "$payload"
    echo
}

pivot-bash() {
    if [ "$#" -lt 1 ]; then
        echo "usage: pivot-bash 'shell command' [text]" >&2
        return 2
    fi
    local cmd="$1"
    local text="${2:-running shell command}"
    local input
    input=$(jq -n --arg c "$cmd" '{command:$c}')
    pivot-tool Bash "$input" "$text"
}

pivot-read() {
    if [ "$#" -lt 1 ]; then
        echo "usage: pivot-read /path/to/file" >&2
        return 2
    fi
    local path="$1"
    local input
    input=$(jq -n --arg p "$path" '{file_path:$p}')
    pivot-tool Read "$input" "reading $path"
}

pivot-submit() {
    pivot-tool SubmitSolution '{}' "submitting for eval"
}

pivot-exit() {
    pivot-tool ExitTask '{}' "done"
}
