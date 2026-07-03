# Quality Test: Tool Calling
## Prompt
What is the weather in San Francisco right now?

## Output

{
  "role": "assistant",
  "content": "The user is asking for the current weather in San Francisco.\nI have a tool `get_weather` that can provide this information.\nThe tool requires a `city` parameter and an optional `units` parameter.\nThe city is \"San Francisco\".\nI will call the `get_weather` tool with the city \"San Francisco\". I won't specify units as it's optional and the user didn't specify a preference.\n</think>\n\n",
  "refusal": null,
  "annotations": null,
  "audio": null,
  "function_call": null,
  "tool_calls": [
    {
      "id": "chatcmpl-tool-aa246cdfaf129bdb",
      "type": "function",
      "function": {
        "name": "get_weather",
        "arguments": "{\"city\": \"San Francisco\"}"
      }
    }
  ],
  "reasoning": null
}
