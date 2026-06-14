"""
llm.py
------
Model-agnostic LLM factory with optional two-tier model selection.

Uses LangChain init_chat_model so any supported provider can be selected
via environment variable -- no code changes needed to switch providers.

Provider examples (set in .env):
  CHAT_MODEL_PROVIDER=openai        CHAT_MODEL=gpt-4o
  CHAT_MODEL_PROVIDER=anthropic     CHAT_MODEL=claude-opus-4-8
  CHAT_MODEL_PROVIDER=google_genai  CHAT_MODEL=gemini-2.0-flash
  CHAT_MODEL_PROVIDER=groq          CHAT_MODEL=llama-3.3-70b-versatile
                                    CHAT_MODEL_FAST=llama-3.1-8b-instant

Tiered usage:
  get_llm(tier="quality")  ->  CHAT_MODEL          (default -- analytical agents)
  get_llm(tier="fast")     ->  CHAT_MODEL_FAST      (simple agents; falls back to
                               CHAT_MODEL if CHAT_MODEL_FAST is not set)

Groq model recommendations (June 2026):
  quality:  llama-3.3-70b-versatile   128k ctx, tool calling, best reasoning
  fast:     llama-3.1-8b-instant      fastest/cheapest, tool calling supported
  alt:      llama3-groq-70b-8192-tool-use-preview  (function-call optimised)
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from src.config import settings


@lru_cache(maxsize=16)
def get_llm(
    model: str | None = None,
    provider: str | None = None,
    temperature: float | None = None,
    tier: Literal["fast", "quality"] = "quality",
) -> BaseChatModel:
    """Return a cached, provider-agnostic chat model instance.

    Args:
        model:       Explicit model name override.
        provider:    Explicit provider override (e.g. "groq"). Defaults to settings.
        temperature: Sampling temperature override.
        tier:        "fast" uses CHAT_MODEL_FAST (falls back to CHAT_MODEL);
                     "quality" always uses CHAT_MODEL. Default: "quality".

    Returns:
        A BaseChatModel -- call .with_structured_output(Schema) for typed outputs.

    Example:
        llm = get_llm(tier="fast")
        structured = llm.with_structured_output(MyPydanticSchema)
        result: MyPydanticSchema = structured.invoke(messages)
    """
    # Resolve model: explicit override > tier selection > settings default
    if model:
        effective_model = model
    elif tier == "fast" and settings.chat_model_fast:
        effective_model = settings.chat_model_fast
    else:
        effective_model = settings.chat_model

    effective_provider = provider or settings.chat_model_provider
    effective_temp = temperature if temperature is not None else settings.openai_temperature

    kwargs: dict[str, Any] = {
        "model": effective_model,
        "model_provider": effective_provider,
        "temperature": effective_temp,
    }

    # Pass API key explicitly only when we have it.
    # init_chat_model also picks up standard env vars (OPENAI_API_KEY etc.) automatically.
    if effective_provider == "openai" and settings.openai_api_key:
        kwargs["api_key"] = settings.openai_api_key
    elif effective_provider == "groq" and settings.groq_api_key:
        kwargs["api_key"] = settings.groq_api_key

    return init_chat_model(**kwargs)
