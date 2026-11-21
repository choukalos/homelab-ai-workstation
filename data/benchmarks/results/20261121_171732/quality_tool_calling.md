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
      "id": "chatcmpl-tool-bab7f54d6c510467",
      "type": "function",
      "function": {
        "name": "get_weather",
        "arguments": "{\"city\": \"San Francisco\"}"
      }
    }
  ],
  "reasoning": "The user is asking about the current weather in San Francisco. I should use the get_weather tool. The units parameter is optional, but I can specify celsius or fahrenheit. Since the user hasn't specified, I can either omit it or choose one. The required parameter is only city. I'll call it with just the city, or maybe include units. Since it's optional, I'll just pass city.\n"
}
