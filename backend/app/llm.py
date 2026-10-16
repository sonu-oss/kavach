"""Shared Groq JSON-mode call used by the Smart Shield and the Complaint Assistant."""

import json

import httpx

from app import config


class LLMError(Exception):
    pass


class RateLimited(LLMError):
    pass


async def groq_json(system: str, payload: dict, model: str, max_tokens: int = 1500) -> dict:
    """Send `payload` as JSON data under a fixed system prompt; return the parsed JSON reply."""
    if not config.GROQ_API_KEY:
        raise LLMError("GROQ_API_KEY not set")
    body = {
        "model": model,
        "temperature": 0,
        # Reasoning models spend tokens thinking before the JSON, so leave headroom.
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }
    if config.GROQ_REASONING_EFFORT and "gpt-oss" in model:
        body["reasoning_effort"] = config.GROQ_REASONING_EFFORT
    try:
        async with httpx.AsyncClient(timeout=config.GROQ_TIMEOUT) as client:
            resp = await client.post(
                config.GROQ_URL, json=body, headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"}
            )
    except httpx.HTTPError as exc:
        raise LLMError(f"network error: {exc}") from exc
    if resp.status_code == 429:
        raise RateLimited(resp.text[:200])
    if resp.status_code != 200:
        raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except (KeyError, IndexError, ValueError) as exc:
        raise LLMError(f"invalid JSON from model: {exc}") from exc


async def groq_json_with_fallback(system: str, payload: dict, model: str, max_tokens: int = 1500) -> tuple[dict, str]:
    """One retry; a rate-limited model is swapped for the fallback model (separate quota)."""
    last: Exception | None = None
    for _ in (1, 2):
        try:
            return await groq_json(system, payload, model, max_tokens), model
        except RateLimited as exc:
            last = exc
            if config.GROQ_FALLBACK_MODEL:
                model = config.GROQ_FALLBACK_MODEL
        except LLMError as exc:
            last = exc
    raise LLMError(str(last))
