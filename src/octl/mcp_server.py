"""octl MCP Server — stdio transport, consolidated tools."""

from __future__ import annotations

import json
import os
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Any

from fastmcp import FastMCP

from octl.client import OpenCodeClient

mcp = FastMCP("octl")

TZ = timezone(timedelta(hours=8))


def _client() -> OpenCodeClient:
    url = os.environ.get("OPENCODE_URL", "http://127.0.0.1:4096")
    password = os.environ.get("OPENCODE_SERVER_PASSWORD")
    return OpenCodeClient(base_url=url, password=password)


def _ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=TZ).strftime("%m-%d %H:%M")


# ── 1. server: health + providers + config ──


@mcp.tool()
def server(action: str = "status") -> str:
    """OpenCode server operations.

    Args:
        action: One of 'status' (health check), 'providers' (list models), 'config' (show config).
    """
    c = _client()
    if action == "status":
        h = c.health()
        return f"OK  v{h.get('version', '?')}" if h.get("healthy") else f"Unhealthy: {h}"
    if action == "providers":
        data = c.providers()
        connected = set(data.get("connected", []))
        lines = []
        for p in data.get("all", []):
            pid = p.get("id", "")
            s = "connected" if pid in connected else "disconnected"
            m = data.get("default", {}).get(pid, "")
            lines.append(f"  {pid} [{s}] {m}")
        return "\n".join(lines) or "No providers"
    if action == "config":
        return json.dumps(c.config_get(), ensure_ascii=False, default=str)
    return f"Unknown action: {action}"


# ── 2. session: all session operations ──


@mcp.tool()
def session(
    action: str,
    session_id: str | None = None,
    title: str | None = None,
    message_id: str | None = None,
) -> Any:
    """Manage OpenCode sessions.

    Args:
        action: One of 'list', 'get', 'new', 'delete', 'status', 'abort', 'diff', 'revert'.
        session_id: Required for get/delete/status/abort/diff/revert.
        title: Optional title for 'new'.
        message_id: Required for 'revert'.
    """
    c = _client()

    if action == "list":
        data = c.session_list()
        if not data:
            return "No sessions"
        lines = []
        for s in data:
            sid = s.get("id", "")[:16]
            title_ = s.get("title", "(untitled)")
            ts = s.get("time", {}).get("created", 0)
            lines.append(f"  {sid}  {_ts(ts) if ts else '?'}  {title_}")
        return "\n".join(lines)

    if action == "get":
        if not session_id:
            return "session_id required"
        return json.dumps(c.session_get(session_id), ensure_ascii=False, default=str)

    if action == "new":
        data = c.session_create(title=title)
        return f"Created session {data['id'][:16]}"

    if action == "delete":
        if not session_id:
            return "session_id required"
        c.session_delete(session_id)
        return f"Deleted {session_id[:16]}"

    if action == "status":
        if session_id:
            data = c.session_status(session_id)
            return f"{session_id[:16]}  state: {data.get('state', 'unknown')}"
        data = c.session_status()
        if not data:
            return "No running sessions"
        return "\n".join(f"  {s[:16]}  {d.get('state', '?')}" for s, d in data.items())

    if action == "abort":
        if not session_id:
            return "session_id required"
        c.session_abort(session_id)
        return f"Aborted {session_id[:16]}"

    if action == "diff":
        if not session_id:
            return "session_id required"
        data = c.session_diff(session_id)
        if not data:
            return "No changes"
        return "\n".join(f"  +{f.get('additions',0)} -{f.get('deletions',0)}  {f.get('path','')}" for f in data)

    if action == "revert":
        if not session_id or not message_id:
            return "session_id and message_id required"
        c.session_revert(session_id, message_id)
        return f"Reverted {message_id[:16]} in {session_id[:16]}"

    return f"Unknown action: {action}"


# ── 3. message: list + get ──


