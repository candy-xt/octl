"""octl MCP Server — stdio transport, full CLI coverage."""

from __future__ import annotations

import json
import os
from typing import Any

from fastmcp import FastMCP

from octl.client import OpenCodeClient

mcp = FastMCP(
    "octl",
    instructions="Agent-friendly CLI for controlling OpenCode server. "
    "Provides session management, message listing, file operations, "
    "and prompt sending via the OpenCode API.",
)

# ── client singleton ──


def _get_client() -> OpenCodeClient:
    url = os.environ.get("OPENCODE_URL", "http://127.0.0.1:4096")
    password = os.environ.get("OPENCODE_SERVER_PASSWORD")
    return OpenCodeClient(base_url=url, password=password)


# ── health / status ──


@mcp.tool()
def server_status() -> str:
    """Check OpenCode server health and version."""
    client = _get_client()
    h = client.health()
    if h.get("healthy"):
        return f"OK  v{h.get('version', '?')}"
    return f"Unhealthy: {json.dumps(h, ensure_ascii=False)}"


@mcp.tool()
def server_providers() -> str:
    """List available providers and models."""
    client = _get_client()
    data = client.providers()
    connected = data.get("connected", [])
    all_providers = data.get("all", [])
    defaults = data.get("default", {})
    lines = []
    for p in all_providers:
        pid = p.get("id", "")
        status = "connected" if pid in connected else "disconnected"
        model = defaults.get(pid, "")
        lines.append(f"  {pid}  [{status}]  {model}")
    return "\n".join(lines) if lines else "No providers"


@mcp.tool()
def server_config() -> dict:
    """Show server configuration."""
    client = _get_client()
    return client.config_get()


# ── sessions ──


@mcp.tool()
def session_list() -> str:
    """List all sessions with ID, title, and creation time."""
    from datetime import datetime, timezone, timedelta

    tz = timezone(timedelta(hours=8))
    client = _get_client()
    data = client.session_list()
    if not data:
        return "No sessions"
    lines = []
    for s in data:
        sid = s.get("id", "")[:16]
        title = s.get("title", "(untitled)")
        ts = s.get("time", {}).get("created", 0)
        if ts:
            dt = datetime.fromtimestamp(ts / 1000, tz=tz)
            created = dt.strftime("%m-%d %H:%M")
        else:
            created = "?"
        lines.append(f"  {sid}  {created}  {title}")
    return "\n".join(lines)


@mcp.tool()
def session_get(session_id: str) -> dict:
    """Get full session details (ID, title, tokens, timestamps, etc.)."""
    client = _get_client()
    return client.session_get(session_id)


@mcp.tool()
def session_new(title: str | None = None) -> dict:
    """Create a new session. Returns session ID and details."""
    client = _get_client()
    data = client.session_create(title=title)
    return {"session_id": data["id"], "title": data.get("title"), "created": data.get("time", {}).get("created")}


@mcp.tool()
def session_delete(session_id: str) -> str:
    """Delete a session by ID."""
    client = _get_client()
    client.session_delete(session_id)
    return f"Deleted session {session_id[:16]}"


@mcp.tool()
def session_status(session_id: str | None = None) -> str:
    """Show session running state. Pass session_id for a specific session, or None for all."""
    client = _get_client()
    if session_id:
        data = client.session_status(session_id)
        state = data.get("state", "unknown")
        return f"{session_id[:16]}  state: {state}"
    data = client.session_status()
    if not data:
        return "No running sessions"
    lines = []
    for sid, status in data.items():
        state = status.get("state", "unknown")
        lines.append(f"  {sid[:16]}  {state}")
    return "\n".join(lines)


@mcp.tool()
def session_abort(session_id: str) -> str:
    """Abort a running session."""
    client = _get_client()
    client.session_abort(session_id)
    return f"Aborted session {session_id[:16]}"


@mcp.tool()
def session_diff(session_id: str) -> str:
    """Show file changes made in a session."""
    client = _get_client()
    data = client.session_diff(session_id)
    if not data:
        return "No changes"
    lines = []
    for f in data:
        path = f.get("path", "")
        adds = f.get("additions", 0)
        dels = f.get("deletions", 0)
        lines.append(f"  +{adds} -{dels}  {path}")
    return "\n".join(lines)


@mcp.tool()
def session_revert(session_id: str, message_id: str) -> str:
    """Revert changes made by a specific message in a session."""
    client = _get_client()
    client.session_revert(session_id, message_id)
    return f"Reverted message {message_id[:16]} in session {session_id[:16]}"


# ── messages ──


