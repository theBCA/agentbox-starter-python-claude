"""Fail-closed SecureProxy binding for AgentBox-managed starter backends."""

from __future__ import annotations

import os


_PROVIDERS = {
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", ""),
    "google": ("GOOGLE_API_KEY", "GOOGLE_API_BASE", ""),
    "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "/v1"),
}


def configure_secureproxy(provider: str) -> tuple[str, str] | None:
    """Force managed model traffic through the model-agnostic proxy contract."""
    try:
        api_key_env, base_url_env, path = _PROVIDERS[provider]
    except KeyError as exc:
        raise ValueError(f"Unsupported SecureProxy provider: {provider}") from exc

    proxy_url = os.environ.get("KOBIL_SECUREPROXY_URL", "").strip()
    virtual_key = os.environ.get("KOBIL_SECUREPROXY_API_KEY", "").strip()
    managed = bool(os.environ.get("AGENTBOX_APP_ID", "").strip()) or (
        os.environ.get("KOBIL_SECUREPROXY_REQUIRED") == "1"
    )
    required = managed or bool(proxy_url or virtual_key)
    if not required:
        return None
    if not proxy_url or not virtual_key:
        raise RuntimeError(
            "AgentBox-managed model calls require KOBIL_SECUREPROXY_URL and "
            "KOBIL_SECUREPROXY_API_KEY; direct provider fallback is disabled."
        )

    base_url = f"{proxy_url.rstrip('/')}{path}"
    os.environ[api_key_env] = virtual_key
    os.environ[base_url_env] = base_url
    if provider == "google":
        os.environ["GOOGLE_GEMINI_BASE_URL"] = base_url
    return virtual_key, base_url
