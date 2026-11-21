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
{"log":"2023-10-15T10:23:45.123456789Z ERROR something went wrong\n","stream":"stderr","time":"2023-10-15T10:23:45.123456789Z"}
```

**Docker plain text format:**
```
2023-10-15T10:23:45.123456789Z ERROR something went wrong
```

**With container name (docker logs --prefix or docker-compose):**
```
container_name  | 2023-10-15T10:23:45.123456789Z ERROR something went wrong
```

**Multi-line logs:**
```
{"log":"2023-10-15T10:23:45.123456789Z ERROR something went wrong\n","stream":"stderr","time":"2023-10-15T10:23:45.123456789Z"}
{"log":"    at line 42 of main.js\n","stream":"stderr","time":"2023-10-15T10:23:45.123456790Z"}
{"log":"    at line 43 of main.js\n","stream":"stderr","time":"2023-10-15T10:23:45.123456791Z"}
```

For multi-line handling, continuation lines (stack traces, etc.) should be appended to the previous ERROR/FATAL entry rather than being treated as separate entries.

Let me design the script:

1. Parse each line
2. For JSON format: extract the "log" field, "stream" field, and "time" field
3. For plain text: parse the timestamp and message
4. For prefixed format: extract container name
5. Detect ERROR/FATAL severity
6. For multi-line: if a line doesn't match the pattern of a new log entry (no timestamp, no JSON structure), append it to the previous entry
7. Output CSV

Let me think about the approach more carefully:

- JSON lines: Each line is a JSON object. The "log" field contains the actual log message (with trailing \n). The "time" field is the timestamp.
- Plain text: Lines like `2023-10-15T10:23:45.123456789Z ERROR message`
- Prefixed: `container_name | 2023-10-15T10:23:45.123456789Z ERROR message`

For multi-line handling:
- In JSON format, a continuation line would be a JSON line where the "log" field doesn't start with a timestamp pattern
- In plain text, a continuation line wouldn't start with a timestamp

Strategy:
- Track state: are we currently in an ERROR/FATAL block?
- If a new line starts with a timestamp (or is a new JSON entry with a timestamp in the log field), it's a new entry
- If it doesn't, it's a continuation of the previous entry
- Only output entries that are ERROR or FATAL

Let me write this as a bash script using awk for the heavy lifting, since bash string manipulation would be painful.

Actually, let me use a combination of bash and awk. The awk approach will be cleaner for parsing.

Let me think about the CSV escaping - fields containing commas, quotes, or newlines need to be properly escaped.

Let me write this:

```bash
#!/usr/bin/env bash
set -euo pipefail
```

I'll use awk as the main parser since it handles text processing well. Let me think about the awk logic:

For each input line:
1. Try to parse as JSON (starts with `{`)
   - Extract "log" field, "time" field
   - The log field might contain the actual message with timestamp
2. Try to parse as prefixed plain text (contains ` | `)
   - Extract container name, rest
3. Try to parse as plain text with timestamp
   - Extract timestamp, rest

For severity detection:
- Look for ERROR or FATAL in the message (case-insensitive)
- In JSON format, the severity might be in the log text itself

For multi-line:
- A line is a "new entry" if it contains a timestamp pattern at the start (after any container prefix)
- A line is a "continuation" if it doesn't
- Continuation lines get appended to the current entry

Let me refine the approach. I'll use awk for the main processing:

```awk
BEGIN {
    # CSV header
    print "timestamp,container,severity,message"
    in_error = 0
    cur_ts = ""
    cur_container = ""
    cur_severity = ""
    cur_message = ""
}

{
    line = $0
    
    # Try JSON format
    if (line ~ /^\{/) {
        # Extract fields from JSON
        # ... parse "log", "time", "stream"
    }
    # Try prefixed format
    else if (line ~ / \| /) {
        # Extract container name
    }
    # Try plain text
    else {
        # Parse timestamp
    }
    
    # Check if this is a new entry or continuation
    # Check severity
    # If ERROR/FATAL, start tracking
    # If continuation and in_error, append
    # If new entry and in_error, flush previous
}

END {
    if (in_error) flush()
}
```

Let me think about JSON parsing in awk. We can use match() with regex to extract fields. For simple JSON (which docker logs produce), this should work.

Actually, for robustness, let me handle the JSON "log" field carefully. The log field in docker JSON logs looks like:
`"log":"2023-10-15T10:23:45.123456789Z ERROR something went wrong\n"`

The \n is a literal backslash-n in the JSON string.

Let me write the full script now. I'll make it comprehensive with good comments.

For the timestamp pattern, I'll use: `[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}`

Let me also consider that the user might pipe `docker logs` output which could be in various formats. The script should handle:
1. JSON log format (default for `docker logs`)
2. Plain text with timestamps
3. Prefixed format (docker-compose or `--prefix`)
4. Multi-line entries (stack traces, etc.)

Let me write the final script now.
</think>

```bash
#!/usr/bin/env bash
#
# docker-log-summary.sh
#
# Parses docker container logs from stdin, identifies ERROR and FATAL messages,
# extracts timestamp and container name, and outputs a CSV summary.
#
# Usage:
#   docker logs <container> | ./docker-log-summary.sh
#   docker-compose logs | ./docker-log-summary.sh
#   cat logs.jsonl | ./docker-log-summary.sh
#
set -euo pipefail

# ---------------------------------------------------------------------------
# CSV field escaper: wraps in quotes if the value contains a comma, quote,
# or newline; doubles embedded quotes.
# ---------------------------------------------------------------------------
csv_escape() {
    local val="$1"
    if [[ "$val" == *[,\"$'\n']* ]]; then
        val="${val//\"/\"\"}"
        printf '"%s"' "$val"
    else
        printf '%s' "$val"
    fi
}

# ---------------------------------------------------------------------------
# Main parser (awk does the heavy lifting for speed and state tracking)
# ---------------------------------------------------------------------------
awk '
BEGIN {
    # Emit CSV header
    print "timestamp,container,severity,message"

    # State for the current (possibly multi-line) entry
    in_entry   = 0
    cur_ts     = ""
    cur_ctr    = ""
    cur_sev    = ""
    cur_msg    = ""

    # Timestamp regex (ISO-8601, optional fractional seconds / timezone)
    ts_re = "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.,][0-9]+)?(Z|[+-][0-9]{2}:?[0
```
