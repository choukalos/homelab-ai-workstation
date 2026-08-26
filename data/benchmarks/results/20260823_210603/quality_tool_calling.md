# Quality Test: Tool Calling
## Prompt
What is the weather in San Francisco right now?

## Output

{
  "role": "assistant",
  "content": null,
  "refusal": null,
  "annotations": null,
  "audio": null,
  "function_call": null,
  "tool_calls": [
    {
      "id": "chatcmpl-tool-a1d2dd339b60615b",
      "type": "function",
      "function": {
        "name": "get_weather",
        "arguments": "{\"city\": \"San Francisco\"}"
      }
    }
  ],
  "reasoning": "The user is asking about the current weather in San Francisco. I have a tool called get_weather that can retrieve the current weather for a city. Let's use it.\n\nThe user hasn't specified units, so I'll use the default. The units parameter is optional (only \"city\" is required). I'll just call it with the city name.\n"
}
