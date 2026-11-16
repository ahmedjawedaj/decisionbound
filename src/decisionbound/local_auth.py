"""Local pairing token lifecycle, independent of optional FastAPI dependencies.

Both `dbound client pair` and the optional web server use this module. The
credential is created with restrictive permissions and is never embedded in a
repository, export, API response, or extension workspace setting.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from .settings import state_dir


def local_token() -> str:
    """Create or read one user-scoped loopback pairing token.

    Refuses a symlinked credential and avoids overwriting an existing token.
    The caller is responsible for checking `Authorization: Bearer` and
    explicitly requesting token disclosure from the CLI if needed.
    """
    location: Path = state_dir() / 'local_token'
    location.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if location.is_symlink():
        raise ValueError('Symlinked local authentication token refused')
    if not location.exists():
        try:
            fd = os.open(location, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                out.write(secrets.token_urlsafe(32))
    if location.is_symlink() or not location.is_file():
        raise ValueError('Local authentication token must be a regular file')
    if os.name == 'posix' and (location.stat().st_mode & 0o077):
        raise PermissionError('Local authentication token has insecure file permissions')
    token = location.read_text(encoding='utf-8')
    if len(token) < 32 or any(c.isspace() for c in token):
        raise ValueError('Malformed local authentication token')
    return token
