"""Adapt the agent's Responses history to a Chat Completions endpoint."""

from types import SimpleNamespace

from openai import OpenAI


def content_parts(content: str | list[dict]) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    parts = []
    for part in content:
        if part["type"] == "input_text":
            parts.append({"type": "text", "text": part["text"]})
        elif part["type"] == "input_image":
            parts.append({"type": "image_url", "image_url": {
                "url": part["image_url"], "detail": part.get("detail", "auto")}})
        else:
            raise ValueError(f"Unsupported content part: {part['type']}")
    return parts


def chat_messages(history: list) -> list[dict]:
    messages, calls = [], []
    for item in history:
        item = item if isinstance(item, dict) else vars(item)
        if item.get("type") == "function_call":
            calls.append({"id": item["call_id"], "type": "function", "function": {
                "name": item["name"], "arguments": item["arguments"]}})
            continue
        if calls:
            messages.append({"role": "assistant", "tool_calls": calls})
            calls = []
        if item.get("type") == "function_call_output":
            parts = content_parts(item["output"])
            messages.append({"role": "tool", "tool_call_id": item["call_id"],
                             "content": "\n".join(p["text"] for p in parts if p["type"] == "text")})
            images = [p for p in parts if p["type"] == "image_url"]
            if images:
                messages.append({"role": "user", "content": images})
        elif item.get("role") == "assistant":
            messages.append({"role": "assistant", "content": item["content"]})
        else:
            messages.append({"role": item["role"], "content": content_parts(item["content"])})
    if calls:
        messages.append({"role": "assistant", "tool_calls": calls})
    return messages


class ChatCompletions:
    """Use with agent.run(), or select --api chat-completions in the CLI."""

    def __init__(self, client: OpenAI):
        self.client = client
        self.responses = self

    def create(self, *, model, input, tools, max_output_tokens, **kwargs):
        tools = [{"type": "function", "function": {
            key: value for key, value in tool.items() if key != "type"}} for tool in tools]
        response = self.client.chat.completions.create(
            model=model, messages=chat_messages(input), tools=tools,
            parallel_tool_calls=kwargs.get("parallel_tool_calls", False),
            max_tokens=max_output_tokens)
        choice = response.choices[0]
        if choice.finish_reason == "length":
            return SimpleNamespace(status="incomplete", output=[])
        if choice.finish_reason not in ("stop", "tool_calls"):
            raise RuntimeError(f"Chat completion stopped: {choice.finish_reason}")
        output = []
        message = choice.message
        if message.content:
            content = message.content
            if isinstance(content, list):
                content = "\n".join(p["text"] for p in content if p.get("type") == "text")
            if content:
                output.append(SimpleNamespace(type="message", role="assistant", content=content))
        for call in message.tool_calls or []:
            output.append(SimpleNamespace(type="function_call", call_id=call.id,
                                          name=call.function.name, arguments=call.function.arguments))
        return SimpleNamespace(status="completed", output=output)
