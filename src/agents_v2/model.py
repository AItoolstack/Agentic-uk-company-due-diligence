"""
The model client for the agent layer.

OpenAI-compatible chat completions in JSON mode, with token usage captured on every call. Plain JSON
mode rather than native tool calls: the pre-screen's one model call already learned that tool-call parsing
fails on some models (gpt-oss on Groq), and the agent loop is simple enough to run as JSON turns. That also
keeps every agent runnable on either provider.

Unknown model parameters are dropped and retried rather than guessed: newer OpenAI models reject
`temperature` and `max_tokens`, and some providers reject `reasoning_effort`.

Tests pass `scripted`: a function (agent, system, user) -> reply text, so each agent's replies can be
written separately and no network is used.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Callable

import httpx

from src.agents_v2.trace import Trace, price_per_million

ENDPOINTS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
}

Scripted = Callable[[str, str, str], str]


class ModelError(Exception):
    pass


def default_model() -> str:
    return os.environ.get("AGENT_MODEL", "gpt-6-luna")


def provider_key(provider: str) -> str:
    return os.environ.get({"openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY"}.get(provider, ""), "")


class AgentModel:
    def __init__(self, trace: Trace, model: str | None = None, provider: str | None = None,
                 api_key: str | None = None, client: httpx.AsyncClient | None = None,
                 scripted: Scripted | None = None, max_concurrency: int = 3) -> None:
        self.trace = trace
        self.model = model or default_model()
        self.provider = provider or os.environ.get("AGENT_PROVIDER", "openai")
        self.api_key = api_key if api_key is not None else provider_key(self.provider)
        self.client = client
        self.scripted = scripted
        self._gate = asyncio.Semaphore(max_concurrency)
        self._dropped: set[str] = set()  # parameters this model refused; not sent again

    @property
    def available(self) -> bool:
        return self.scripted is not None or bool(self.api_key and self.provider in ENDPOINTS)

    async def json(self, agent: str, system: str, user: str, purpose: str = "",
                   max_output_tokens: int = 1800) -> dict[str, Any]:
        """One JSON-mode call. Retries once on invalid JSON. Raises ModelError when there's no usable answer."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last = ""
        for attempt in range(2):
            text = await self._call(agent, messages, purpose or "call", max_output_tokens)
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as err:
                last = f"invalid JSON ({err.msg})"
            else:
                if isinstance(parsed, dict):
                    return parsed
                last = "the reply wasn't a JSON object"
            messages = messages + [{"role": "assistant", "content": text[:3000]},
                                   {"role": "user", "content": f"That was not usable: {last}. "
                                                               "Reply with one JSON object only."}]
        raise ModelError(last)

    async def _call(self, agent: str, messages: list[dict[str, str]], purpose: str, max_out: int) -> str:
        # Groq runs on its free tier here (guests), so the trace shows $0 rather than another model's list price.
        p_in, p_out = (0.0, 0.0) if self.provider == "groq" else price_per_million(self.model)
        with self.trace.timed("model", purpose, agent, model=self.model) as span:
            if self.scripted is not None:
                text = self.scripted(agent, messages[0]["content"], messages[-1]["content"])
                tin = sum(len(m["content"]) for m in messages) // 4
                tout = len(text) // 4
                span.update(input_tokens=tin, output_tokens=tout, scripted=True,
                            cost_usd=round((tin * p_in + tout * p_out) / 1e6, 6))
                return text
            if not self.available:
                raise ModelError(f"no API key for {self.provider}")
            body = self._body(messages, max_out)
            headers = {"Authorization": f"Bearer {self.api_key}"}
            data: dict[str, Any] | None = None
            async with self._gate:
                for attempt in range(5):
                    try:
                        resp = await self.client.post(ENDPOINTS[self.provider], json=body, headers=headers, timeout=90)
                    except httpx.HTTPError as err:
                        if attempt < 2:
                            await asyncio.sleep(1 + attempt)
                            continue
                        raise ModelError(f"model unavailable ({type(err).__name__})") from err
                    if resp.status_code == 429 and attempt < 3:
                        try:
                            wait = float(resp.headers.get("retry-after", 5))
                        except ValueError:
                            wait = 5.0
                        await asyncio.sleep(min(wait + 1, 20))
                        continue
                    if resp.status_code == 400:
                        dropped = self._drop_rejected(body, resp.text[:600])
                        if dropped:
                            span.setdefault("dropped_params", []).append(dropped)
                            continue
                    if resp.status_code >= 400:
                        raise ModelError(f"model returned HTTP {resp.status_code}: {_error_message(resp)}")
                    try:
                        data = resp.json()
                    except ValueError as err:
                        raise ModelError("model response wasn't JSON") from err
                    break
            if data is None:
                raise ModelError("rate limited by the model provider")
            usage = data.get("usage") or {}
            tin = int(usage.get("prompt_tokens") or 0)
            tout = int(usage.get("completion_tokens") or 0)
            cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
            reasoning = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
            span.update(input_tokens=tin, output_tokens=tout, cached_tokens=cached, reasoning_tokens=reasoning,
                        served_by=data.get("model", ""), cost_usd=round((tin * p_in + tout * p_out) / 1e6, 6))
            choice = (data.get("choices") or [{}])[0]
            span["finish_reason"] = choice.get("finish_reason", "")
            if choice.get("finish_reason") == "length":
                raise ModelError("the answer was cut off at the token limit")
            return (choice.get("message") or {}).get("content") or ""

    def _body(self, messages: list[dict[str, str]], max_out: int) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "messages": messages,
                                "response_format": {"type": "json_object"}}
        if self.provider == "openai":
            body["max_completion_tokens"] = max_out
            effort = os.environ.get("AGENT_REASONING_EFFORT", "low")
            if effort:
                body["reasoning_effort"] = effort
        else:
            body["max_tokens"] = max_out
            body["temperature"] = 0
            if "gpt-oss" in self.model:
                body["reasoning_effort"] = "low"
        for key in self._dropped:
            body.pop(key, None)
        if "max_completion_tokens" in self._dropped:
            body["max_tokens"] = max_out
        return body

    def _drop_rejected(self, body: dict[str, Any], detail: str) -> str:
        for key in ("reasoning_effort", "temperature", "response_format"):
            if key in body and key in detail:
                body.pop(key)
                self._dropped.add(key)
                return key
        if "max_completion_tokens" in body and "max_completion_tokens" in detail:
            body["max_tokens"] = body.pop("max_completion_tokens")
            self._dropped.add("max_completion_tokens")
            return "max_completion_tokens"
        return ""


def _error_message(resp: httpx.Response) -> str:
    try:
        return str((resp.json().get("error") or {}).get("message", ""))[:200]
    except ValueError:
        return resp.text[:200]