@mcp.tool()
def message_list(session_id: str, limit: int | None = None) -> str:
    """List messages in a session. Optionally limit the count."""
    client = _get_client()
    data = client.message_list(session_id, limit=limit)
    if not data:
        return "No messages"
    lines = []
    for msg in data:
        info = msg.get("info", {})
        role = info.get("role", "?")
        mid = info.get("id", "")[:16]
        parts = msg.get("parts", [])
        for part in parts:
            ptype = part.get("type", "")
            if ptype == "text":
                text = part.get("text", "")[:200]
                lines.append(f"  {role} {mid}: {text}")
            elif ptype == "tool":
                tool = part.get("tool", "")
                state = part.get("state", {})
                status = state.get("status", "")
                inp = state.get("input", {})
                desc = inp.get("description", inp.get("filePath", inp.get("command", "")[:80]))
                lines.append(f"  {role} {mid}: [{tool}] {status} - {desc}")
            elif ptype == "reasoning":
                text = part.get("text", "")[:150]
                lines.append(f"  {role} {mid}: reasoning: {text}")
    return "\n".join(lines)


@mcp.tool()
def message_get(session_id: str, message_id: str) -> dict:
    """Get full details of a specific message."""
    client = _get_client()
    return client.message_get(session_id, message_id)


# ── send ──


@mcp.tool()
def send_prompt(
    prompt: str,
    session_id: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    agent: str | None = None,
    timeout: float = 300.0,
) -> dict:
    """Send a prompt to a session and wait for response. Creates a new session if none provided."""
    client = _get_client()
    result = client.send_and_wait(
        prompt=prompt,
        session_id=session_id,
        model=model,
        provider=provider,
        agent=agent,
        timeout=timeout,
    )
    # Extract text from the last assistant message
    last_text = ""
    for msg in result.get("messages", []):
        info = msg.get("info", {})
        if info.get("role") == "assistant":
            for part in msg.get("parts", []):
                if part.get("type") == "text":
                    last_text = part.get("text", "")
    return {
        "session_id": result["session_id"],
        "response": last_text,
    }


@mcp.tool()
def send_prompt_async(
    prompt: str,
    session_id: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    agent: str | None = None,
) -> dict:
    """Send a prompt without waiting for response (fire and forget)."""
    client = _get_client()
    if not session_id:
        s = client.session_create()
        session_id = s["id"]
    client.message_send_async(
        session_id,
        [{"type": "text", "text": prompt}],
        model=model,
        provider=provider,
        agent=agent,
    )
    return {"session_id": session_id, "status": "sent"}


# ── files ──


@mcp.tool()
def file_list(path: str = ".") -> str:
    """List files and directories at a path."""
    client = _get_client()
    data = client.file_list(path)
    lines = []
    for item in data:
        name = item.get("name", "")
        kind = item.get("type", "")
        icon = "/" if kind == "directory" else " "
        lines.append(f"  {icon} {name}")
    return "\n".join(lines) if lines else "Empty directory"


@mcp.tool()
def file_read(path: str) -> str:
    """Read file content."""
    client = _get_client()
    data = client.file_read(path)
    return data.get("content", "")


@mcp.tool()
def file_search(pattern: str) -> str:
    """Search file contents by regex pattern."""
    client = _get_client()
    data = client.file_search(pattern)
    if not data:
        return "No matches"
    lines = []
    for match in data:
        path = match.get("path", "")
        line_num = match.get("line_number", 0)
        text = match.get("lines", "").strip()
        lines.append(f"  {path}:{line_num}: {text}")
    return "\n".join(lines)


@mcp.tool()
def file_find(query: str) -> str:
    """Find files by name pattern."""
    client = _get_client()
    data = client.file_find(query)
    if not data:
        return "No files found"
    return "\n".join(f"  {p}" for p in data)


# ── agents ──


@mcp.tool()
def agent_list() -> str:
    """List available agents."""
    client = _get_client()
    data = client.agent_list()
    if not data:
        return "No agents"
    lines = []
    for a in data:
        aid = a.get("id", "")
        name = a.get("name", "")
        lines.append(f"  {aid}  {name}")
    return "\n".join(lines)


# ── events ──


@mcp.tool()
def events_subscribe(duration_seconds: int = 30) -> str:
    """Subscribe to server events for a limited time. Returns collected events as JSON."""
    client = _get_client()
    events: list[dict] = []

    import time

    start = time.time()

    def on_event(event: dict) -> None:
        events.append(event)

    try:
        # events() blocks, we need to run it in a thread with a timeout
        import threading

        t = threading.Thread(target=client.events, kwargs={"callback": on_event}, daemon=True)
        t.start()
        t.join(timeout=duration_seconds)
    except Exception:
        pass

    if not events:
        return "No events received"
    return json.dumps(events, ensure_ascii=False, default=str)


if __name__ == "__main__":
    mcp.run(transport="stdio")
