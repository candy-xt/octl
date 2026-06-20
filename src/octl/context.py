"""Session context management - tracks active session."""

from __future__ import annotations

import json
import os

SESSION_DIR = os.path.expanduser("~/.octl")
ACTIVE_SESSION_FILE = os.path.join(SESSION_DIR, "active_session")


def get_active_session() -> str | None:
    """Get the active session ID, or None."""
    try:
        with open(ACTIVE_SESSION_FILE) as f:
            data = json.load(f)
            return data.get("session_id")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def set_active_session(session_id: str) -> None:
    """Set the active session ID, persisting to disk."""
    os.makedirs(SESSION_DIR, exist_ok=True)
    with open(ACTIVE_SESSION_FILE, "w") as f:
        json.dump({"session_id": session_id}, f)


def clear_active_session() -> None:
    """Clear the active session."""
    try:
        os.remove(ACTIVE_SESSION_FILE)
    except FileNotFoundError:
        pass
