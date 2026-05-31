"""Shared Claude API wrapper. Defaults to Sonnet 4.6 (current gen; the build
guide's claude-sonnet-4-5 is superseded). Override with CLAUDE_MODEL."""
import json
import os

import anthropic

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

_client: anthropic.Anthropic | None = None


def _get() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def analyze(system_prompt: str, data: str, max_tokens: int = 2048) -> str:
    # System prompt is marked cacheable — saves cost when an agent makes
    # multiple calls in one run (e.g. the monthly executive rollup).
    msg = _get().messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": data}],
    )
    return msg.content[0].text


def analyze_json(system_prompt: str, data: str, max_tokens: int = 4096) -> dict:
    """Like analyze() but parses the response as JSON, tolerating markdown fences."""
    text = analyze(system_prompt, data, max_tokens).strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        return json.loads(text[start : end + 1])
