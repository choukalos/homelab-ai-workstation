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

# Output header
echo "timestamp,container,severity,message"

# State variables for multi-line log handling
current_timestamp=""
current_container=""
current_severity=""
current_message=""
in_multiline=0

# Function to emit the current record if we have one
emit_record() {
    if [[ -n "$current_severity" && -n "$current_message" ]]; then
        # Escape any commas and quotes in the message for CSV
        local escaped_message
        escaped_message=$(echo "$current_message" | sed 's/"/""/g')
        # Wrap message in quotes if it contains commas, quotes, or newlines
        if [[ "$escaped_message" == *","* || "$escaped_message" == *'"'* || "$escaped_message" == *$'\n'* ]]; then
            escaped_message="\"$escaped_message\""
        fi
        local escaped_container
        escaped_container=$(echo "$current_container" | sed 's/"/""/g')
        if [[ "$escaped_container" == *","* || "$escaped_container" == *'"'* ]]; then
            escaped_container="\"$escaped_container\""
        fi
        local escaped_timestamp
        escaped_timestamp=$(echo "$current_timestamp" | sed 's/"/""/g')
        if [[ "$escaped_timestamp" == *","* || "$escaped_timestamp" == *'"'* ]]; then
            escaped_timestamp="\"$escaped_timestamp\""
        fi
        echo "${escaped_timestamp},${escaped_container},${current_severity},${escaped_message}"
    fi
}

# Function to reset current record
reset_record() {
    current_timestamp=""
    current_container=""
    current_severity=""
    current_message=""
    in_multiline=0
}

# Process each line from stdin
while IFS= read -r line || [[ -n "$line" ]]; do
    # Try to detect a new log entry with timestamp and container
    # Common docker log formats:
    # 1) 2023-01-01T00:00:00.000Z container_name ERROR message
    # 2) 2023-01-01 00:00:00,000 container_name ERROR message
    # 3) [2023-01-01T00:00:00.000Z] container_name ERROR message
    # 4) 2023-01-01T00:00:00.000Z ERROR message (no container)
    # 5) 2023-01-01 00:00:00 ERROR message (no container)
    
    # Check if this line starts with a timestamp pattern
    # ISO 8601: 2023-01-01T00:00:00.000Z or 2023-01-01T00:00:00.000+00:00
    # Or: 2023-01-01 00:00:00,000
    # Or: [2023-01-01T00:00:00.000Z]
    
    if [[ "$line" =~ ^\[?([0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}([.,][0-9]+)?([Z]|[+-][0-9]{2}:[0-9]{2})?)\]?[[:space:]] ]]; then
        # Emit any previous record
        emit_record
        
        # Extract timestamp
        current_timestamp="${BASH_REMATCH[1]}"
        
        # Remove the timestamp and leading bracket/space from line to parse rest
        local_rest="${line#*${current_timestamp}}"
        # Remove leading bracket if present
        local_rest="${local_rest#\[}"
        # Remove leading whitespace
        local_rest="${local_rest#"${local_rest%%[![:space:]]*}"}"
        
        # Now try to extract container name and severity
        # Pattern: container_name SEVERITY message
        # Container name typically alphanumeric with hyphens/underscores
        if [[ "$local_rest" =~ ^([a-zA-Z0-9_-]+)[[:space:]]+(ERROR|FATAL)[[:space:]]+(.*) ]]; then
            current_container="${BASH_REMATCH[1]}"
            current_severity="${BASH_REMATCH[2]}"
            current_message="${BASH_REMATCH[3]}"
            in_multiline=0
        elif [[ "$local_rest" =~ ^(ERROR|FATAL)[[:space:]]+(.*) ]]; then
            current_container=""
            current_severity="${BASH_REMATCH[1]}"
            current_message="${BASH_REMATCH[2]}"
            in_multiline=0
        else
            # Not an ERROR/FATAL line, but has timestamp - might be start of multi-line
            # Check if it contains ERROR or FATAL later in the line
            if [[ "$local_rest" =~ (ERROR|FATAL) ]]; then
                current_severity="${BASH_REMATCH[1]}"
                current_container=""
                current_message="$local_rest"
                in_multiline=0
            else
                # Not an error/fatal line, reset
                reset_record
            fi
        fi
    elif [[ $in_multiline -eq 1 ]]; then
        # This is a continuation of a multi-line log entry
        # Append to current message
        current_message="${current_message}
${line}"
    else
        # Check if this line contains ERROR or FATAL without a timestamp
        # This might be a continuation or a standalone error line
        if [[ "$line" =~ (ERROR|FATAL) ]]; then
            # If we don't have a current record, start one
            if [[ -z "$current_severity" ]]; then
                emit_record
                current_severity="${BASH_REMATCH[1]}"
                current_message="$line"
                current_timestamp=""
                current_container=""
                in_multiline=1
            else
                # Append to existing
                current_message="${current_message}
${line}"
            fi
        else
            # Not an error line, reset if we were in a multiline
            if [[ $in_multiline -eq 1 ]]; then
                emit_record
                reset_record
            fi
        fi
    fi
done

# Emit the last record if any
emit_record
```
```
