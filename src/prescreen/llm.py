"""
The one place the model is used: reading search results about the company.

Calls an OpenAI-compatible chat endpoint (Groq by default) in JSON mode and
validates the answer. On the live baseline, structured output via tool calls
failed on gpt-oss ("Failed to parse tool call arguments"), so this uses plain
JSON mode, a schema check, one repair retry, and waits out 429s.

If the model can't give a valid answer, the media check becomes
"couldn't check". It never becomes "clear".
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

ENDPOINTS = {
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
}


class LLMError(Exception):
    pass


class JSONChat:
    def __init__(self, client: httpx.AsyncClient | None, provider: str, model: str, api_key: str,
                 scripted: list[str] | None = None) -> None:
        self.client = client
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.scripted = scripted  # tests: canned responses, consumed in order
        self.calls = 0

    @property
    def available(self) -> bool:
        return self.scripted is not None or bool(self.api_key and self.provider in ENDPOINTS)

    async def complete_json(self, system: str, user: str, max_tokens: int = 1800) -> dict[str, Any]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error = ""
        for attempt in range(2):
            text = await self._call(messages, max_tokens)
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
                last_error = "top-level JSON was not an object"
            except json.JSONDecodeError as exc:
                last_error = f"invalid JSON ({exc.msg})"
            messages += [
                {"role": "assistant", "content": text[:2000]},
                {"role": "user", "content": f"That was not valid: {last_error}. Reply with the JSON object only."},
            ]
        raise LLMError(last_error)

    async def _call(self, messages: list[dict[str, str]], max_tokens: int) -> str:
        self.calls += 1
        if self.scripted is not None:
            if not self.scripted:
                raise LLMError("no scripted response left")
            return self.scripted.pop(0)
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        for attempt in range(3):
            try:
                resp = await self.client.post(ENDPOINTS[self.provider], json=body, headers=headers, timeout=60)
            except httpx.HTTPError as exc:
                if attempt < 2:
                    await asyncio.sleep(1 + attempt)
                    continue
                raise LLMError(f"model unavailable ({type(exc).__name__})") from exc
            if resp.status_code == 429 and attempt < 2:
                try:
                    wait = float(resp.headers.get("retry-after", 8))
                except ValueError:
                    wait = 8.0
                await asyncio.sleep(min(wait + 1, 30))
                continue
            if resp.status_code >= 400:
                raise LLMError(f"model returned HTTP {resp.status_code}")
            data = resp.json()
            return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        raise LLMError("rate limited by the model provider")
