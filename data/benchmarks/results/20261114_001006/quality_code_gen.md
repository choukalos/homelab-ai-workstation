# Quality Test: Code Generation
## Prompt
Write a bash script that parses docker container logs...

## Output

```bash
<think>

</think>

```bash
#!/bin/bash

# Parse docker container logs from stdin, identify ERROR and FATAL messages,
# extract timestamp and container name if present, and output a summary CSV.

# Read all lines from stdin
declare -a lines
while IFS= read -r line; do
    lines+=("$line")
done

# Output CSV header
echo "timestamp,container,severity,message"

# Process each line
for line in "${lines[@]}"; do
    # Skip empty lines
    [[ -z "$line" ]] && continue

    # Check if the line contains ERROR or FATAL (case-insensitive for severity detection)
    # We look for ERROR, FATAL, CRITICAL, CRIT, EMERG, ALERT as severe levels
    severity=""
    if echo "$line" | grep -qiE '\b(FATAL|CRITICAL|CRIT|EMERG|ALERT)\b'; then
        severity="FATAL"
    elif echo "$line" | grep -qiE '\bERROR\b'; then
        severity="ERROR"
    fi

    # If no severe level found, skip
    [[ -z "$severity" ]] && continue

    # Extract timestamp: look for ISO 8601 format like 2024-01-15T10:30:45.123Z or 2024-01-15 10:30:45
    timestamp=""
    # Try ISO 8601 with T separator
    if [[ "$line" =~ ([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:?[0-9]{2})?) ]]; then
        timestamp="${BASH_REMATCH[1]}"
    # Try space-separated datetime
    elif [[ "$line" =~ ([0-9]{4}-[0-9]{2}-[0-9]{2}\ [0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?) ]]; then
        timestamp="${BASH_REMATCH[1]}"
    fi

    # Extract container name: look for common patterns
    # Pattern 1: "container_name[pid]: " at the start
    # Pattern 2: "name=container_name"
    # Pattern 3: "container=container_name"
    # Pattern 4: "[container_name]"
    container=""
    if [[ "$line" =~ ^([a-zA-Z0-9_.-]+)\[[0-9]+\]: ]]; then
        container="${BASH_REMATCH[1]}"
    elif [[ "$line" =~ name=([a-zA-Z0-9_.-]+) ]]; then
        container="${BASH_REMATCH[1]}"
    elif [[ "$line" =~ container=([a-zA-Z0-9_.-]+) ]]; then
        container="${BASH_REMATCH[1]}"
    elif [[ "$line" =~ \[([a-zA-Z0-9_.-]+)\] ]]; then
        container="${BASH_REMATCH[1]}"
    fi

    # Extract the message: everything after the severity keyword
    message=""
    if [[ "$line" =~ [[:space:]](FATAL|CRITICAL|CRIT|EMERG|ALERT|ERROR)[[:space:]]+(.*) ]]; then
        message="${BASH_REMATCH[2]}"
    elif [[ "$line" =~ (FATAL|CRITICAL|CRIT|EMERG|ALERT|ERROR)[[:space:]]+(.*) ]]; then
        message="${BASH_REMATCH[2]}"
    fi

    # If no message extracted, use the whole line
    if [[ -z "$message" ]]; then
        message="$line"
    fi

    # Clean up message: remove leading/trailing whitespace
    message="$(echo "$message" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"

    # Escape CSV fields: wrap in quotes and escape internal quotes
    escape_csv() {
        local field="$1"
        # Replace double quotes with two double quotes
        field="${field//\"/\"\"}"
        echo "\"$field\""
    }

    ts_escaped="$(escape_csv "$timestamp")"
    container_escaped="$(escape_csv "$container")"
    severity_escaped="$(escape_csv "$severity")"
    message_escaped="$(escape_csv "$message")"

    echo "$ts_escaped,$container_escaped,$severity_escaped,$message_escaped"
done
```
```
