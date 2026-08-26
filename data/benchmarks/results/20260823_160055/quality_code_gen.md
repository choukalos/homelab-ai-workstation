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
{"log":"2023-10-01T12:00:00.000Z ERROR some message\n","stream":"stderr","time":"2023-10-01T12:00:00.000Z"}
```

**Docker text format:**
```
2023-10-01T12:00:00.000Z ERROR some message
```

**Multi-line logs:**
Docker's JSON format can have multi-line entries where a single log entry spans multiple lines. The `stream` field and `time` field help identify the start of a new entry.

**Container name:**
- In `docker logs` output, there's no container name by default
- In `docker logs --details` or from the log file, it might be present
- In some formats, the container name might be in the log line itself
- We could also accept it as an option or try to extract it from the input

Let me think about the approach:

1. Parse stdin line by line
2. Detect the format (JSON vs text)
3. For JSON format: extract `log`, `stream`, `time` fields
4. For text format: parse the timestamp and message
5. Handle multi-line: if a line doesn't start with a timestamp or JSON object, append to previous entry
6. Filter for ERROR and FATAL
7. Output CSV

Let me think about multi-line handling more carefully:
- In JSON format, each line is a JSON object, so multi-line is handled by the JSON structure itself (the `log` field contains `\n`)
- In text format, a new entry starts with a timestamp pattern. Lines that don't match the timestamp pattern are continuations of the previous entry.

For container name:
- It might be in a field like `"container":"name"` in JSON
- Or it might be passed as an argument
- Or we might not have it (empty field)

Let me write a robust script:

```bash
#!/usr/bin/env bash
```

I'll use a combination of bash and awk for the heavy lifting, since awk is much better at text processing than pure bash.

Actually, let me think about this more carefully. The script needs to:

1. Read from stdin
2. Handle two main formats:
   - JSON lines (one JSON object per line)
   - Plain text with timestamps
3. Handle multi-line entries (continuation lines)
4. Filter ERROR/FATAL
5. Output CSV

Let me use awk for the main parsing logic since it's more efficient and handles the state machine for multi-line better.

For JSON parsing in bash/awk, I'll use grep/sed for simple extraction since we can't assume jq is available (though I could make it optional). Actually, let me use a combination - try jq if available, fall back to sed/grep.

Actually, for a bash script, let me keep it practical. I'll use:
- `sed`/`grep` for JSON field extraction (simple cases)
- `awk` for the main state machine (multi-line handling, filtering)
- Proper CSV escaping

Let me design the awk program:

State machine:
- If line matches JSON format `{"log":...}`:
  - Extract `log`, `time`, `stream` fields
  - Check if log contains ERROR or FATAL
  - If it's a continuation (no timestamp in log), append to previous
- If line matches text format with timestamp:
  - Extract timestamp, rest is message
  - Check for ERROR/FATAL
- If line is a continuation (no timestamp, no JSON):
  - Append to previous entry

Wait, I need to be more careful. Let me re-think.

For JSON format:
```
{"log":"2023-10-01T12:00:00.000Z ERROR something\n","stream":"stderr","time":"2023-10-01T12:00:00.000Z"}
{"log":"  stack trace line 1\n","stream":"stderr","time":"2023-10-01T12:00:00.001Z"}
{"log":"  stack trace line 2\n","stream":"stderr","time":"2023-10-01T12:00:00.002Z"}
```

In this case, the ERROR is in the first line, and the subsequent lines are continuations. We should group them together.

For text format:
```
2023-10-01T12:00:00.000Z ERROR something
  stack trace line 1
  stack trace line 2
2023-10-01T12:00:01.000Z INFO something else
```

Here, lines starting with a timestamp are new entries, others are continuations.

Let me write this with awk as the main processor. I'll make it a bash script that wraps an awk program.

For CSV escaping: fields containing commas, quotes, or newlines need to be quoted, and quotes need to be doubled.

Let me write the full script now:

```bash
#!/usr/bin/env bash
set -euo pipefail

# ... awk program ...
```

Let me think about the awk program structure:

```awk
BEGIN {
    # Initialize
    in_error = 0
    buf = ""
    buf_ts = ""
    buf_sev = ""
    buf_container = ""
}

# Function to check if a line is JSON
function is_json(line) {
    return line ~ /^\{.*\}$/
}

# Function to extract JSON field
function json_get(line, field,    re, val) {
    re = "\"" field "\"[[:space:]]*:[[:space:]]*\"[^\"]*\""
    if (match(line, re)) {
        val = substr(line, RSTART, RLENGTH)
        # Remove field name and colon
        sub("^\"" field "\"[[:space:]]*:[[:space:]]*\"", "", val)
        sub("\"$", "", val)
        return val
    }
    return ""
}
```

Hmm, this is getting complex. Let me think about a cleaner approach.

Actually, for JSON extraction, I can use a simpler regex approach in awk. The JSON fields we care about are:
- `log` - the actual log message
- `time` - the timestamp
- `stream` - stdout/stderr
- Maybe `container` or similar

Let me use a different approach: use `sed` to extract fields from JSON, or better yet, use a single awk pass with careful regex.

Actually, let me reconsider. The most robust approach for a bash script would be:

1. Use `awk` as the main processor
2. In awk, detect format per line
3. Maintain state for multi-line grouping
4. At the end of each group (or when a new group starts), check if it contains ERROR/FATAL
5. Output CSV

Let me write this more carefully:

```awk
# For each line:
# 1. If it's a JSON line, parse it
# 2. If it's a text line with timestamp, parse it
# 3. If it's a continuation line, append to current buffer
# 4. When we detect a new entry, flush the previous one if it was ERROR/FATAL
```

The key insight for multi-line: we buffer the current entry. When we see a new entry start (new timestamp or new JSON object), we flush the previous buffer. At END, we flush the last buffer.

Let me also consider: the container name. In docker logs, the container name isn't typically in the output. But:
- If using `docker logs --details`, there might be metadata
- The user might pipe from a file that has container names
- We could accept it as a command-line argument

I'll support:
- Container name from JSON field if present (e.g., `"container":"web"`)
- Container name as a command-line option `-c name`
- Empty if not available

Let me finalize the script. I'll make it clean and well-commented.

One more consideration: the timestamp format. Docker uses ISO 8601: `2023-10-01T12:00:00.000Z`. In text format, this is at the start of the line. In JSON format, it's in the `time` field or within the `log` field.

For the text format, the pattern would be:
```
^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[
```
