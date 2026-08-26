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
```json
{"log":"2023-10-01T12:00:00.000Z ERROR [container-name] Something went wrong\n","stream":"stderr","time":"2023-10-01T12:00:00.123456789Z"}
```

**Docker native/syslog format:**
```
time="2023-10-01T12:00:00.123456789Z" level=error msg="Something went wrong"
```

**Plain text format (docker logs output):**
```
2023-10-01T12:00:00.000Z ERROR [my-container] Something went wrong
```

**Multi-line logs:**
Docker JSON format can have multi-line messages where a single logical log entry spans multiple JSON lines (each line is a separate JSON object but they belong to the same message). Actually, in Docker's JSON log format, each line IS a separate log entry. But the `log` field within a JSON object can contain newlines (escaped as `\n`).

Actually, let me reconsider. Multi-line in Docker context:
- In JSON format: each line is a JSON object, but the `log` field can contain `\n` (escaped newlines)
- In plain text: a stack trace or multi-line message might span multiple lines
- The `docker logs` command outputs in the format the container's logging driver uses

Let me think about what "multi-line log formats" means:
1. JSON log format where `log` field contains escaped newlines
2. Plain text where a message spans multiple lines (e.g., stack traces)
3. The `docker logs` output which might have the format: `container-name: message`

Let me design the script to handle:
1. **JSON format**: `{"log":"...","stream":"stderr","time":"..."}` - parse with jq or grep/sed
2. **Native format**: `time="..." level=error msg="..."` 
3. **Plain text**: Lines with timestamps, severity keywords
4. **Multi-line**: Continuation lines that don't start with a timestamp or known prefix should be appended to the previous message

For the CSV output, I need to:
- Escape commas in messages (wrap in quotes)
- Handle newlines in messages (replace with spaces or escape)
- Extract container name if present (from JSON `name` field, or from text patterns)

Let me write a robust bash script:

```bash
#!/usr/bin/env bash
```

I'll use a combination of awk and bash for parsing. Let me think about the approach:

1. Read line by line from stdin
2. Detect the format of each line
3. For JSON: extract log, time, and name fields
4. For native: extract time, level, msg
5. For plain text: try to parse timestamp, severity, container, message
6. Handle multi-line: if a line doesn't match any known start pattern, append to previous message
7. Filter for ERROR and FATAL
8. Output CSV

Let me write this carefully. I'll use awk for the main processing since it's more efficient for line-by-line processing, but I'll wrap it in a bash script.

Actually, let me use a hybrid approach - bash for the outer structure and awk for the heavy lifting, or just do it all in awk within the bash script.

Let me think about the multi-line handling more carefully:
- A new log entry starts with a recognizable pattern (timestamp, JSON `{`, `time="`, etc.)
- A continuation line is anything that doesn't match those patterns
- Continuation lines should be appended to the previous entry's message

For the CSV:
- Fields containing commas, quotes, or newlines should be quoted
- Newlines within messages should be replaced with literal `\n` or spaces

Let me write this now. I'll make it comprehensive and well-commented.

I'll structure it as:
1. Shebang and set options
2. Main processing in awk (for performance)
3. The awk script handles all format detection and multi-line logic
4. Output CSV header and rows

Let me refine the awk logic:

For JSON detection: line starts with `{` and contains `"log"`
For native detection: line matches `time="` and `level=`
For plain text: line contains a timestamp pattern and ERROR/FATAL keyword

Multi-line: if current line doesn't match any "start" pattern, it's a continuation.

Let me write this out properly.
</think>

```bash
#!/usr/bin/env bash
#
# docker-log-summary.sh
# Parses docker container logs from stdin, filters ERROR/FATAL entries,
# and emits a CSV summary: timestamp,container,severity,message
#
# Handles:
#   • Docker JSON log format  ({"log":"...","stream":"stderr","time":"..."})
#   • Docker native format    (time="..." level=error msg="...")
#   • Plain-text / syslog-ish (2023-10-01T12:00:00Z ERROR [svc] message)
#   • Multi-line messages (stack traces, wrapped lines)
#
# Usage:
#   docker logs --log-driver json my-container | ./docker-log-summary.sh
#   docker logs my-container | ./docker-log-summary.sh
#   cat logs.txt | ./docker-log-summary.sh

set -euo pipefail

# ─── CSV field escaping ───────────────────────────────────────────────────────
# Wraps a value in double-quotes if it contains a comma, quote, or newline.
# Internal quotes are doubled per RFC 4180.
csv_escape() {
    local val="$1"
    if [[ "$val" == *[,\"$'\n']* ]]; then
        val="${val//\"/\"\"}"
        printf '"%s"' "$val"
    else
        printf '%s' "$val"
    fi
}

# ─── Main parser (awk) ───────────────────────────────────────────────────────
# We do the heavy lifting in a single awk pass for speed.
# The awk program:
#   1. Detects the log format per line.
#   2. Accumulates multi-line messages.
#   3. Filters for ERROR / FATAL severity.
#   4. Emits CSV rows (header printed by bash before/after).

awk '
BEGIN {
    OFS = ","
    # Print CSV header
    print "timestamp,container,severity,message"

    # State for the current (possibly multi-line) entry
    cur_ts      = ""
    cur_cont    = ""
    cur_sev     = ""
    cur_msg     = ""
    has_entry   = 0
}

# ── Helper: flush the current entry if it is ERROR or FATAL ─────────────────
function flush_entry() {
    if (has_entry && (cur_sev == "ERROR" || cur_sev == "FATAL")) {
        # Collapse embedded newlines to literal \n for CSV safety
        gsub(/\n/, "\\n", cur_msg)
        # Trim leading/trailing whitespace
        gsub(/^[ \t]+|[ \t]+$/, "", cur_msg)
        printf "%s,%s,%s,%s\n",
            csv_field(cur_ts),
            csv_field(cur_cont),
            csv_field(cur_sev),
            csv_field(cur_msg)
    }
    has_entry = 0
    cur_ts = ""; cur_cont = ""; cur_sev = ""; cur_msg = ""
}

# ── Helper: RFC-4180 CSV field quoting ──────────────────────────────────────
function csv_field(val) {
    if (val ~ /[",\n]/) {
        gsub(/"/, "\"\"", val)
        return "\"" val "\""
    }
    return val
}

# ── Detect: Docker JSON log format ──────────────────────────────────────────
# Example:
# {"log":"2023-10-01T12:00:00Z ERROR [api] boom\n","stream":"stderr","time":"2023-10-01T12:00:00.123Z"}
# {"log":"  at com.example.Foo(Foo.java:42)\n","stream":"stderr","time":"..."}
#
# The "name" field may appear in some drivers:
# {"log":"...","stream":"stderr","time":"...","name":"my-container"}
/^{"log":/ {
    # A new JSON line starts a new logical entry OR is a continuation.
    # Heuristic: if the "log" value starts with a timestamp or a known
    # severity keyword, it is a new
```
