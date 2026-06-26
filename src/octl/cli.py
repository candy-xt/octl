"""octl CLI - Agent-friendly interface for OpenCode server."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from octl.client import OpenCodeClient, OCError, discover_server
from octl.context import get_active_session, set_active_session, clear_active_session
from octl import __version__

console = Console()

DEFAULT_BASE_URL = "http://127.0.0.1:4096"


# ── helpers ──


def get_client(base_url: str | None = None) -> OpenCodeClient:
    url = base_url or os.environ.get("OPENCODE_URL")
    password = os.environ.get("OPENCODE_SERVER_PASSWORD")

    if url:
        return OpenCodeClient(base_url=url, password=password)

    client = OpenCodeClient(base_url=DEFAULT_BASE_URL, password=password)
    if client.check_server():
        return client

    discovered = discover_server()
    if discovered:
        return OpenCodeClient(base_url=discovered, password=password)

    return client


def print_json(data: Any) -> None:
    click.echo(json.dumps(data, ensure_ascii=False, default=str))


def print_error(msg: str) -> None:
    click.echo(f"Error: {msg}", err=True)


def should_output_json(as_json: bool, ctx: click.Context) -> bool:
    return as_json or ctx.obj.get("format") == "json"


def resolve_session(session_id: str | None) -> str | None:
    """Return the given session_id, fall back to active session, or None."""
    if session_id:
        return session_id
    active = get_active_session()
    return active


def _print_sessions_table(data: list[dict]) -> None:
    if not data:
        console.print("[dim]No sessions[/dim]")
        return
    from datetime import datetime, timezone, timedelta

    tz = timezone(timedelta(hours=8))
    table = Table(title="Sessions")
    table.add_column("ID", style="cyan", max_width=12)
    table.add_column("Title")
    table.add_column("Created")
    for s in data:
        sid = s.get("id", "")[:12]
        title = s.get("title", "(untitled)")
        created_ts = s.get("time", {}).get("created", 0)
        if created_ts:
            dt = datetime.fromtimestamp(created_ts / 1000, tz=tz)
            created = dt.strftime("%m-%d %H:%M")
        else:
            created = ""
        table.add_row(sid, title, created)
    console.print(table)


def _print_diff(data: list[dict]) -> None:
    if not data:
        console.print("[dim]No changes[/dim]")
        return
    for f in data:
        path = f.get("path", "")
        additions = f.get("additions", 0)
        deletions = f.get("deletions", 0)
        console.print(f"  [green]+{additions}[/green] [red]-{deletions}[/red]  {path}")


def _do_file_list(client: OpenCodeClient, path: str, as_json: bool, ctx: click.Context) -> None:
    data = client.file_list(path)
    if should_output_json(as_json, ctx):
        print_json(data)
        return
    for item in data:
        name = item.get("name", "")
        kind = item.get("type", "")
        icon = "[blue]/[/blue]" if kind == "directory" else " "
        console.print(f"  {icon} {name}")


def _do_file_read(client: OpenCodeClient, path: str) -> None:
    data = client.file_read(path)
    console.print(data.get("content", ""))


def _do_file_search(client: OpenCodeClient, pattern: str, as_json: bool, ctx: click.Context) -> None:
    data = client.file_search(pattern)
    if should_output_json(as_json, ctx):
        print_json(data)
        return
    for match in data:
        path = match.get("path", "")
        line_num = match.get("line_number", 0)
        lines = match.get("lines", "")
        console.print(f"  [cyan]{path}[/cyan]:{line_num}: {lines.strip()}")


def _do_file_find(client: OpenCodeClient, query: str, as_json: bool, ctx: click.Context) -> None:
    data = client.file_find(query)
    if should_output_json(as_json, ctx):
        print_json(data)
        return
    for path in data:
        console.print(f"  {path}")


# ── main group ──


@click.group()
@click.version_option(__version__, prog_name="octl")
@click.option("--url", envvar="OPENCODE_URL", default=None, help="OpenCode server URL")
@click.option("--quiet", is_flag=True, default=False, help="Suppress non-essential output")
@click.option("--format", "output_format", type=click.Choice(["text", "json"]), default="text", help="Output format")
@click.pass_context
def main(ctx: click.Context, url: str | None, quiet: bool, output_format: str) -> None:
    """octl - Agent-friendly CLI for OpenCode server."""
    ctx.ensure_object(dict)
    ctx.obj["url"] = url
    ctx.obj["quiet"] = quiet
    ctx.obj["format"] = output_format


# ── status ──


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def status(ctx: click.Context, as_json: bool) -> None:
    """Check server health and version."""
    client = get_client(ctx.obj.get("url"))
    try:
        h = client.health()
        if should_output_json(as_json, ctx):
            print_json(h)
            return
        if h.get("healthy"):
            if not ctx.obj.get("quiet"):
                console.print(f"[green]OK[/green]  v{h.get('version', '?')}")
            else:
                click.echo(h.get("version", "?"))
        else:
            print_error("Server unhealthy")
            sys.exit(1)
    except Exception as e:
        print_error(f"Cannot connect: {e}")
        sys.exit(1)


# ── providers ──


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def providers(ctx: click.Context, as_json: bool) -> None:
    """List available providers and models."""
    client = get_client(ctx.obj.get("url"))
    data = client.providers()
    if should_output_json(as_json, ctx):
        print_json(data)
        return

    connected = data.get("connected", [])
    all_providers = data.get("all", [])
    defaults = data.get("default", {})

    table = Table(title="Providers")
    table.add_column("ID", style="cyan")
    table.add_column("Status")
    table.add_column("Default Model", style="green")

    for p in all_providers:
        pid = p.get("id", "")
        is_connected = pid in connected
        status_str = "[green]connected[/green]" if is_connected else "[dim]disconnected[/dim]"
        default_model = defaults.get(pid, "")
        table.add_row(pid, status_str, default_model)

    console.print(table)


# ── send ──


@main.command()
@click.argument("prompt")
@click.option("--session", "-s", "session_id", default=None, help="Session ID (creates new if omitted)")
@click.option("--model", "-m", default=None, help="Model ID")
@click.option("--provider", "-p", default=None, help="Provider ID")
@click.option("--agent", "-a", default=None, help="Agent ID")
@click.option("--async", "send_async", is_flag=True, default=False, help="Send without waiting for response")
@click.option("--timeout", "-t", default=300.0, help="Timeout in seconds")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def send(
    ctx: click.Context,
    prompt: str,
    session_id: str | None,
    model: str | None,
    provider: str | None,
    agent: str | None,
    send_async: bool,
    timeout: float,
    as_json: bool,
) -> None:
    """Send a prompt and wait for response."""
    client = get_client(ctx.obj.get("url"))

    resolved = resolve_session(session_id)
    if resolved:
        sid = resolved
    else:
        s = client.session_create()
        sid = s["id"]

    set_active_session(sid)

    parts = [{"type": "text", "text": prompt}]

    if send_async:
        client.message_send_async(sid, parts, model=model, provider=provider, agent=agent)
        result = {"session_id": sid, "status": "sent"}
        if should_output_json(as_json, ctx):
            print_json(result)
        else:
            quiet = ctx.obj.get("quiet", False)
            if not quiet:
                console.print(f"[dim]Sent to session {sid[:12]}[/dim]")
            else:
                click.echo(sid)
        return

    try:
        result = client.send_and_wait(
            prompt=prompt,
            session_id=sid,
            model=model,
            provider=provider,
            agent=agent,
            timeout=timeout,
        )
        if should_output_json(as_json, ctx):
            print_json(result)
            return

        if not ctx.obj.get("quiet"):
            console.print(f"[dim]Session:[/dim] {result['session_id'][:12]}")
        for msg in result.get("messages", []):
            info = msg.get("info", {})
            parts = msg.get("parts", [])
            role = info.get("role", "?")
            for part in parts:
                if part.get("type") == "text":
                    text = part.get("text", "")
                    if role == "assistant":
                        if ctx.obj.get("quiet"):
                            click.echo(text)
                        else:
                            console.print(Panel(text, title="Response", border_style="green"))
    except OCError as e:
        print_error(str(e))
        sys.exit(1)


# ── abort ──


@main.command()
@click.argument("session_id", required=False, default=None)
@click.pass_context
def abort(ctx: click.Context, session_id: str | None) -> None:
    """Abort a running session."""
    sid = resolve_session(session_id)
    if not sid:
        print_error("No session ID provided and no active session")
        sys.exit(1)
    client = get_client(ctx.obj.get("url"))
    client.session_abort(sid)
    if not ctx.obj.get("quiet"):
        console.print(f"[yellow]Aborted[/yellow] session {sid[:12]}")


# ── diff ──


@main.command()
@click.argument("session_id", required=False, default=None)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def diff(ctx: click.Context, session_id: str | None, as_json: bool) -> None:
    """Show file changes from a session."""
    sid = resolve_session(session_id)
    if not sid:
        print_error("No session ID provided and no active session")
        sys.exit(1)
    client = get_client(ctx.obj.get("url"))
    data = client.session_diff(sid)
    if should_output_json(as_json, ctx):
        print_json(data)
        return
    _print_diff(data)


# ── revert ──


@main.command()
@click.argument("session_id")
@click.argument("message_id")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def revert(ctx: click.Context, session_id: str, message_id: str, as_json: bool) -> None:
    """Revert changes made by a message."""
    client = get_client(ctx.obj.get("url"))
    try:
        data = client.session_revert(session_id, message_id)
        if should_output_json(as_json, ctx):
            print_json(data)
        else:
            console.print(f"[yellow]Reverted[/yellow] message {message_id[:12]} in session {session_id[:12]}")
    except Exception as e:
        print_error(str(e))
        sys.exit(1)


# ── messages ──


@main.command()
@click.argument("session_id", required=False, default=None)
@click.option("--limit", "-n", default=None, type=int, help="Max messages to show")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def messages(ctx: click.Context, session_id: str | None, limit: int | None, as_json: bool) -> None:
    """List messages in a session."""
    sid = resolve_session(session_id)
    if not sid:
        print_error("No session ID provided and no active session")
        sys.exit(1)
    client = get_client(ctx.obj.get("url"))
    data = client.message_list(sid, limit=limit)
    if should_output_json(as_json, ctx):
        print_json(data)
        return

    for msg in data:
        info = msg.get("info", {})
        parts = msg.get("parts", [])
        role = info.get("role", "?")
        mid = info.get("id", "")[:12]
        for part in parts:
            if part.get("type") == "text":
                text = part.get("text", "")
                if role == "user":
                    console.print(f"  [cyan]{role}[/cyan] {mid}: {text[:100]}")
                else:
                    console.print(f"  [green]{role}[/green] {mid}: {text[:200]}")


# ── session ──


class _SessionGroup(click.Group):
    """Group that falls back to 'get' when the command is not a subcommand."""

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        cmd = super().get_command(ctx, cmd_name)
        if cmd is not None:
            return cmd
        if cmd_name.startswith("_"):
            return None
        @click.command(name=cmd_name, hidden=True)
        @click.pass_context
        def _get(_ctx: click.Context) -> None:
            _display_session(_ctx, cmd_name)
        return _get


def _display_session(ctx: click.Context, session_id: str) -> None:
    client = get_client(ctx.obj.get("url"))
    data = client.session_get(session_id)
    set_active_session(session_id)
    if ctx.obj.get("format") == "json":
        print_json(data)
    else:
        console.print_json(json.dumps(data, ensure_ascii=False, default=str))


@main.group(cls=_SessionGroup)
@click.pass_context
def session(ctx: click.Context) -> None:
    """Manage sessions."""


@session.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def session_list(ctx: click.Context, as_json: bool) -> None:
    """List all sessions."""
    client = get_client(ctx.obj.get("url"))
    data = client.session_list()
    if should_output_json(as_json, ctx):
        print_json(data)
        return
    _print_sessions_table(data)


@session.command("new")
@click.argument("title", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def session_new(ctx: click.Context, title: str | None, as_json: bool) -> None:
    """Create a new session."""
    client = get_client(ctx.obj.get("url"))
    data = client.session_create(title=title)
    sid = data["id"]
    set_active_session(sid)
    if should_output_json(as_json, ctx):
        print_json(data)
        return
    quiet = ctx.obj.get("quiet", False)
    if quiet:
        click.echo(sid)
    else:
        console.print(f"[green]Created[/green] session {sid[:12]}")


@session.command("get")
@click.argument("session_id")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def session_get(ctx: click.Context, session_id: str, as_json: bool) -> None:
    """Get session details."""
    client = get_client(ctx.obj.get("url"))
    data = client.session_get(session_id)
    set_active_session(session_id)
    if should_output_json(as_json, ctx):
        print_json(data)
        return
    console.print_json(json.dumps(data, ensure_ascii=False, default=str))


@session.command("status")
@click.argument("session_id", required=False, default=None)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def session_status(ctx: click.Context, session_id: str | None, as_json: bool) -> None:
    """Show session running status (all or specific)."""
    client = get_client(ctx.obj.get("url"))
    if session_id:
        data = client.session_status(session_id)
        if should_output_json(as_json, ctx):
            print_json(data)
            return
        state = data.get("state", "unknown")
        console.print(f"  [cyan]{session_id[:12]}[/cyan]  state: [bold]{state}[/bold]")
    else:
        data = client.session_status()
        if should_output_json(as_json, ctx):
            print_json(data)
            return
        table = Table(title="Session Status")
        table.add_column("ID", style="cyan", max_width=12)
        table.add_column("State")
        for sid, status in data.items():
            state = status.get("state", "unknown")
            table.add_row(sid[:12], state)
        console.print(table)


@session.command("abort")
@click.argument("session_id")
@click.pass_context
def session_abort(ctx: click.Context, session_id: str) -> None:
    """Abort a running session."""
    client = get_client(ctx.obj.get("url"))
    client.session_abort(session_id)
    if not ctx.obj.get("quiet"):
        console.print(f"[yellow]Aborted[/yellow] session {session_id[:12]}")


@session.command("delete")
@click.argument("session_id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@click.pass_context
def session_delete(ctx: click.Context, session_id: str, yes: bool) -> None:
    """Delete a session."""
    if not yes:
        click.confirm(f"Delete session {session_id[:12]}?", abort=True)
    client = get_client(ctx.obj.get("url"))
    client.session_delete(session_id)
    active = get_active_session()
    if active == session_id:
        clear_active_session()
    if not ctx.obj.get("quiet"):
        console.print(f"[red]Deleted[/red] session {session_id[:12]}")


@session.command("diff")
@click.argument("session_id")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def session_diff(ctx: click.Context, session_id: str, as_json: bool) -> None:
    """Show file changes from a session."""
    client = get_client(ctx.obj.get("url"))
    data = client.session_diff(session_id)
    if should_output_json(as_json, ctx):
        print_json(data)
        return
    _print_diff(data)


@session.command("use")
@click.argument("session_id")
@click.pass_context
def session_use(ctx: click.Context, session_id: str) -> None:
    """Set the active session for subsequent commands."""
    set_active_session(session_id)
    if not ctx.obj.get("quiet"):
        console.print(f"  [green]Active session:[/green] {session_id[:12]}")


@session.command("messages")
@click.argument("session_id", required=False, default=None)
@click.option("--limit", "-n", default=None, type=int, help="Max messages to show")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def session_messages(
    ctx: click.Context, session_id: str | None, limit: int | None, as_json: bool
) -> None:
    """List messages in a session."""
    sid = resolve_session(session_id)
    if not sid:
        print_error("No session ID provided and no active session")
        sys.exit(1)
    client = get_client(ctx.obj.get("url"))
    data = client.message_list(sid, limit=limit)
    if should_output_json(as_json, ctx):
        print_json(data)
        return

    for msg in data:
        info = msg.get("info", {})
        parts = msg.get("parts", [])
        role = info.get("role", "?")
        mid = info.get("id", "")[:12]
        for part in parts:
            if part.get("type") == "text":
                text = part.get("text", "")
                if role == "user":
                    console.print(f"  [cyan]{role}[/cyan] {mid}: {text[:100]}")
                else:
                    console.print(f"  [green]{role}[/green] {mid}: {text[:200]}")


# ── file group ──


@main.group()
def file() -> None:
    """File operations."""


@file.command("list")
@click.argument("path", default=".")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def file_list(ctx: click.Context, path: str, as_json: bool) -> None:
    """List files and directories."""
    client = get_client(ctx.obj.get("url"))
    _do_file_list(client, path, as_json, ctx)


@file.command("read")
@click.argument("path")
@click.pass_context
def file_read(ctx: click.Context, path: str) -> None:
    """Read file content."""
    client = get_client(ctx.obj.get("url"))
    _do_file_read(client, path)


@file.command("search")
@click.argument("pattern")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def file_search(ctx: click.Context, pattern: str, as_json: bool) -> None:
    """Search file contents (regex)."""
    client = get_client(ctx.obj.get("url"))
    _do_file_search(client, pattern, as_json, ctx)


@file.command("grep")
@click.argument("pattern")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def file_grep(ctx: click.Context, pattern: str, as_json: bool) -> None:
    """Search file contents (regex), alias for search."""
    client = get_client(ctx.obj.get("url"))
    _do_file_search(client, pattern, as_json, ctx)


@file.command("find")
@click.argument("query")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def file_find(ctx: click.Context, query: str, as_json: bool) -> None:
    """Find files by name."""
    client = get_client(ctx.obj.get("url"))
    _do_file_find(client, query, as_json, ctx)


# ── top-level file aliases ──


@main.command("ls")
@click.argument("path", default=".")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def ls(ctx: click.Context, path: str, as_json: bool) -> None:
    """List files and directories."""
    client = get_client(ctx.obj.get("url"))
    _do_file_list(client, path, as_json, ctx)


@main.command("read")
@click.argument("path")
@click.pass_context
def read_file(ctx: click.Context, path: str) -> None:
    """Read file content."""
    client = get_client(ctx.obj.get("url"))
    _do_file_read(client, path)


@main.command("grep")
@click.argument("pattern")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def grep(ctx: click.Context, pattern: str, as_json: bool) -> None:
    """Search file contents."""
    client = get_client(ctx.obj.get("url"))
    _do_file_search(client, pattern, as_json, ctx)


@main.command("find")
@click.argument("query")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def find_file(ctx: click.Context, query: str, as_json: bool) -> None:
    """Find files by name."""
    client = get_client(ctx.obj.get("url"))
    _do_file_find(client, query, as_json, ctx)


# ── agents ──


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def agents(ctx: click.Context, as_json: bool) -> None:
    """List available agents."""
    client = get_client(ctx.obj.get("url"))
    data = client.agent_list()
    if should_output_json(as_json, ctx):
        print_json(data)
        return

    for a in data:
        aid = a.get("id", "")
        name = a.get("name", "")
        console.print(f"  [cyan]{aid}[/cyan]  {name}")


# ── events ──


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Output NDJSON")
@click.pass_context
def events(ctx: click.Context, as_json: bool) -> None:
    """Stream server events (SSE). Ctrl+C to stop."""
    client = get_client(ctx.obj.get("url"))
    if not ctx.obj.get("quiet"):
        console.print("[dim]Streaming events...[/dim]")

    def on_event(event: dict) -> None:
        if as_json or ctx.obj.get("format") == "json":
            click.echo(json.dumps(event, ensure_ascii=False))
        else:
            event_type = event.get("type", "unknown")
            console.print(f"  [{event_type}] {json.dumps(event, ensure_ascii=False)}")

    try:
        client.events(callback=on_event)
    except KeyboardInterrupt:
        if not ctx.obj.get("quiet"):
            console.print("\n[dim]Stopped[/dim]")


# ── config ──


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.pass_context
def config(ctx: click.Context, as_json: bool) -> None:
    """Show server configuration."""
    client = get_client(ctx.obj.get("url"))
    data = client.config_get()
    if should_output_json(as_json, ctx):
        print_json(data)
        return
    console.print_json(json.dumps(data, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
