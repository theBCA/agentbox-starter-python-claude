"""Shared prompt text and response parsing for every DocBrief backend.

Each backend module (claude.py / openai_backend.py / google.py) exports one
async `process(document, question) -> dict` function using its own SDK's
native tool-calling idiom, but they all ask the model for the same JSON
shape and parse the final text response the same way — kept here once
instead of copy-pasted three times.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
_OPEN_FENCE_RE = re.compile(r"^```(?:json)?[ \t]*\r?\n?")

BASE_INSTRUCTIONS = (
    "You are DocBrief, a document-briefing assistant. Given a document, "
    "produce a concise 2-4 sentence summary and a list of short imperative "
    "action items found or implied in the document. If a question is asked, "
    "answer it using only the document's content; otherwise leave the "
    "answer null. Call get_current_date_iso if you need today's date for a "
    "relative action item (e.g. 'follow up next week'). Respond with ONLY a "
    "JSON object of this exact shape, no other text: "
    '{"summary": "...", "action_items": ["..."], "answer": "..." or null}'
)
INSTRUCTIONS = BASE_INSTRUCTIONS
_MAX_SKILL_CHARS = 12_000


def _skill_file_content(record: dict[str, Any], rel_path: str = "SKILL.md") -> str:
    for file_info in record.get("files") or []:
        if file_info.get("path") == rel_path and file_info.get("encoding") == "utf-8":
            return str(file_info.get("content") or "")
    return ""


async def load_app_skill_instructions() -> list[tuple[str, str]]:
    """Load only the app's read-only, AgentBox-approved skill mounts.

    AgentBox resolves catalog grants and mounts each approved skill below
    ``AGENTBOX_SKILLS_ROOT``. The app receives neither the global skills
    volume nor MongoDB credentials, so it cannot enumerate another app's
    records or bypass the catalog grant boundary.
    """
    import os

    root_value = os.environ.get("AGENTBOX_SKILLS_ROOT", "").strip()
    if not root_value:
        return []
    root = Path(root_value)
    if not root.is_dir():
        return []

    loaded: list[tuple[str, str]] = []
    for skill_file in sorted(root.rglob("SKILL.md")):
        try:
            content = skill_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if content:
            loaded.append((skill_file.parent.name, content))
    return loaded


async def build_system_instructions() -> str:
    skills = await load_app_skill_instructions()
    if not skills:
        return BASE_INSTRUCTIONS

    parts = [BASE_INSTRUCTIONS, "\n\nApproved AgentBox app skills from scoped mounts:"]
    remaining = _MAX_SKILL_CHARS
    for name, content in skills:
        if remaining <= 0:
            break
        chunk = content[:remaining]
        remaining -= len(chunk)
        parts.append(f"\n\n<skill name={json.dumps(name)}>\n{chunk}\n</skill>")
    return "".join(parts)


def build_user_text(document: str, question: str | None) -> str:
    user_text = f"Document:\n{document}"
    if question:
        user_text += f"\n\nQuestion: {question}"
    return user_text


def get_current_date_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def parse_result(text: str) -> dict:
    # Some models (observed with Gemini via OpenRouter) wrap the JSON in a
    # markdown code fence despite being told to respond with only JSON —
    # strip it before parsing rather than treating the whole fenced block
    # as an unparsed summary.
    stripped = text.strip()
    # search, not match: a model may put a sentence before or after the fenced
    # block, and a truncated response may never close the fence at all.
    # Anchoring the whole string made any of those fall through to the
    # "unparsed summary" branch, so the API handed back the raw fenced JSON
    # as its `summary`.
    fence_match = _FENCE_RE.search(stripped)
    if fence_match:
        candidate = fence_match.group(1).strip()
    else:
        candidate = _OPEN_FENCE_RE.sub("", stripped).strip()

    try:
        data = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return {"summary": stripped, "action_items": [], "answer": None}
    if not isinstance(data, dict):
        return {"summary": stripped, "action_items": [], "answer": None}
    return {
        "summary": str(data.get("summary", "")),
        "action_items": [str(x) for x in data.get("action_items", [])],
        "answer": data.get("answer"),
    }
