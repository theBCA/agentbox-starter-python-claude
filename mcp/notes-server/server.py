"""A small MCP server bundled with this starter app.

AgentBox scans it, then exposes it through MCP Bridge as
`<app-id>__notes-server`. Three tools, picked to take three different paths:

    save_note(text)      writes
    list_notes()         read-only, so it is never held
    delete_all_notes()   destructive, so the bridge holds it for approval

That last one is why there are three. AgentBox classifies a tool from its
name and the first line of its docstring; "delete" makes it destructive,
which means an operator has to approve the call before it runs.

Notes go to a file, not a list in memory, so they survive a restart and
`list_notes` has something to return. This server is its own container, so
the app's /app/scratch is not visible here.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# host must be 0.0.0.0, not FastMCP's 127.0.0.1 default -- this server runs
# in its own container, reachable from managed-mcp-bridge over the docker
# network, not from a process sharing its own loopback.
mcp = FastMCP("notes-server", host="0.0.0.0")

_STORE = Path(os.environ.get("NOTES_STORE_PATH", "/data/notes.json"))
# FastMCP serves requests from a thread pool, so two concurrent save_note
# calls can interleave a read-modify-write and lose one. A lock is cheaper
# than reasoning about whether that can happen in practice.
_LOCK = threading.Lock()


def _read() -> list[dict]:
    try:
        raw = _STORE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # A truncated file (killed mid-write) should not make every
        # subsequent call fail. Start over rather than raise.
        return []
    return data if isinstance(data, list) else []


def _write(notes: list[dict]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-rename: a crash mid-write leaves the previous good file in
    # place instead of a half-written one.
    tmp = _STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(notes, indent=2), encoding="utf-8")
    tmp.replace(_STORE)


@mcp.tool()
def save_note(text: str) -> dict:
    """Save a short note and return its id."""
    text = (text or "").strip()
    if not text:
        return {"saved": False, "error": "text must not be empty"}
    with _LOCK:
        notes = _read()
        note = {
            "id": (max((n.get("id", 0) for n in notes), default=0) + 1),
            "text": text[:4000],
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        notes.append(note)
        _write(notes)
        return {"id": note["id"], "saved": True, "count": len(notes)}


@mcp.tool()
def list_notes() -> dict:
    """List every note saved so far, newest last."""
    with _LOCK:
        notes = _read()
    return {"notes": notes, "count": len(notes)}


@mcp.tool()
def delete_all_notes() -> dict:
    """Delete every saved note permanently.

    Deliberately destructive, and named so the bridge can tell: this is the
    starter's example of a tool that MCP Bridge holds for operator approval
    rather than running on request. Approve or deny it in the admin console
    under Security, then call it again.
    """
    with _LOCK:
        removed = len(_read())
        _write([])
    return {"deleted": removed, "remaining": 0}


if __name__ == "__main__":
    # sse, not FastMCP's stdio default: this runs as its own detached
    # container with no attached stdin, where stdio reads EOF and exits
    # immediately. The bridge reaches it over HTTP at <container>:8000/sse.
    #
    # The retry absorbs a slow container network at startup, where the SSE
    # app can exit cleanly (code 0) before anything is listening.
    for attempt in range(10):
        try:
            mcp.run(transport="sse")
            break
        except BaseException as exc:
            print(
                f"notes-server: startup attempt {attempt} failed: {exc!r}", flush=True
            )
            time.sleep(1)
