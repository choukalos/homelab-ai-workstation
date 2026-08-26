# Quality Test: Code Generation
## Prompt
Write a bash script that parses docker container logs...

## Output

```bash
<think>
The user wants a bash script that:
1. Reads docker container logs from stdin
2. Identifies ERROR and FATAL messages
3. Extracts timestamp and container name if present
4. Outputs a summary CSV with columns: timestamp, container, severity, message
5. Handles multi-line log formats gracefully

Let me think about Docker log formats:

**Docker JSON log format (default):**
```
{"log":"2023-10-15T10:23:45.123Z ERROR something went wrong\n","stream":"stderr","time":"2023-10-15T10:23:45.123456789Z"}
```

**Docker syslog format:**
```
Oct 15 10:23:45 host docker: [container_name] 2023-10-15T10:23:45.123Z ERROR something went wrong
```

**Docker native format:**
```
2023-10-15T10:23:45.123Z ERROR something went wrong
```

**Multi-line logs:** In JSON format, multi-line messages are split across multiple JSON lines, each with the same "time" field. The continuation lines don't have the timestamp/severity prefix.

**Container name:** In `docker logs` output, the container name isn't typically included unless you're using `docker logs` with specific options or the log format includes it. However, in some formats (like when using `docker --format` or when the log driver includes it), the container name might be present.

Let me think about the approach:

1. Parse JSON lines (the most common format from `docker logs --log-driver json-file`)
2. Handle the "log" field which contains the actual message
3. Extract timestamp from the "time" field or from within the "log" field
4. Extract container name if present (could be in the log line itself)
5. Filter for ERROR and FATAL severity
6. Handle multi-line: continuation lines in JSON format have the same "time" but the "log" field doesn't start with a timestamp pattern
7. Output CSV

Let me design the script:

For JSON format:
- Each line is a JSON object with "log", "stream", "time" fields
- The "log" field contains the actual log message (with trailing \n)
- Multi-line: subsequent lines of a multi-line message will have the same "time" value but the "log" field won't match a timestamp pattern at the start

For the container name:
- In `docker logs` output, the container name isn't directly in the JSON
- But if the log message itself contains a container name pattern, we can extract it
- Or if the user pipes from a format that includes it
- I'll look for patterns like `[container_name]` or `container_name:` in the log line

Let me write a robust script:

```bash
#!/usr/bin/env bash
```

I'll use:
- `jq` for JSON parsing (if available) or fallback to grep/sed
- Actually, let me use a combination. I'll try to detect the format and handle it.

Let me think more carefully:

The script should:
1. Read line by line from stdin
2. Detect if it's JSON format (starts with `{`)
3. If JSON, extract fields using jq or sed
4. If not JSON, try to parse as plain text
5. For each log entry, check if it contains ERROR or FATAL
6. Handle multi-line: if a line doesn't look like a new log entry (no timestamp at start), append to previous entry
7. Output CSV

Let me write this carefully:

```bash
#!/usr/bin/env bash
set -euo pipefail

# ... 
```

Actually, let me think about multi-line handling more carefully. In Docker's JSON log format:
- Line 1: `{"log":"2023-10-15T10:23:45.123Z ERROR first line\n","stream":"stderr","time":"2023-10-15T10:23:45.123456789Z"}`
- Line 2: `{"log":"  second line of stack trace\n","stream":"stderr","time":"2023-10-15T10:23:45.123456789Z"}`
- Line 3: `{"log":"  third line\n","stream":"stderr","time":"2023-10-15T10:23:45.123456789Z"}`

The continuation lines have the same "time" and the "log" field doesn't start with a timestamp.

For plain text format:
- Line 1: `2023-10-15T10:23:45.123Z ERROR first line`
- Line 2: `  second line of stack trace`
- Line 3: `  third line`

Strategy:
- A "new entry" is identified by a line that starts with a timestamp pattern
- Lines that don't start with a timestamp are continuations of the previous entry
- We accumulate continuation lines with the previous entry
- When we encounter a new entry or EOF, we process the accumulated entry

For CSV output, I need to handle:
- Quoting fields that contain commas, quotes, or newlines
- Multi-line messages should be quoted in CSV

Let me write the script now. I'll use `jq` if available for JSON parsing, with a fallback.

Actually, to keep it more portable and self-contained, let me use a hybrid approach:
- Use `jq` if available for JSON
- Use sed/grep for plain text
- Handle multi-line with a state machine in bash

Let me write a clean, well-commented script.

I'll structure it as:
1. Configuration/variables
2. Helper functions (CSV quoting, timestamp detection)
3. Main loop reading stdin
4. Multi-line accumulation logic
5. Output

Let me refine the approach for detecting "new entry" vs "continuation":
- JSON: if the "log" field (after stripping) matches a timestamp pattern at the start → new entry
- Plain text: if the line matches a timestamp pattern at the start → new entry
- Otherwise → continuation

Timestamp patterns to match:
- ISO 8601: `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}`
- Syslog: `^[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}`
- Docker native: `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}`

Container name extraction:
- Look for patterns like `[name]` or `name:` at the beginning
- In some docker log formats, the container name appears as a prefix
- I'll look for: `^\[([^\]]+)\]` or `^(\S+):` patterns

Let me write the final script now.
</think>

```bash
#!/usr/bin/env bash
#
# docker-log-summary.sh
#
# Parses Docker container logs from stdin, filters ERROR/FATAL entries,
# and emits a CSV summary: timestamp,container,severity,message
#
# Supported input formats:
#   • Docker JSON log-driver (one JSON object per line)
#   • Docker native / plain-text (timestamp-prefixed lines)
#   • Syslog-style lines
#
# Multi-line messages (stack traces, wrapped text) are folded into the
# parent entry so the CSV stays one-row-per-logical-log.
#
# Usage:
#   docker logs <container> | ./docker-log-summary.sh
#   ./docker-log-summary.sh < logs.txt
#
set -euo pipefail

# ─── Configuration ────────────────────────────────────────────────────────────
SEVERITIES_RE='ERROR|FATAL|CRITICAL|PANIC'   # case-insensitive
CSV_HEADER='timestamp,container,severity,message'

# ─── Helpers ──────────────────────────────────────────────────────────────────

# Quote a field for safe CSV embedding (RFC 4180).
csv_quote() {
    local val="$1"
    # If the value contains a comma, double-quote, or newline → wrap in quotes
    if [[ "$val" == *,* || "$val" == *\"* || "$val" == *$'\n'* ]]; then
        val="${val//\"/\"\"}"          # escape embedded quotes
        printf '"%s"' "$val"
    else
        printf '%s' "$val"
    fi
}

# Return 0 if the string looks like the start of a new log entry.
# Matches ISO-8601, syslog, and common "level-prefixed" patterns.
is_new_entry() {
    local line="$1"
    # ISO
```
