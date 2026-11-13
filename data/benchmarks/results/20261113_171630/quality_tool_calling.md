# Quality Test: Tool Calling
## Prompt
What is the weather in San Francisco right now?

## Output

{
  "role": "assistant",
  "content": "",
  "reasoning_content": "The user wants the current weather in San Francisco. I have a get_weather tool. I should call it with city \"San Francisco\". Units not specified; I'll use celsius as a default, or fahrenheit? San Francisco is US, so fahrenheit is common. I'll provide celsius as default; the tool accepts both. I'll just call with city and units celsius.",
  "tool_calls": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "arguments": "{\"city\":\"San Francisco\",\"units\":\"celsius\"}"
      },
      "id": "ZrEt7kETX96pqwaMVKlPCZvXSljXdmR3"
    }
  ]
}
