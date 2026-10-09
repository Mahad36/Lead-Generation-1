from __future__ import annotations

from typing import Any

PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "claude_code",
        "label": "Claude Code (your Claude subscription, no API key)",
        "api_style": "claude_code",
        "api_base": "",
        "models": ["opus", "sonnet"],
        "key_hint": "No API key needed: log in to Claude Code on this machine",
        "docs_url": "https://code.claude.com/docs/en/headless",
    },
    {
        "id": "openai",
        "label": "ChatGPT (OpenAI)",
        "api_style": "openai",
        "api_base": "https://api.openai.com/v1",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-4.1-mini", "o4-mini"],
        "key_hint": "sk-...",
        "docs_url": "https://platform.openai.com/docs/overview",
    },
    {
        "id": "anthropic",
        "label": "Claude (Anthropic)",
        "api_style": "anthropic",
        "api_base": "https://api.anthropic.com",
        "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"],
        "key_hint": "sk-ant-...",
        "docs_url": "https://docs.anthropic.com/en/api/getting-started",
    },
    {
        "id": "gemini",
        "label": "Gemini (Google)",
        "api_style": "openai",
        "api_base": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "models": ["gemini-3.6-flash", "gemini-3-flash-preview", "gemini-2.0-flash"],
        "key_hint": "AIza...",
        "docs_url": "https://ai.google.dev/gemini-api/docs",
    },
    {
        "id": "groq",
        "label": "Groq",
        "api_style": "openai",
        "api_base": "https://api.groq.com/openai/v1",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "openai/gpt-oss-120b"],
        "key_hint": "gsk_...",
        "docs_url": "https://console.groq.com/docs/quickstart",
    },
    {
        "id": "together",
        "label": "Together AI",
        "api_style": "openai",
        "api_base": "https://api.together.xyz/v1",
        "models": ["meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", "Qwen/Qwen2.5-72B-Instruct-Turbo"],
        "key_hint": "tgp_...",
        "docs_url": "https://docs.together.ai/docs/quickstart",
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "api_style": "openai",
        "api_base": "https://openrouter.ai/api/v1",
        "models": ["openai/gpt-4o-mini", "anthropic/claude-sonnet-4", "google/gemini-2.0-flash-001"],
        "key_hint": "sk-or-...",
        "docs_url": "https://openrouter.ai/docs/quick-start",
    },
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "api_style": "openai",
        "api_base": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "key_hint": "sk-...",
        "docs_url": "https://api-docs.deepseek.com/",
    },
    {
        "id": "mistral",
        "label": "Mistral",
        "api_style": "openai",
        "api_base": "https://api.mistral.ai/v1",
        "models": ["mistral-small-latest", "mistral-large-latest"],
        "key_hint": "....",
        "docs_url": "https://docs.mistral.ai/getting-started/quickstart/",
    },
    {
        "id": "xai",
        "label": "Grok (xAI)",
        "api_style": "openai",
        "api_base": "https://api.x.ai/v1",
        "models": ["grok-3-mini", "grok-3"],
        "key_hint": "xai-...",
        "docs_url": "https://docs.x.ai/docs/overview",
    },
    {
        "id": "custom",
        "label": "Custom OpenAI-compatible",
        "api_style": "openai",
        "api_base": "",
        "models": [],
        "key_hint": "API key",
        "docs_url": "https://platform.openai.com/docs/api-reference",
    },
]

_BY_ID = {item["id"]: item for item in PROVIDERS}
_ALIASES = {
    "chatgpt": "openai",
    "gpt": "openai",
    "claude": "anthropic",
    "google": "gemini",
    "openai_compatible": "custom",
}

_MODEL_ALIASES = {
    "gemini-2.5-flash": "gemini-3.6-flash",
    "gemini-2.5-pro": "gemini-3.6-flash",
    "models/gemini-2.5-flash": "gemini-3.6-flash",
    "gemini-1.5-pro": "gemini-2.0-flash",
    "gemini-1.5-flash": "gemini-2.0-flash",
}


def normalize_provider(provider: str) -> str:
    value = (provider or "openai").strip().lower()
    return _ALIASES.get(value, value)


def get_provider_preset(provider: str) -> dict[str, Any] | None:
    return _BY_ID.get(normalize_provider(provider))


def normalize_model(model: str) -> str:
    value = (model or "").strip()
    key = value.removeprefix("models/")
    return _MODEL_ALIASES.get(value) or _MODEL_ALIASES.get(key) or value


def apply_provider_defaults(provider: str, api_base: str, model: str) -> tuple[str, str, str]:
    pid = normalize_provider(provider)
    preset = get_provider_preset(pid) or _BY_ID["custom"]
    resolved = normalize_model(model.strip())
    return (
        pid,
        api_base.strip() or preset["api_base"],
        resolved or (preset["models"][0] if preset["models"] else model),
    )
