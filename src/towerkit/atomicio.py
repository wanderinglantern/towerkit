"""Durable file writes.

`open(mode="w")` truncates before the first byte lands, so any failure
between truncate and flush destroys the old file. Every write of a program
file — a user's irreplaceable work — goes through here: a same-directory
temp file, fsynced, then an atomic `os.replace`, then an fsync of the
directory that now names it. Every failure mode then leaves either the old
contents or the new ones, never nothing.

THE TEMP NAME IS UNIQUE PER WRITE, and that is load-bearing. It used to be
derived from the target alone (`.<name>.tmp`), so every writer of one program
file opened the SAME temp path: two `"wb"` opens truncated each other's bytes
into one inode, one writer `os.replace`d the mixture into place and the other
raised `FileNotFoundError` from a replace whose source had already been
renamed away. Measured against the old code, 30 concurrent writers of
equal-size payloads produced 30 `FileNotFoundError`s; with payloads of
different sizes, 25 of 40 final files were neither payload — they began with
one writer's bytes and ended with another's. towerkit JSON files are the sole
authority for program structure, and bookkit's `sync.write_through`,
towerkit's own `EditSession.save` and the MCP server all reach this function,
so "two writers" is an ordinary Tuesday, not an exotic race (2026-08-18).

Atomicity is not mutual exclusion: concurrent writers still race for the last
word on CONTENT. That is a different guarantee, owned by the callers that need
it (bookkit's sha256 write-through guard, towerkit's edit session), and this
module deliberately takes no lock.

This is the one place that knows how a program file reaches disk. The TUI,
the CLI and the MCP server all route their program writes through it, so
"what happens when the write fails" has one answer instead of three.
Renders and the xlsx template writer legitimately bypass it — their output
is regenerable, not irreplaceable.

Stdlib only, deliberately: no towerkit import may ever appear here, so
this module can be used from anywhere without an import cycle.
"""

from __future__ import annotations

import errno
import os
import secrets
import shutil
import stat
from pathlib import Path

__all__ = ["atomic_write_bytes", "atomic_write_text", "backup_path"]

# Enough entropy that a collision is not a thing that happens; the retry loop
# below exists because O_EXCL makes the answer to "did it anyway" free, not
# because anyone expects to use it.
_TOKEN_BYTES = 8
_TMP_ATTEMPTS = 8


def backup_path(path: Path | str) -> Path:
    """Sidecar holding the contents this file had before the last write.

    Hidden, and not named `*.json`, so it never shows up as a phantom
    program in the browser's `programs/*.json` glob."""
    path = Path(path)
    return path.with_name(f".{path.name}.bak")


def _temp_name(path: Path) -> Path:
    """A same-directory scratch name no other writer will pick.

    Hidden and `.tmp`-suffixed so the repo's existing `.*.tmp` ignore rule
    still covers it, and so a crashed write leaves nothing the program
    browser's `programs/*.json` glob can see."""
    return path.with_name(f".{path.name}.{secrets.token_hex(_TOKEN_BYTES)}.tmp")


def _open_temp(path: Path, mode: int) -> tuple[int, Path]:
    """Create a fresh temp file beside `path` and return its fd and name.

    `O_CREAT | O_EXCL` is what makes the uniqueness real: the name is random,
    but the kernel — not the random number generator — is what guarantees no
    two writers ever share the inode. `mode` is passed to `os.open` rather than
    applied afterwards, so a NEW file lands with exactly the permissions a
    plain `open(..., "wb")` would have given it (the umask applies), and the
    window in which it is more permissive than intended does not exist."""
    for _ in range(_TMP_ATTEMPTS):
        tmp = _temp_name(path)
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        except FileExistsError:
            continue
        return fd, tmp
    raise OSError(
        errno.EEXIST, "could not create a unique temporary file", str(path)
    )


# What a brand-new file asks for; `os.open` subtracts the umask from it,
# which is exactly what `open(path, "wb")` did before.
_DEFAULT_MODE = 0o666


def _existing_mode(path: Path) -> int | None:
    """The permissions `path` already carries, or None if it does not exist.

    Preserving them matters because the temp file is a NEW inode. Writing
    through a fresh inode with default permissions silently re-permissions the
    target on every save — a 0600 program file quietly becoming group- and
    world-readable, or a deliberately restricted one being handed back to
    everybody by a save the user thought only changed the text."""
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return None


def _fsync_dir(directory: Path) -> None:
    """Make the directory entry itself durable.

    `os.replace` is atomic with respect to readers the instant it returns, but
    on a crashing machine the RENAME is only as durable as the directory that
    records it: the file's own fsync says nothing about the name pointing at
    it, so a save reported successful could come back as the old contents
    after power loss. Best-effort — the bytes are already on the platter and a
    filesystem that refuses to fsync a directory (some network mounts) is no
    reason to fail a write that landed."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_text(
    path: Path | str, text: str, *, encoding: str = "utf-8", backup: bool = True
) -> None:
    """Write `text` durably. See `atomic_write_bytes` for the guarantees."""
    atomic_write_bytes(path, text.encode(encoding), backup=backup)


def atomic_write_bytes(path: Path | str, data: bytes, *, backup: bool = True) -> None:
    """Write `data` durably: unique temp in the same directory, fsync,
    replace, fsync the directory.

    Raises `OSError` (including `PermissionError`) on failure, and the
    pre-existing file is byte-unchanged when it does.

    Concurrent writers of the same path never mix: each owns a private inode,
    so the file that wins is exactly one writer's bytes, whole.

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

    mode = _existing_mode(path)
    # Created with the FINAL permissions where they are known, so the file is
    # never briefly more permissive than the one it replaces; `fchmod` after
    # is what defeats the umask, which `os.open` would otherwise subtract from
    # a mode the target already proves is allowed.
    fd, tmp = _open_temp(path, _DEFAULT_MODE if mode is None else mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            if mode is not None:
                os.fchmod(fh.fileno(), mode)
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
    _fsync_dir(path.parent)


def _keep_aside(path: Path) -> None:
    """Point the sidecar at the current contents.

    A hard link first: it copies no data, so it cannot itself fail with
    ENOSPC on the full disk this whole module exists to survive. Copying
    is the fallback for filesystems without hard links (SMB, exFAT).

    Build the replacement under a UNIQUE temp name and `os.replace` it into
    position: unlinking the sidecar first meant a link that then failed AND
    a copy that then failed left the user with no backup at all, having had
    a valid one a moment earlier. Replacing keeps the previous sidecar
    whenever the new one cannot be made. The name is unique for the same
    reason the main write's is — the old fixed `.<name>.bak.tmp` was one more
    path two concurrent writers fought over."""
    bak = backup_path(path)
    tmp = bak.with_name(f"{bak.name}.{secrets.token_hex(_TOKEN_BYTES)}.tmp")
    try:
        try:
            os.link(path, tmp)
        except OSError:
            shutil.copyfile(path, tmp)
        os.replace(tmp, bak)
    except OSError:
        pass
    finally:
        # UNCONDITIONAL, not an error path. `rename(2)` is a documented no-op
        # when both names already resolve to ONE inode, and that is exactly
        # what a second backup of an unchanged target produces: the sidecar is
        # a hard link to the file, so the second writer's scratch link and the
        # sidecar are the same inode, the replace reports success and the
        # scratch name survives. The old fixed name hid this by unlinking the
        # leftover on the next write; with a per-write name every concurrent
        # writer leaked one instead (found by the concurrency test, 2026-08-18).
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
