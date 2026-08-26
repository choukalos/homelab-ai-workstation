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
{"log":"2023-10-15T10:30:45.123Z ERROR: Something went wrong\n","stream":"stderr","time":"2023-10-15T10:30:45.123456789Z"}
```

**Docker native format:**
```
2023-10-15T10:30:45.123Z ERROR: Something went wrong
```

**Docker syslog format:**
```
Oct 15 10:30:45 host docker: [container_name] 2023-10-15T10:30:45.123Z ERROR: Something went wrong
```

**Multi-line logs:**
Docker JSON format can have multi-line entries where a single log entry spans multiple lines. The JSON format handles this by having `\n` within the "log" field. But if we're reading from `docker logs` command, each line is a separate JSON object.

However, multi-line logs can also appear when:
- A stack trace spans multiple lines
- The log message itself contains newlines (in JSON format, these are escaped as `\n`)
- In native format, continuation lines might not have a timestamp

Let me think about the approach:

1. Read from stdin line by line
2. Try to parse JSON format first (using `jq` or `sed`/`grep`)
3. Fall back to native/syslog format
4. For multi-line: if a line doesn't match the expected pattern (no timestamp), append it to the previous message
5. Filter for ERROR and FATAL
6. Output CSV

Let me think about the multi-line handling more carefully:
- In JSON format, each line is a complete JSON object, so multi-line is handled by the `\n` in the log field
- In native format, a line like `2023-10-15T10:30:45.123Z ERROR: First line` followed by `    at some.stack.trace` - the second line is a continuation
- We need to detect if a line is a "new log entry" or a "continuation"

A new log entry typically starts with:
- A JSON object `{`
- A timestamp pattern like `2023-10-15T10:30:45` or `Oct 15 10:30:45`
- Or a syslog-like prefix

A continuation line:
- Doesn't start with a timestamp
- Doesn't start with `{`
- Might be indented

Let me write the script:

```bash
#!/usr/bin/env bash
```

I'll use a combination of `sed`, `awk`, and `grep` for parsing. Let me think about whether to use `jq` - it's cleaner for JSON but might not be available. I'll provide a solution that works with standard tools but also handles JSON.

Actually, let me use `awk` as the main workhorse since it's great for line-by-line processing and state management (for multi-line handling).

Let me structure the script:

1. A function to detect and parse JSON log lines
2. A function to detect and parse native/syslog log lines
3. Multi-line accumulation logic
4. CSV output with proper quoting

For CSV, I need to handle:
- Commas in messages → quote the field
- Quotes in messages → escape by doubling
- Newlines in messages → quote the field (but in CSV, embedded newlines within quotes are valid)

Let me write this carefully:

```bash
#!/usr/bin/env bash
set -euo pipefail

# CSV escape function
csv_escape() {
    local field="$1"
    # If field contains comma, quote, or newline, wrap in quotes
    if [[ "$field" == *[,\"$'\n']* ]]; then
        field="${field//\"/\"\"}"
        field="\"${field}\""
    fi
    printf '%s' "$field"
}
```

Actually, for a bash script that processes potentially large logs, using `awk` for the heavy lifting would be more performant. But the user asked for a bash script, so let me use bash with some awk/sed helpers.

Let me reconsider the approach. I'll use a main loop in bash with `read`, and use `sed`/`grep` for pattern matching. For multi-line, I'll accumulate lines.

Let me think about the timestamp patterns:
- ISO 8601: `2023-10-15T10:30:45.123Z` or `2023-10-15T10:30:45+00:00`
- Syslog: `Oct 15 10:30:45`
- Docker native: `2023-10-15T10:30:45.123456789Z`

Container name patterns:
- In JSON: might be in a "container" field or in the message
- In syslog: `docker: [container_name]`
- In native: might be prefixed

Let me write a comprehensive script:

```bash
#!/usr/bin/env bash
set -euo pipefail

# ... main processing
```

I think the cleanest approach is:
1. Read line by line
2. Determine if it's a new entry or continuation
3. Parse the new entry to extract fields
4. Accumulate continuation lines
5. When we get a new entry or EOF, process the accumulated entry
6. If severity is ERROR or FATAL, output CSV row

Let me write this out properly now.
</think>

```bash
#!/usr/bin/env bash
#
# docker-log-summary.sh
#
# Parses Docker container logs from stdin, filters ERROR/FATAL entries,
# and emits a CSV summary: timestamp,container,severity,message
#
# Handles:
#   • Docker JSON log format (one JSON object per line)
#   • Docker native / syslog formats
#   • Multi-line log entries (stack traces, wrapped messages)
#
# Usage:
#   docker logs --timestamps mycontainer | ./docker-log-summary.sh
#   docker logs mycontainer | ./docker-log-summary.sh
#   ./docker-log-summary.sh < saved-logs.txt
#

set -euo pipefail

# ─── CSV field escaping ───────────────────────────────────────────────────────
csv_escape() {
    local f="$1"
    # Replace embedded newlines with a literal \n (keeps CSV single-line per row)
    f="${f//$'\n'/\\n}"
    # If the field contains a comma, double-quote, or leading/trailing space, quote it
    if [[ "$f" == *,* || "$f" == *\"* || "$f" == " "* || "$f" == *" " ]]; then
        f="${f//\"/\"\"}"
        printf '"%s"' "$f"
    else
        printf '%s' "$f"
    fi
}

# ─── State for the current (possibly multi-line) entry ───────────────────────
cur_timestamp=""
cur_container=""
cur_severity=""
cur_message=""
has_entry=0

# ─── Flush the accumulated entry to CSV if it qualifies ──────────────────────
flush_entry() {
    [[ $has_entry -eq 0 ]] && return 0

    # Only emit ERROR / FATAL
    if [[ "$cur_severity" == "ERROR" || "$cur_severity" == "FATAL" ]]; then
        printf '%s,%s,%s,%s\n' \
            "$(csv_escape "$cur_timestamp")" \
            "$(csv_escape "$cur_container")" \
            "$(csv_escape "$cur_severity")" \
            "$(csv_escape "$cur_message")"
    fi

    # Reset
    cur_timestamp=""
    cur_container=""
    cur_severity=""
    cur_message=""
    has_entry=0
}

# ─── Try to parse a JSON-format log line ─────────────────────────────────────
# Expected shape (one object per line):
#   {"log":"2023-10-15T10:30:45.123Z ERROR: msg\n","stream":"stderr","time":"..."}
#   {"log":"msg\n","stream":"stdout","time":"2023-10-15T10:30:45.123456789Z"}
#
# Returns 0 on success, 1 if the line is not JSON.
parse_json_line() {
    local line="$1"

    # Quick reject: must start
```
