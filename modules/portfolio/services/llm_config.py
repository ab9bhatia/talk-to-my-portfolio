"""Portfolio agent LLM provider configuration (.env + setup UI)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from modules.portfolio.services.env_store import env_file_path

from modules.portfolio.services.env_store import env_var_present, read_env_value, upsert_env_vars

PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_GEMINI = "gemini"
PROVIDER_OLLAMA = "ollama"

SUPPORTED_PROVIDERS = (PROVIDER_OPENAI, PROVIDER_ANTHROPIC, PROVIDER_GEMINI, PROVIDER_OLLAMA)

def _model_options(
    items: list[tuple[str, str, bool]],
) -> list[dict[str, Any]]:
    """Build model dropdown entries: (value, label, recommended)."""
    return [
        {"value": value, "label": label, "recommended": recommended}
        for value, label, recommended in items
    ]


LLM_PROVIDER_CATALOG: list[dict[str, Any]] = [
    {
        "id": PROVIDER_OPENAI,
        "label": "OpenAI",
        "description": "GPT models via api.openai.com — recommended for JSON agent replies.",
        "external_url": "https://platform.openai.com/api-keys",
        "default_model": "gpt-4o-mini",
        "models": _model_options(
            [
                ("gpt-4o-mini", "GPT-4o mini (fast, recommended)", True),
                ("gpt-4o", "GPT-4o", False),
                ("gpt-4.1-mini", "GPT-4.1 mini", False),
                ("gpt-4.1", "GPT-4.1", False),
                ("o1-mini", "o1 mini (reasoning)", False),
            ]
        ),
        "fields": [
            {
                "name": "api_key",
                "label": "API key",
                "type": "secret",
                "required": True,
                "env": "PORTFOLIO_OPENAI_API_KEY",
            },
            {
                "name": "model",
                "label": "Model",
                "type": "model_select",
                "required": True,
                "env": "PORTFOLIO_LLM_MODEL",
            },
        ],
    },
    {
        "id": PROVIDER_ANTHROPIC,
        "label": "Claude (Anthropic)",
        "description": "Claude models via Anthropic API.",
        "external_url": "https://console.anthropic.com/settings/keys",
        "default_model": "claude-sonnet-4-20250514",
        "models": _model_options(
            [
                ("claude-sonnet-4-20250514", "Claude Sonnet 4 (recommended)", True),
                ("claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet", False),
                ("claude-3-5-haiku-20241022", "Claude 3.5 Haiku (faster)", False),
            ]
        ),
        "fields": [
            {
                "name": "api_key",
                "label": "API key",
                "type": "secret",
                "required": True,
                "env": "PORTFOLIO_ANTHROPIC_API_KEY",
            },
            {
                "name": "model",
                "label": "Model",
                "type": "model_select",
                "required": True,
                "env": "PORTFOLIO_LLM_MODEL",
            },
        ],
    },
    {
        "id": PROVIDER_GEMINI,
        "label": "Google Gemini",
        "description": "Gemini models via Google AI Studio.",
        "external_url": "https://aistudio.google.com/apikey",
        "default_model": "gemini-2.0-flash",
        "models": _model_options(
            [
                ("gemini-2.0-flash", "Gemini 2.0 Flash (recommended)", True),
                ("gemini-2.0-flash-lite", "Gemini 2.0 Flash Lite", False),
                ("gemini-1.5-pro", "Gemini 1.5 Pro", False),
                ("gemini-1.5-flash", "Gemini 1.5 Flash", False),
            ]
        ),
        "fields": [
            {
                "name": "api_key",
                "label": "API key",
                "type": "secret",
                "required": True,
                "env": "PORTFOLIO_GEMINI_API_KEY",
            },
            {
                "name": "model",
                "label": "Model",
                "type": "model_select",
                "required": True,
                "env": "PORTFOLIO_LLM_MODEL",
            },
        ],
    },
    {
        "id": PROVIDER_OLLAMA,
        "label": "Ollama (local)",
        "description": "Run models on your machine — no cloud API key. Install Ollama and pull a model first.",
        "external_url": "https://ollama.com/download",
        "default_model": "llama3.2",
        "models": _model_options(
            [
                ("llama3.2", "Llama 3.2 (recommended)", True),
                ("llama3.1", "Llama 3.1", False),
                ("mistral", "Mistral", False),
                ("qwen2.5", "Qwen 2.5", False),
                ("gemma2", "Gemma 2", False),
                ("phi3", "Phi-3", False),
            ]
        ),
        "models_dynamic": True,
        "fields": [
            {
                "name": "base_url",
                "label": "Ollama URL",
                "type": "url",
                "required": True,
                "env": "PORTFOLIO_OLLAMA_BASE_URL",
                "placeholder": "http://localhost:11434",
            },
            {
                "name": "model",
                "label": "Model",
                "type": "model_select",
                "required": True,
                "env": "PORTFOLIO_LLM_MODEL",
            },
        ],
    },
]


def _env_first(*names: str) -> str:
    for name in names:
        val = (os.getenv(name) or read_env_value(name) or "").strip()
        if val:
            return val.strip('"').strip("'")
    return ""


def provider_catalog() -> list[dict[str, Any]]:
    return [dict(p) for p in LLM_PROVIDER_CATALOG]


def validate_ollama_base_url(base_url: str) -> str:
    """Allow loopback / private LAN only — blocks SSRF to cloud metadata and the public internet."""
    from urllib.parse import urlparse

    import ipaddress

    raw = (base_url or "http://localhost:11434").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Ollama URL must start with http:// or https://")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("Ollama URL must include a host")
    if host in ("localhost", "127.0.0.1", "::1"):
        return raw.rstrip("/")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("Ollama URL must be localhost or a private IP address") from exc
    if ip.is_loopback:
        return raw.rstrip("/")
    if ip.is_link_local or ip.is_reserved:
        raise ValueError("Ollama URL cannot use link-local or reserved addresses")
    if ip.is_private:
        return raw.rstrip("/")
    raise ValueError("Ollama URL must be localhost or a private network address (not public internet)")


def fetch_ollama_model_names(base_url: str) -> list[dict[str, Any]]:
    """List models from a running Ollama instance (falls back to catalog defaults)."""
    entry = _catalog_entry(PROVIDER_OLLAMA)
    fallback = list(entry.get("models") or [])
    try:
        url = validate_ollama_base_url(base_url)
    except ValueError:
        return fallback
    try:
        req = urllib.request.Request(f"{url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
        return fallback

    names = sorted(
        {
            (m.get("name") or "").strip()
            for m in data.get("models") or []
            if (m.get("name") or "").strip()
        }
    )
    if not names:
        return fallback
    return [{"value": name, "label": name, "recommended": False} for name in names]


def _catalog_entry(provider_id: str) -> dict[str, Any]:
    for item in LLM_PROVIDER_CATALOG:
        if item["id"] == provider_id:
            return item
    raise ValueError(f"Unknown LLM provider: {provider_id}")


def active_provider() -> str | None:
    explicit = _env_first("PORTFOLIO_LLM_PROVIDER", "LLM_PROVIDER").lower()
    if explicit in SUPPORTED_PROVIDERS:
        return explicit
    if _env_first("PORTFOLIO_OPENAI_API_KEY", "OPENAI_API_KEY", "API_KEY"):
        return PROVIDER_OPENAI
    if _env_first("PORTFOLIO_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"):
        return PROVIDER_ANTHROPIC
    if _env_first("PORTFOLIO_GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        return PROVIDER_GEMINI
    if _env_first("PORTFOLIO_OLLAMA_BASE_URL", "OLLAMA_HOST"):
        return PROVIDER_OLLAMA
    return None


def model_name() -> str:
    provider = active_provider()
    custom = _env_first("PORTFOLIO_LLM_MODEL", "LLM_MODEL")
    if custom:
        return custom
    if provider:
        return str(_catalog_entry(provider).get("default_model") or "gpt-4o-mini")
    return "gpt-4o-mini"


def api_key_for_provider(provider: str | None = None) -> str | None:
    pid = provider or active_provider()
    if pid == PROVIDER_OPENAI:
        key = _env_first("PORTFOLIO_OPENAI_API_KEY", "OPENAI_API_KEY", "API_KEY")
        return key or None
    if pid == PROVIDER_ANTHROPIC:
        key = _env_first("PORTFOLIO_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")
        return key or None
    if pid == PROVIDER_GEMINI:
        key = _env_first("PORTFOLIO_GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY")
        return key or None
    return None


def ollama_base_url() -> str:
    base = _env_first("PORTFOLIO_OLLAMA_BASE_URL", "OLLAMA_HOST") or "http://localhost:11434"
    return base.rstrip("/")


def agent_configured() -> bool:
    provider = active_provider()
    if not provider:
        return False
    if provider == PROVIDER_OLLAMA:
        return bool(model_name())
    return bool(api_key_for_provider(provider))


def vision_configured() -> bool:
    """Screenshot / vision features — OpenAI key or any configured cloud key."""
    if _env_first("PORTFOLIO_OPENAI_API_KEY", "OPENAI_API_KEY", "API_KEY"):
        return True
    return agent_configured() and active_provider() == PROVIDER_OPENAI


def llm_setup_status() -> dict[str, Any]:
    provider = active_provider()
    entry = _catalog_entry(provider) if provider else None
    api_configured = False
    if provider == PROVIDER_OLLAMA:
        api_configured = bool(_env_first("PORTFOLIO_OLLAMA_BASE_URL", "OLLAMA_HOST") or True)
    elif provider:
        api_configured = bool(api_key_for_provider(provider))

    return {
        "provider": provider,
        "provider_label": entry["label"] if entry else None,
        "model": model_name() if provider else None,
        "api_configured": api_configured,
        "configured": agent_configured(),
        "ollama_base_url": ollama_base_url() if provider == PROVIDER_OLLAMA else None,
        "providers": provider_catalog(),
    }


def llm_config_for_edit() -> dict[str, Any]:
    """Current LLM settings for the setup form (secrets masked)."""
    provider = active_provider()
    status = llm_setup_status()
    values: dict[str, Any] = {
        "provider": provider,
        "model": model_name() if provider else "",
        "base_url": ollama_base_url() if provider == PROVIDER_OLLAMA else "",
        "api_key_set": False,
    }
    if provider and provider != PROVIDER_OLLAMA:
        values["api_key_set"] = bool(api_key_for_provider(provider))
    return {**status, "values": values}


def save_llm_config(payload: dict[str, Any]) -> dict[str, Any]:
    provider = (payload.get("provider") or "").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Provider must be one of: {', '.join(SUPPORTED_PROVIDERS)}")

    entry = _catalog_entry(provider)
    updates: dict[str, str] = {"PORTFOLIO_LLM_PROVIDER": provider}

    model = (payload.get("model") or "").strip() or str(entry.get("default_model") or "")
    if model:
        updates["PORTFOLIO_LLM_MODEL"] = model

    if provider == PROVIDER_OLLAMA:
        base = (payload.get("base_url") or "").strip() or "http://localhost:11434"
        updates["PORTFOLIO_OLLAMA_BASE_URL"] = base.rstrip("/")
    else:
        api_key = (payload.get("api_key") or "").strip()
        key_env = next(
            (f["env"] for f in entry.get("fields") or [] if f.get("name") == "api_key"),
            None,
        )
        if not api_key and key_env and not env_var_present(key_env):
            raise ValueError("API key is required")
        if api_key:
            for field in entry.get("fields") or []:
                if field.get("name") == "api_key" and field.get("env"):
                    updates[field["env"]] = api_key
                    break

    upsert_env_vars(updates)
    status = llm_setup_status()
    return {
        "ok": True,
        "env_updated": True,
        "env_path": str(env_file_path()),
        "message": f"Saved to {env_file_path().name} — restart not required; agent uses new settings immediately.",
        **status,
    }
