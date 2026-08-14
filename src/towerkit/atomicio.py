"""Durable file writes.

`open(mode="w")` truncates before the first byte lands, so any failure
between truncate and flush destroys the old file. Everything towerkit
writes is a user's irreplaceable work — program JSON above all — so every
write goes through here: a same-directory temp file, fsynced, then an
atomic `os.replace`. Every failure mode then leaves either the old
contents or the new ones, never nothing.

This is the one place that knows how a file reaches disk. The TUI, the
CLI and the MCP server all route through it, so "what happens when the
write fails" has one answer instead of three.

Stdlib only, deliberately: no towerkit import may ever appear here, so
this module can be used from anywhere without an import cycle.
"""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

__all__ = ["atomic_write_bytes", "atomic_write_text", "backup_path"]


def backup_path(path: Path | str) -> Path:
    """Sidecar holding the contents this file had before the last write.

    Hidden, and not named `*.json`, so it never shows up as a phantom
    program in the browser's `programs/*.json` glob."""
    path = Path(path)
    return path.with_name(f".{path.name}.bak")


def atomic_write_text(
    path: Path | str, text: str, *, encoding: str = "utf-8", backup: bool = True
) -> None:
    """Write `text` durably. See `atomic_write_bytes` for the guarantees."""
    atomic_write_bytes(path, text.encode(encoding), backup=backup)


def atomic_write_bytes(path: Path | str, data: bytes, *, backup: bool = True) -> None:
    """Write `data` durably: temp in the same directory, fsync, replace.

    Raises `OSError` (including `PermissionError`) on failure, and the
    pre-existing file is byte-unchanged when it does.

    `backup` keeps the previous contents in a `backup_path` sidecar. It is
    best-effort and stays silent when it fails: the atomic replace already
    guarantees crash-safety, so the sidecar exists only to undo a save the
    user *meant* to make — an accidental Overwrite, a bad edit committed —
    and is not worth failing an otherwise good save over.
    """
    path = Path(path)
    if path.exists() and not os.access(path, os.W_OK):
        # replace() needs only directory permission, so without this a
        # read-only file would silently become writable again — and a
        # read-only program file usually means "do not edit this".
        raise PermissionError(errno.EACCES, "Permission denied", str(path))

    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if backup and path.exists():
            _keep_aside(path)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _keep_aside(path: Path) -> None:
    """Point the sidecar at the current contents.

    A hard link first: it copies no data, so it cannot itself fail with
    ENOSPC on the full disk this whole module exists to survive. Copying
    is the fallback for filesystems without hard links (SMB, exFAT)."""
    bak = backup_path(path)
    try:
        bak.unlink(missing_ok=True)
        os.link(path, bak)
    except OSError:
        try:
            shutil.copyfile(path, bak)
        except OSError:
            pass
