import anthropic


_client: anthropic.AsyncAnthropic | None = None


def get_client(api_key: str) -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=api_key)
    return _client


async def call_sonnet(api_key: str, prompt: str, system: str = "", max_tokens: int = 4096) -> str:
    client = get_client(api_key)
    messages = [{"role": "user", "content": prompt}]
    kwargs: dict = {"model": "claude-sonnet-4-6-20250514", "max_tokens": max_tokens, "messages": messages}
    if system:
        kwargs["system"] = system
    resp = await client.messages.create(**kwargs)
    return resp.content[0].text


async def call_opus_with_search(
    api_key: str,
    prompt: str,
    system: str = "",
    max_tokens: int = 16000,
    thinking_budget: int = 10000,
) -> str:
    client = get_client(api_key)
    messages = [{"role": "user", "content": prompt}]
    kwargs: dict = {
        "model": "claude-opus-4-6-20250415",
        "max_tokens": max_tokens,
        "messages": messages,
        "thinking": {"type": "enabled", "budget_tokens": thinking_budget},
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
    }
    if system:
        kwargs["system"] = system
    resp = await client.messages.create(**kwargs)
    text_parts = [block.text for block in resp.content if block.type == "text"]
    return "\n".join(text_parts)
