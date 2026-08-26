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
      "id": "chatcmpl-tool-b88e28fb8bc71eb7",
      "type": "function",
      "function": {
        "name": "get_weather",
        "arguments": "{\"city\": \"San Francisco\"}"
      }
    }
  ],
  "reasoning": "The user is asking about the current weather in San Francisco. I should use the get_weather tool. The units parameter is optional, so I can either omit it or specify celsius. Since it's not specified, I'll just call it with the city name.\n"
}
