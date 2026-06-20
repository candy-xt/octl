"""HTTP client for OpenCode server API."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import httpx


class OCError(Exception):
    """octl error."""


class OpenCodeClient:
    """Thin wrapper around OpenCode server HTTP API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:4096",
        username: str = "opencode",
        password: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        auth = None
        if password:
            auth = httpx.BasicAuth(username, password)
        self._client = httpx.Client(
            base_url=self.base_url,
            auth=auth,
            timeout=timeout,
        )

    def _get(self, path: str, **kwargs: Any) -> Any:
        resp = self._client.get(path, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, **kwargs: Any) -> Any:
        resp = self._client.post(path, **kwargs)
        resp.raise_for_status()
        if resp.status_code == 204:
            return None
        return resp.json()

    def _patch(self, path: str, **kwargs: Any) -> Any:
        resp = self._client.patch(path, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str, **kwargs: Any) -> Any:
        resp = self._client.delete(path, **kwargs)
        resp.raise_for_status()
        if resp.status_code == 204:
            return None
        return resp.json()

    # ── health ──

    def health(self) -> dict:
        return self._get("/global/health")

    def check_server(self) -> bool:
        try:
            h = self.health()
            return h.get("healthy", False)
        except Exception:
            return False

    # ── providers ──

    def providers(self) -> dict:
        return self._get("/provider")

    # ── session ──

    def session_list(self) -> list[dict]:
        return self._get("/session")

    def session_create(self, title: str | None = None) -> dict:
        body: dict[str, Any] = {}
        if title:
            body["title"] = title
        return self._post("/session", json=body)

    def session_get(self, session_id: str) -> dict:
        return self._get(f"/session/{session_id}")

    def session_status(self, session_id: str | None = None) -> dict:
        if session_id:
            all_status = self._get("/session/status")
            return all_status.get(session_id, {})
        return self._get("/session/status")

    def session_delete(self, session_id: str) -> bool:
        return self._delete(f"/session/{session_id}")

    def session_abort(self, session_id: str) -> bool:
        return self._post(f"/session/{session_id}/abort")

    def session_diff(self, session_id: str) -> list[dict]:
        return self._get(f"/session/{session_id}/diff")

    # ── messages ──

    def message_list(self, session_id: str, limit: int | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if limit:
            params["limit"] = limit
        return self._get(f"/session/{session_id}/message", params=params)

    def message_send(
        self,
        session_id: str,
        parts: list[dict],
        model: str | None = None,
        provider: str | None = None,
        agent: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {"parts": parts}
        if model:
            body["model"] = model
        if provider:
            body["provider"] = provider
        if agent:
            body["agent"] = agent
        return self._post(f"/session/{session_id}/message", json=body)

    def message_send_async(
        self,
        session_id: str,
        parts: list[dict],
        model: str | None = None,
        provider: str | None = None,
        agent: str | None = None,
    ) -> None:
        body: dict[str, Any] = {"parts": parts}
        if model:
            body["model"] = model
        if provider:
            body["provider"] = provider
        if agent:
            body["agent"] = agent
        self._post(f"/session/{session_id}/prompt_async", json=body)

    def message_get(self, session_id: str, message_id: str) -> dict:
        return self._get(f"/session/{session_id}/message/{message_id}")

    # ── files ──

    def file_list(self, path: str = ".") -> list[dict]:
        return self._get("/file", params={"path": path})

    def file_read(self, path: str) -> dict:
        return self._get("/file/content", params={"path": path})

    def file_search(self, pattern: str) -> list[dict]:
        return self._get("/find", params={"pattern": pattern})

    def file_find(self, query: str) -> list[str]:
        return self._get("/find/file", params={"query": query})

    # ── agents ──

    def agent_list(self) -> list[dict]:
        return self._get("/agent")

    # ── config ──

    def config_get(self) -> dict:
        return self._get("/config")

    def config_providers(self) -> dict:
        return self._get("/config/providers")

    # ── revert ──

    def session_revert(self, session_id: str, message_id: str) -> bool:
        """Revert changes made by a message."""
        return self._post(
            f"/session/{session_id}/revert",
            json={"messageID": message_id},
        )

    # ── events (SSE) ──

    def events(self, callback: Any | None = None) -> None:
        """Stream server-sent events. Calls callback(event_dict) for each event."""
        with self._client.stream("GET", "/event") as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    try:
                        event = json.loads(line[6:])
                        if callback:
                            callback(event)
                        else:
                            print(json.dumps(event, ensure_ascii=False))
                    except json.JSONDecodeError:
                        pass

    # ── high-level helpers ──

    def send_and_wait(
        self,
        prompt: str,
        session_id: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        agent: str | None = None,
        timeout: float = 300.0,
    ) -> dict:
        """Create session if needed, send prompt, wait for response."""
        if not session_id:
            s = self.session_create()
            sid: str = s["id"]
        else:
            sid = session_id

        parts = [{"type": "text", "text": prompt}]
        self.message_send_async(
            sid,
            parts,
            model=model,
            provider=provider,
            agent=agent,
        )

        # poll with exponential backoff
        start = time.time()
        interval = 1.0
        max_interval = 10.0
        while time.time() - start < timeout:
            try:
                status = self.session_status(sid)
                state = status.get("state", "")
                if state == "idle":
                    break
            except Exception:
                pass  # network blip, keep polling
            time.sleep(interval)
            interval = min(interval * 1.5, max_interval)
        else:
            raise OCError(f"Timeout after {timeout}s waiting for response")

        messages = self.message_list(sid, limit=1)
        return {
            "session_id": sid,
            "messages": messages,
        }


def discover_server() -> str | None:
    """Try to find a running OpenCode server on common ports."""
    for port in [4096, 4097, 8080, 3000]:
        url = f"http://127.0.0.1:{port}"
        try:
            c = OpenCodeClient(base_url=url, timeout=2.0)
            if c.check_server():
                return url
        except Exception:
            continue
    return None
