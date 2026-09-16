"""Claude Agent SDK backend for DocBrief.

Uses the real `claude-agent-sdk` package -- the agentic runtime, not the raw
`anthropic` API client. That distinction is the whole point: AgentBox exists
to secure *agentic* applications, so a starter built on a bare HTTP client
would not exercise what AgentBox protects (a tool loop, multi-turn
orchestration, and the subprocess the SDK drives).

Packaging consequence, verified against PyPI: `claude-agent-sdk`
publishes wheels only for manylinux/macos/win -- there is NO musllinux
build. On `python:3.12-alpine` pip therefore falls back to the 0.3MB sdist,
which does not bundle the Claude Code CLI this SDK spawns
(`_internal/transport/subprocess_cli.py`); it installs cleanly and fails at
runtime. This app is built on `python:3.12-slim` for that reason -- see the
note in the Dockerfile.

SecureProxy: `configure_secureproxy` binds ANTHROPIC_API_KEY and
ANTHROPIC_BASE_URL in `os.environ` before the SDK starts, so the CLI
subprocess inherits them and every model call is brokered by SecureProxy.
Direct provider fallback is refused, not silently allowed.
"""

from __future__ import annotations

import os

from claude_agent_sdk import ClaudeAgentOptions, query

from app.backends._secureproxy import configure_secureproxy
from app.backends._shared import (
    build_system_instructions,
    build_user_text,
    get_current_date_iso,
    parse_result,
)


def _text_of(message: object) -> str:
    """Pull assistant text out of whatever the SDK yields.

    The SDK streams several message types (assistant turns, tool activity, a
    final result). Rather than pattern-match on class names -- which couples
    this starter to SDK internals that move between versions -- take text
    from anything that carries it and let the caller join the pieces.
    """
    result = getattr(message, "result", None)
    if isinstance(result, str) and result.strip():
        return result

    parts: list[str] = []
    content = getattr(message, "content", None)
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, (list, tuple)):
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text.strip():
                parts.append(text)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
    return "\n".join(parts)


async def process(document: str, question: str | None) -> dict:
    configure_secureproxy("anthropic")

    # The date tool is exposed to the model through the system prompt rather
    # than as an SDK tool: the value is fixed for the life of the request, so
    # handing it over up front costs one line and removes a whole tool round
    # trip from every call.
    instructions = (
        f"{await build_system_instructions()}\n\n"
        f"Today's date in ISO 8601 format is {get_current_date_iso()}."
    )

    options = ClaudeAgentOptions(
        system_prompt=instructions,
        max_turns=6,
        model=os.environ.get("DOCBRIEF_CLAUDE_MODEL", "claude-sonnet-4-5"),
    )

    chunks: list[str] = []
    async for message in query(
        prompt=build_user_text(document, question), options=options
    ):
        text = _text_of(message)
        if text:
            chunks.append(text)

    if not chunks:
        raise RuntimeError(
            "claude-agent-sdk returned no assistant text; the bundled CLI may "
            "be unavailable in this image"
        )
    return parse_result(chunks[-1] if len(chunks) == 1 else "\n".join(chunks))


def _content_blocks(message: object) -> list:
    content = getattr(message, "content", None)
    return list(content) if isinstance(content, (list, tuple)) else []


def _block_field(block: object, field: str):
    if isinstance(block, dict):
        return block.get(field)
    return getattr(block, field, None)


async def stream(document: str, question: str | None):
    """Yield the agent's turns as they happen, for POST /process/stream.

    Deliberately NOT built on `_text_of`: that helper returns the final
    ResultMessage's `result`, which is the complete answer again, so a stream
    built on it would replay every token a second time at the end. Here the
    final result is emitted once as its own `result` event and the incremental
    text comes only from assistant content blocks.
    """
    configure_secureproxy("anthropic")

    instructions = (
        f"{await build_system_instructions()}\n\n"
        f"Today's date in ISO 8601 format is {get_current_date_iso()}."
    )
    options = ClaudeAgentOptions(
        system_prompt=instructions,
        max_turns=6,
        model=os.environ.get("DOCBRIEF_CLAUDE_MODEL", "claude-sonnet-4-5"),
    )

    async for message in query(
        prompt=build_user_text(document, question), options=options
    ):
        result = getattr(message, "result", None)
        if isinstance(result, str) and result.strip():
            yield {"type": "result", "text": result}
            continue

        for block in _content_blocks(message):
            block_type = _block_field(block, "type")
            if block_type == "tool_use":
                yield {
                    "type": "tool",
                    "name": str(_block_field(block, "name") or "tool"),
                    "input": _block_field(block, "input") or {},
                }
                continue
            text = _block_field(block, "text")
            if isinstance(text, str) and text.strip():
                yield {"type": "token", "text": text}
