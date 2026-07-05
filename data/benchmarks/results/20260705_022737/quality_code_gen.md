# Quality Test: Code Generation
## Prompt
Write a bash script that parses docker container logs...

## Output

```bash
<think>
Here's a thinking process:

1.  **Understand User Requirements:**
   - **Input:** Docker container logs from stdin
   - **Task:** Parse logs, identify ERROR and FATAL messages
   - **Extract:** Timestamp, container name (if present), severity, message
   - **Output:** Summary CSV with columns: `timestamp,container,severity,message`
   - **Special Requirement:** Handle multi-line log formats gracefully
   - **Language:** Bash script

2.  **Analyze Docker Log Format:**
   - Standard Docker logs typically look like:
     `2023-10-25T12:34:56.789Z container_name [ERROR] Some message here`
     or
     `2023-10-25T12:34:56.789Z [ERROR] Some message here`
     or
     `2023-10-25T12:34:56.789Z container_name ERROR Some message here`
     or JSON format: `{"log":"2023-10-25T12:34:56.789Z container_name [ERROR] message\n","stream":"stdout","...}`
   - Multi-line logs: Often start with a timestamp/container on the first line, and subsequent lines are continuations (no timestamp/container prefix). They might be indented or just raw text.
   - Docker's default logging driver outputs lines like:
     `2023-10-25T12:34:56.789Z container_name [ERROR] message`
     or sometimes just:
     `2023-10-25T12:34:56.789Z [ERROR] message`
   - I need to handle variations gracefully. The prompt says "identifies ERROR and FATAL messages", so I'll look for those keywords.
   - Multi-line handling: When a line doesn't start with a timestamp/container pattern, it's a continuation of the previous log entry. I should append it to the current message.

3.  **Design the Parsing Logic:**
   - Read stdin line by line
   - Maintain state: `current_timestamp`, `current_container`, `current_severity`, `current_message`
   - For each line:
     - Check if it matches a new log entry pattern (starts with timestamp, optionally container, then severity)
     - If it matches:
       - If we have a pending entry (from previous lines), output it if severity is ERROR or FATAL
       - Parse the new line to extract timestamp, container, severity, message
       - Update state
     - If it doesn't match (continuation line):
       - Append to `current_message` (with newline or space? CSV usually handles newlines by quoting, but for simplicity, I'll replace newlines with spaces or keep them but properly quote the CSV field)
   - At EOF, output the last pending entry if applicable
   - CSV formatting: Need to properly quote fields containing commas, quotes, or newlines. Standard CSV escaping: wrap in double quotes, escape internal double quotes by doubling them.

4.  **Refine Pattern Matching:**
   - Docker logs typically start with an ISO 8601 timestamp: `^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}`
   - After timestamp, there might be a container name (alphanumeric, hyphens, underscores, dots) or directly a severity tag.
   - Severity: `[ERROR]`, `[FATAL]`, `ERROR`, `FATAL`, or similar. I'll look for these keywords.
   - Let's define a regex for a new log line:
     `^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[^ ]*) *(?:([a-zA-Z0-9._-]+) *)?(?:\[(ERROR|FATAL)\]|(ERROR|FATAL)) *(.*)`
     This is getting complex for bash. Bash's `[[ =~ ]]` supports extended regex but can be tricky.
   - Alternative: Use `awk` or `perl` for more robust parsing. The prompt says "bash script", but it's common to use standard Unix tools within bash. I'll stick to pure bash if possible, but `awk` is much better for this. I'll write it in bash with `awk` for the heavy lifting, or pure bash with careful regex.
   - Actually, pure bash with `read` and regex is fine. Let's try to keep it in bash as requested, but I'll use standard tools if it makes it more robust. The prompt says "Write a bash script", so I'll make it a bash script that can use `awk`/`sed`/`grep` internally.

   Let's design an `awk` solution inside the bash script for robustness, especially for multi-line handling and CSV escaping.

   Wait, the prompt says "bash script", so I'll write it primarily in bash, but I can use `awk` as a helper. Actually, I'll write the core logic in bash to stay true to the request, but I'll make sure it's robust.

   Let's think about bash regex:
   ```bash
   re='^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[^ ]*) *(?:([a-zA-Z0-9._-]+) *)?(?:\[(ERROR|FATAL)\]|(ERROR|FATAL)) *(.*)'
   ```
   Bash regex doesn't support non-capturing groups `(?:...)` or alternation in capture groups well. It's better to use `awk` or `perl`. I'll use `awk` for the parsing logic, wrapped in a bash script. That's standard practice.

   Actually, I can write a pure bash script that handles it line-by-line with `read` and `[[ $line =~ ... ]]`. Let's try to make it work in bash:
   ```bash
   # Match timestamp
   ts_re='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[^ ]*'
   # Match container (optional)
   cont_re='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[^ ]* ([a-zA-Z0-9._-]+) '
   # Match severity
   sev_re='(\[?(ERROR|FATAL)\]?)'
   ```
   This is getting messy. I'll use `awk` for the actual parsing. It's more reliable for multi-line and CSV output. I'll wrap it in a bash script as requested.

   Let's design the `awk` script:
   - State variables: `ts`, `cont`, `sev`, `msg`
   - Function to output CSV if `sev` is ERROR or FATAL
   - Pattern for new line: `/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}/`
   - If matches:
     - If `sev` in ("ERROR", "FATAL"), print previous record
     - Parse new line:
       - `ts = $1`
       - Check if `$2` matches container pattern (alphanumeric, not a severity keyword)
       - If `$2` is not ERROR/FATAL/[ERROR]/[FATAL], then `cont = $2`, `rest = substr($0, length($1) + length($2) + 3)`
       - Else `cont = ""`, `rest = substr($0, length($1) + 2)`
       - Extract severity from `rest`: match `\[?(ERROR|FATAL)\]?`
       - `sev = matched`
       - `msg = rest after severity`
     - Reset `msg` to start with this line's message part
   - If doesn't match (continuation):
     - `msg = msg "\n" $0`
   - END: print last if applicable
   - CSV escaping: replace `"` with `""`, wrap in `"` if contains `,` or `"` or `\n`

   This is doable in `awk`. Let's draft it carefully.

   Actually, Docker logs often come in JSON format when using `docker logs --details` or certain drivers, but the prompt implies standard text logs. I'll assume standard text logs as described.

   Let's refine the `awk` approach:
   ```awk
   BEGIN {
       OFS=","
       print "timestamp,container,severity,message"
       ts=""; cont=""; sev=""; msg=""
   }

   function csv_escape(s) {
       gsub(/"/,
```
