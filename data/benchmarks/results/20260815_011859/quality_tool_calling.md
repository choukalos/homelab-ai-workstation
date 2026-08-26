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
      "id": "chatcmpl-tool-903c3ffc1d154b57",
      "type": "function",
      "function": {
        "name": "get_weather",
        "arguments": "{\"city\": \"San Francisco\"}"
      }
    }
  ],
  "reasoning": "The user wants the current weather in San Francisco. I have a get_weather tool that takes a city name and optional units. I'll call it with city \"San Francisco\". Units are optional, so I'll leave it default or just provide the city.\n"
}
