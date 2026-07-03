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

# State variables
current_timestamp=""
current_container=""
current_severity=""
current_message=""
in_multiline=0

# Function to emit a record if we have accumulated data
emit_record() {
    if [[ -n "$current_severity" && -n "$current_message" ]]; then
        # Escape any commas, quotes, or newlines in the message for CSV
        # Replace double quotes with two double quotes
        local escaped_message="${current_message//\"/\"\"}"
        # Wrap message in quotes if it contains commas, quotes, or newlines
        if [[ "$escaped_message" == *","* || "$escaped_message" == *'"'* || "$escaped_message" == *$'\n'* ]]; then
            escaped_message="\"${escaped_message}\""
        fi
        # Escape container name similarly
        local escaped_container="${current_container//\"/\"\"}"
        if [[ "$escaped_container" == *","* || "$escaped_container" == *'"'* || "$escaped_container" == *$'\n'* ]]; then
            escaped_container="\"${escaped_container}\""
        fi
        # Escape timestamp similarly
        local escaped_timestamp="${current_timestamp//\"/\"\"}"
        if [[ "$escaped_timestamp" == *","* || "$escaped_timestamp" == *'"'* || "$escaped_timestamp" == *$'\n'* ]]; then
            escaped_timestamp="\"${escaped_timestamp}\""
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
    # Try to detect if this line starts a new log entry
    # Docker logs often start with a timestamp like 2023-10-01T12:34:56.789Z or similar
    # Or they may have a container name prefix like [container_name] or container_name:
    
    # Check if line matches a new log entry pattern
    # Pattern 1: ISO 8601 timestamp at the start
    # Pattern 2: Line starts with a container name followed by colon or bracket
    
    # Detect if this is a new log entry by checking for timestamp patterns
    # Common docker log timestamp formats:
    # - 2023-10-01T12:34:56.789Z
    # - 2023-10-01 12:34:56
    # - Oct  1 12:34:56
    # - 2023-10-01T12:34:56.789+00:00
    
    is_new_entry=0
    
    # Check for ISO 8601 timestamp at the beginning of the line
    if [[ "$line" =~ ^([0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?([+-][0-9]{2}:[0-9]{2}|Z)?) ]]; then
        is_new_entry=1
        current_timestamp="${BASH_REMATCH[1]}"
    elif [[ "$line" =~ ^([A-Z][a-z]{2}\s+[0-9]{1,2}\s+[0-9]{2}:[0-9]{2}:[0-9]{2}) ]]; then
        # Syslog-style timestamp
        is_new_entry=1
        current_timestamp="${BASH_REMATCH[1]}"
    fi
    
    # If we detected a new entry, emit any previous record and reset
    if [[ $is_new_entry -eq 1 ]]; then
        emit_record
        reset_record
        # current_timestamp is already set above
    fi
    
    # If we're not in a new entry context, this might be a continuation of a multi-line log
    if [[ $is_new_entry -eq 0 ]]; then
        # Check if this line contains ERROR or FATAL
        if [[ "$line" =~ (ERROR|FATAL) ]]; then
            # This could be a new entry that didn't start with a timestamp
            # Try to extract container name and severity
            emit_record
            reset_record
            
            # Try to extract container name from the line
            # Common patterns: [container_name], container_name:, or just embedded
            if [[ "$line" =~ ^\[([^\]]+)\] ]]; then
                current_container="${BASH_REMATCH[1]}"
            elif [[ "$line" =~ ^([^:]+): ]]; then
                current_container="${BASH_REMATCH[1]}"
            fi
            
            # Determine severity
            if [[ "$line" =~ FATAL ]]; then
                current_severity="FATAL"
            elif [[ "$line" =~ ERROR ]]; then
                current_severity="ERROR"
            fi
            
            # Extract message: everything after the severity keyword
            if [[ "$current_severity" == "FATAL" ]]; then
                current_message="${line#*FATAL}"
            else
                current_message="${line#*ERROR}"
            fi
            # Trim leading whitespace and colons from message
            current_message="$(echo "$current_message" | sed 's/^[[:space:]:]*//')"
            in_multiline=1
        else
            # Continuation of a multi-line log entry
            if [[ $in_multiline -eq 1 ]]; then
                # Append to current message with a newline
                current_message="${current_message}
${line}"
            fi
            # If not in multiline, this line is not an ERROR/FATAL, so ignore
        fi
    else
        # This is a new entry starting with a timestamp
        # Try to extract container name
        # Common patterns after timestamp:
        # - [container_name]
        # - container_name:
        # - Just the message
        
        # Remove the timestamp from the line for further parsing
        local_rest="${line#*${current_timestamp}}"
        # Trim leading whitespace
        local_rest="$(echo "$local_rest" | sed 's/^[[:space:]]*//')"
        
        # Try to extract container name
        if [[ "$local_rest" =~ ^\[([^\]]+)\] ]]; then
            current_container="${BASH_REMATCH[1]}"
            local_rest="${local_rest#*]}"
            local_rest="$(echo "$local_rest" | sed 's/^[[:space:]]*//')"
        elif [[ "$local_rest" =~ ^([^:]+): ]]; then
            current_container="${BASH_REMATCH[1]}"
            local_rest="${local_rest#*:}"
            local_rest="$(echo "$local_rest" | sed 's/^[[:space:]]*//')"
        fi
        
        # Check if this line contains ERROR or FATAL
        if [[ "$local_rest" =~ ERROR ]]; then
            current_severity="ERROR"
            current_message="${local_rest#*ERROR}"
            current_message="$(echo "$current_message" | sed 's/^[[:space:]:]*//')"
            in_multiline=1
        elif [[ "$local_rest" =~ FATAL ]]; then
            current_severity="FATAL"
            current_message="${local_rest#*FATAL}"
            current_message="$(echo "$current_message" | sed 's/^[[:space:]:]*//')"
            in_multiline=1
        else
            # Not an ERROR or FATAL, reset
            reset_record
        fi
    fi
    
done

# Emit any remaining record
emit_record
```
```