@mcp.tool()
def message(
    action: str = "list",
    session_id: str | None = None,
    message_id: str | None = None,
    limit: int | None = None,
) -> Any:
    """List or get messages in a session.

    Args:
        action: 'list' or 'get'.
        session_id: Required. Session to query.
        message_id: Required for 'get'.
        limit: Max messages for 'list'.
    """
    if not session_id:
        return "session_id required"
    c = _client()

    if action == "list":
        data = c.message_list(session_id, limit=limit)
        if not data:
            return "No messages"
        lines = []
        for msg in data:
            info = msg.get("info", {})
            role = info.get("role", "?")
            mid = info.get("id", "")[:16]
            for part in msg.get("parts", []):
                pt = part.get("type", "")
                if pt == "text":
                    lines.append(f"  {role} {mid}: {part.get('text', '')[:200]}")
                elif pt == "tool":
                    st = part.get("state", {})
                    d = st.get("input", {}).get("description", st.get("input", {}).get("filePath", ""))
                    lines.append(f"  {role} {mid}: [{part.get('tool','')}] {st.get('status','')} {d}")
                elif pt == "reasoning":
                    lines.append(f"  {role} {mid}: reasoning: {part.get('text', '')[:150]}")
        return "\n".join(lines)

    if action == "get":
        if not message_id:
            return "message_id required"
        return json.dumps(c.message_get(session_id, message_id), ensure_ascii=False, default=str)

    return f"Unknown action: {action}"


# ── 4. send: sync + async ──


@mcp.tool()
def send(
    prompt: str,
    session_id: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    agent: str | None = None,
    async_mode: bool = False,
    timeout: float = 300.0,
) -> Any:
    """Send a prompt to OpenCode and optionally wait for response.

    Args:
        prompt: The prompt text to send.
        session_id: Target session. Creates new if omitted.
        model: Model override.
        provider: Provider override.
        agent: Agent override.
        async_mode: If True, fire-and-forget (don't wait).
        timeout: Max seconds to wait (sync mode only).
    """
    c = _client()
    if async_mode:
        if not session_id:
            s = c.session_create()
            session_id = s["id"]
        c.message_send_async(
            session_id, [{"type": "text", "text": prompt}],  # type: ignore[arg-type]
            model=model, provider=provider, agent=agent,
        )
        return f"Sent to {session_id[:16]}"

    result = c.send_and_wait(
        prompt=prompt, session_id=session_id,
        model=model, provider=provider, agent=agent, timeout=timeout,
    )
    last_text = ""
    for msg in result.get("messages", []):
        if msg.get("info", {}).get("role") == "assistant":
            for part in msg.get("parts", []):
                if part.get("type") == "text":
                    last_text = part.get("text", "")
    return {"session_id": result["session_id"], "response": last_text}


# ── 5. file: list/read/search/find ──


@mcp.tool()
def file(action: str = "list", path: str = ".", pattern: str = "", query: str = "") -> Any:
    """File operations on the OpenCode server workspace.

    Args:
        action: One of 'list', 'read', 'search', 'find'.
        path: Directory path for 'list', file path for 'read'.
        pattern: Regex pattern for 'search'.
        query: Filename pattern for 'find'.
    """
    c = _client()

    if action == "list":
        data = c.file_list(path)
        if not data:
            return "Empty"
        return "\n".join(f"  {'/' if i.get('type')=='directory' else ' '} {i.get('name','')}" for i in data)

    if action == "read":
        if not path:
            return "path required"
        return c.file_read(path).get("content", "")

    if action == "search":
        if not pattern:
            return "pattern required"
        data = c.file_search(pattern)
        if not data:
            return "No matches"
        return "\n".join(f"  {m.get('path','')}:{m.get('line_number',0)}: {m.get('lines','').strip()}" for m in data)

    if action == "find":
        if not query:
            return "query required"
        data = c.file_find(query)
        return "\n".join(f"  {p}" for p in data) if data else "No files found"

    return f"Unknown action: {action}"


# ── 6. agents ──


@mcp.tool()
def agents() -> str:
    """List available agents."""
    data = _client().agent_list()
    if not data:
        return "No agents"
    return "\n".join(f"  {a.get('id','')}  {a.get('name','')}" for a in data)


# ── 7. events ──


@mcp.tool()
def events(duration_seconds: int = 30) -> str:
    """Subscribe to server events for a limited time.

    Args:
        duration_seconds: How long to listen (default 30).
    """
    c = _client()
    collected: list[dict] = []
    t = threading.Thread(target=c.events, kwargs={"callback": collected.append}, daemon=True)
    t.start()
    t.join(timeout=duration_seconds)
    return json.dumps(collected, ensure_ascii=False, default=str) if collected else "No events"


if __name__ == "__main__":
    mcp.run(transport="stdio")
