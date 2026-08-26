# Quality Test: Code Generation
## Prompt
Write a bash script that parses docker container logs...

## Output

```bash
<think>
The user wants a bash script that:
1. Parses Docker container logs from stdin
2. Identifies ERROR and FATAL messages
3. Extracts timestamp and container name if present
4. Outputs a summary CSV with columns: timestamp, container, severity, message
5. Handles multi-line log formats gracefully

Let me think about Docker log formats:

Docker logs can come in several formats:
- `docker logs` (plain text) - just the output, no metadata
- `docker logs --timestamps` - prepends ISO 8601 timestamp
- `docker logs --details` - adds extra metadata
- `docker logs --format json` - JSON format with fields like `log`, `time`, `stream`, `status`
- `docker logs --format json --details` - includes container name, etc.
- `docker logs --format json --details` output looks like:
  ```
  {"log":"2023-01-01T00:00:00.000Z ERROR message\n","time":"2023-01-01T00:00:00.000Z","stream":"stderr","status":"200"}
  ```
- With `--details`:
  ```
  {"log":"...","time":"...","stream":"stderr","status":"200","container":"abc123","container_name":"my_container"}
  ```

Also, Docker logs can have multi-line messages where a single log entry spans multiple lines (e.g., stack traces). The JSON format helps with this since each JSON object is one line.

For the plain text format, multi-line is harder to handle.

Let me design a script that:
1. Accepts stdin (could be JSON format or plain text)
2. Detects the format
3. For JSON format: parse each JSON line, extract fields
4. For plain text: try to parse timestamps and container names from the log lines
5. Filter for ERROR and FATAL
6. Handle multi-line: in JSON format, each line is a complete entry. In plain text, we need to handle continuation lines.
7. Output CSV: timestamp, container, severity, message

Let me think about the approach:

For JSON format (`docker logs --format json`):
- Each line is a JSON object
- Fields: `log` (the actual log line), `time` (timestamp), `stream` (stdout/stderr), `status`, and with `--details`: `container`, `container_name`
- The `log` field might contain the actual message which could have ERROR/FATAL in it
- Multi-line: the `log` field might contain newlines (escaped as \n in JSON)

For plain text format:
- Lines might start with a timestamp (if `--timestamps` was used)
- Format: `2023-01-01T00:00:00.000Z message`
- Or just raw output without timestamps
- Container name might be in the format: `container_name | message` (some logging drivers)
- Multi-line: continuation lines don't have timestamps

Let me write a comprehensive script that handles both formats.

I'll use a combination of bash, sed, awk, and possibly jq for JSON parsing. But to keep it as a bash script, I'll try to use standard tools. Actually, let me use a hybrid approach - bash with awk for the heavy lifting, and handle JSON parsing with a simple approach or suggest jq.

Actually, let me think more carefully. The script should:
1. Detect if input is JSON (lines starting with `{`)
2. If JSON: parse each line as JSON, extract fields
3. If plain text: parse line by line, handle multi-line entries
4. Filter for ERROR/FATAL
5. Output CSV

For multi-line handling in plain text:
- A new log entry starts with a timestamp pattern
- Lines without a timestamp are continuations of the previous entry
- We accumulate continuation lines until we hit a new timestamp or EOF

For JSON format, multi-line is handled naturally since each JSON object is one line, but the `log` field might contain `\n` characters.

Let me write this script now. I'll make it robust and well-documented.

I'll structure it as:
- Header with usage
- Configuration/variables
- CSV header output
- Main parsing logic (detect format, parse)
- Multi-line handling
- ERROR/FATAL filtering
- CSV output

Let me write a thorough, production-quality script.
</think>

# Docker Log Parser – Bash Script

```bash
#!/usr/bin/env bash
#
# docker-log-parser.sh
# Parses Docker container logs from stdin, filters ERROR/FATAL entries,
# and emits a summary CSV: timestamp,container,severity,message
#
# Usage:
#   docker logs --format json --details mycontainer | ./docker-log-parser.sh
#   docker logs --timestamps mycontainer | ./docker-log-parser.sh
#   cat saved.log | ./docker-log-parser.sh
#
# Handles:
#   - JSON log format  (docker logs --format json [--details])
#   - Plain-text format (docker logs [--timestamps])
#   - Multi-line entries (stack traces, wrapped messages)
#   - Missing / partial metadata (timestamp, container name)
#
set -euo pipefail

# ── constants ────────────────────────────────────────────────────────────────
readonly CSV_HEADER="timestamp,container,severity,message"
readonly TIMESTAMP_RE='^([0-9]{4}-[0-9]{2}-[0-9]{2}[T ])[0-9:.]+(Z|[+-][0-9]{2}:[0-9]{2})?'
readonly CONTAINER_PIPE_RE='^([^|]+)\s*\|\s*(.*)$'
readonly SEVERITY_RE='(ERROR|FATAL|CRITICAL)'
readonly JSON_RE='^\{'
readonly CONTAINER_NAME_RE='(container_name|container)'

# ── helpers ──────────────────────────────────────────────────────────────────

# CSV-escape a single field: wrap in quotes if it contains comma, quote, or newline
csv_escape() {
    local field="$1"
    # Collapse embedded newlines into a space (keeps CSV single-line)
    field="${field//$'\n'/ }"
    # If the field contains a comma, double-quote, or CR, wrap in double quotes
    if [[ "$field" == *,* || "$field" == *\"* || "$field" == *$'\r' ]]; then
        # Escape embedded double-quotes by doubling them
        field="${field//\"/\"\"}"
        printf '"%s"' "$field"
    else
        printf '%s' "$field"
    fi
}

# Trim leading/trailing whitespace
trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"   # leading
    s="${s%"${s##*[![:space:]]}"}"   # trailing
    printf '%s' "$s"
}

# Extract severity keyword from a message string; echoes ERROR / FATAL / CRITICAL / ""
extract_severity() {
    local msg="$1"
    if [[ "$msg" =~ $SEVERITY_RE ]]; then
        printf '%s' "${BASH_REMATCH[1]}"
    fi
}

# ── JSON-mode parser ─────────────────────────────────────────────────────────
#
# Each stdin line is a JSON object produced by `docker logs --format json`.
# Expected keys (subset):
#   log          – the raw log text (may contain \n for multi-line)
#   time         – ISO-8601 timestamp
#   stream       – "stdout" | "stderr"
#   status       – HTTP status (usually "200")
#   container    – container ID (with --details)
#   container_name – human-readable name (with --details)
#
# We avoid requiring `jq` by using sed/grep for simple key extraction,
# but fall back to jq when available for robustness.

parse_json_line() {
    local line="$1"
    local log_ts="" container="" severity="" message=""

    # --- try jq first (most reliable for nested / escaped JSON) ---
    if command -v jq &>/dev/null; then
        log_ts=$(printf '%s' "$line" | jq -r '.time // ""' 2>/dev/null || true)
        container=$(printf '%s' "$line" | jq -r '(.container_name // .container // "")' 2>/dev/null || true)
        message=$(printf '%s' "$line" | jq -r '.log // ""' 2>/dev/null || true)
    else
        # --- fallback: sed extraction (handles most single-level JSON) ---
        log_ts=$(printf '%s' "$line" | sed -n 's/.*"time"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
        container=$(printf '%s' "$line" | sed -n 's/.*"container_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
```
