"""Local runtime paths; overrides are explicit and scoped to this process."""
from __future__ import annotations
import os
from pathlib import Path
from .storage import SQLiteRepository


def state_dir() -> Path:
    """Return the user's private state directory or an explicit test override."""
    return Path(os.environ.get("DBOUND_HOME", Path.home() / ".decisionbound")).expanduser()


def repository() -> SQLiteRepository:
    """Open the local state store without invoking remote services."""
    return SQLiteRepository(state_dir() / "state.sqlite3")
