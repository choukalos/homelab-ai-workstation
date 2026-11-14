# Quality Test: Tool Calling
## Prompt
What is the weather in San Francisco right now?

## Output

{
  "role": "assistant",
  "content": "",
  "reasoning_content": "The user wants to know the current weather in San Francisco. I have a tool for getting weather. I'll use it with the city \"San Francisco\" and default units (celsius is fine, but I'll include both options).",
  "tool_calls": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "arguments": "{\"city\":\"San Francisco\",\"units\":\"celsius\"}"
      },
      "id": "NuGtd5rTcdpMdTz5NcfeglR4Ake30rkU"
    }
  ]
}
