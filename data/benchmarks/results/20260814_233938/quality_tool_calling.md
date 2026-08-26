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
      "id": "chatcmpl-tool-b1be661b678df61f",
      "type": "function",
      "function": {
        "name": "get_weather",
        "arguments": "{\"city\": \"San Francisco\"}"
      }
    }
  ],
  "reasoning": "Thinking Process:\n1.  Identify the user's request: Weather in San Francisco.\n2.  Identify the available tool: `get_weather`.\n3.  Check required parameters for `get_weather`: `city` (required), `units` (optional).\n4.  Extract parameters from the prompt: `city` = \"San Francisco\".\n5.  Call the tool `get_weather` with `city`=\"San Francisco\".\n6.  Wait for the result and formulate the response. (I will just generate the tool call now).\u2705\n"
}
