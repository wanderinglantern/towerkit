"""The write primitive: a failed write must never cost the old contents.

These tests simulate the failure rather than filling a real disk — `fsync`
is the last call before the atomic replace, so raising there exercises the
exact window where `write_text` used to leave a truncated file.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
import threading
from pathlib import Path

import pytest

from towerkit.atomicio import atomic_write_bytes, atomic_write_text, backup_path


def _enospc(_fd: int) -> None:
    raise OSError(errno.ENOSPC, "No space left on device")


class TestAtomicWrite:
    def test_a_failed_write_leaves_the_original_byte_identical(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        target = tmp_path / "p.json"
        target.write_text("original contents", encoding="utf-8")
        monkeypatch.setattr(os, "fsync", _enospc)

        with pytest.raises(OSError):
            atomic_write_text(target, "replacement")

        assert target.read_text(encoding="utf-8") == "original contents"

    def test_a_failed_write_leaves_no_temp_file_behind(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        target = tmp_path / "p.json"
        target.write_text("original", encoding="utf-8")
        monkeypatch.setattr(os, "fsync", _enospc)

        with pytest.raises(OSError):
            atomic_write_text(target, "replacement")

        assert list(tmp_path.glob(".*.tmp")) == []

    def test_the_previous_contents_are_kept_aside(self, tmp_path: Path) -> None:
        target = tmp_path / "p.json"
        target.write_text("v1", encoding="utf-8")

        atomic_write_text(target, "v2")

        assert target.read_text(encoding="utf-8") == "v2"
        assert backup_path(target).read_text(encoding="utf-8") == "v1"

    def test_the_backup_sidecar_is_hidden_and_not_a_program_file(
        self, tmp_path: Path
    ) -> None:
        # the program browser globs programs/*.json — the sidecar must not
        # show up there as a phantom program
        target = tmp_path / "atomic-2026.json"
        target.write_text("v1", encoding="utf-8")
        atomic_write_text(target, "v2")

        assert backup_path(target).name.startswith(".")
        assert list(tmp_path.glob("*.json")) == [target]

    def test_a_first_write_makes_no_backup(self, tmp_path: Path) -> None:
        target = tmp_path / "new.json"

        atomic_write_text(target, "v1")

        assert target.read_text(encoding="utf-8") == "v1"
        assert not backup_path(target).exists()

    def test_backup_can_be_turned_off(self, tmp_path: Path) -> None:
        target = tmp_path / "p.json"
        target.write_text("v1", encoding="utf-8")

        atomic_write_text(target, "v2", backup=False)

        assert not backup_path(target).exists()

    def test_a_read_only_target_is_still_refused(self, tmp_path: Path) -> None:
        # replace() only needs directory permission, so without an explicit
        # check a read-only file would silently become writable again
        target = tmp_path / "p.json"
        target.write_text("v1", encoding="utf-8")
        target.chmod(0o444)
        try:
            with pytest.raises(PermissionError):
                atomic_write_text(target, "v2")
            assert target.read_text(encoding="utf-8") == "v1"
        finally:
            target.chmod(0o644)

    def test_bytes_and_text_agree(self, tmp_path: Path) -> None:
        a, b = tmp_path / "a.json", tmp_path / "b.json"

        atomic_write_text(a, "héllo")
        atomic_write_bytes(b, "héllo".encode())

        assert a.read_bytes() == b.read_bytes()

    def test_the_copy_fallback_works_when_hard_links_are_unavailable(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # SMB/exFAT don't support hard links; os.link raises EXDEV there
        target = tmp_path / "p.json"
        target.write_text("v1", encoding="utf-8")

        def _no_hardlinks(_src: str, _dst: str) -> None:
            raise OSError(errno.EXDEV, "Cross-device link")

        monkeypatch.setattr(os, "link", _no_hardlinks)

        atomic_write_text(target, "v2")

        assert target.read_text(encoding="utf-8") == "v2"
        assert backup_path(target).read_text(encoding="utf-8") == "v1"

    def test_a_totally_failed_backup_does_not_fail_the_write(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # the sidecar is best-effort: even if both the hard link and the
        # copy fallback fail, the main write must still land
        target = tmp_path / "p.json"
        target.write_text("v1", encoding="utf-8")

        def _fail(*_args: object, **_kwargs: object) -> None:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(os, "link", _fail)
        monkeypatch.setattr(shutil, "copyfile", _fail)

        atomic_write_text(target, "v2")

        assert target.read_text(encoding="utf-8") == "v2"
        assert not backup_path(target).exists()

    def test_a_failed_backup_leaves_the_previous_sidecar_intact(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # the sidecar used to be unlinked BEFORE the replacement was made,
        # so a link that failed and a copy that failed left the user with no
        # backup at all — having had a valid one a moment earlier
        target = tmp_path / "p.json"
        target.write_text("v1", encoding="utf-8")
        atomic_write_text(target, "v2")  # sidecar now holds v1
        assert backup_path(target).read_text(encoding="utf-8") == "v1"

        def _fail(*_args: object, **_kwargs: object) -> None:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(os, "link", _fail)
        monkeypatch.setattr(shutil, "copyfile", _fail)

        atomic_write_text(target, "v3")

        assert target.read_text(encoding="utf-8") == "v3"
        assert backup_path(target).read_text(encoding="utf-8") == "v1", (
            "the only backup the user had was thrown away for one that "
            "could not be made"
        )
        # Globbed, not named: the sidecar's scratch name carries a per-write
        # token now, so pinning the old fixed `.p.json.bak.tmp` would pass
        # whatever was left behind.
        assert list(tmp_path.glob(".*.bak.*.tmp")) == [], (
            "the half-built sidecar was left behind"
        )


# --- CRITICAL 2: two writers of one program file --------------------------
#
# The temp name used to be derived from the TARGET alone, so every writer of
# `atomic-casualty.json` opened `.atomic-casualty.json.tmp`. Two `"wb"` opens
# truncated each other's bytes into one inode; one writer replaced the mixture
# into place and the other's replace raised FileNotFoundError. Measured against
# the old implementation: 30/30 FileNotFoundError on equal-size payloads; 40/40
# errors and 25/40 final files that were NEITHER payload on unequal ones;
# 13/20 torn with backup=True, which is bookkit's default.
#
# These are real threads writing the real function — nothing patched. They can
# only fail if the defect is back.

_WRITERS = 24
_PAYLOAD_ROUNDS = 6


def _payloads(count: int, *, equal_size: bool) -> list[bytes]:
    if equal_size:
        return [bytes([65 + i]) * 40_000 for i in range(count)]
    # Wildly different lengths: a torn file that begins with a long payload's
    # bytes and ends with a short one's is a file no reader can even parse.
    return [bytes([65 + i]) * (2_000 * (i + 1)) for i in range(count)]


def _write_concurrently(
    target: Path, payloads: list[bytes], *, backup: bool
) -> list[BaseException]:
    """Every payload written to one path at once. Returns what was raised."""
    start = threading.Barrier(len(payloads))
    errors: list[BaseException] = []
    lock = threading.Lock()

    def writer(data: bytes) -> None:
        start.wait()
        try:
            atomic_write_bytes(target, data, backup=backup)
        except BaseException as exc:  # noqa: BLE001 - recorded, then asserted on
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=writer, args=(d,)) for d in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


class TestConcurrentWriters:
    def test_equal_size_payloads_never_collide(self, tmp_path: Path) -> None:
        target = tmp_path / "atomic-casualty.json"
        for _ in range(_PAYLOAD_ROUNDS):
            payloads = _payloads(_WRITERS, equal_size=True)
            errors = _write_concurrently(target, payloads, backup=False)
            assert errors == [], f"a concurrent writer failed: {errors[:3]}"
            assert target.read_bytes() in payloads, (
                "the file on disk is not any writer's bytes — it is a mixture"
            )

    def test_unequal_payloads_are_never_torn_together(self, tmp_path: Path) -> None:
        """The measured worst case: a final file that began with one writer's
        bytes and ended with another's, because both `wb` opens landed on one
        inode and the shorter write never truncated the longer one's tail."""
        target = tmp_path / "atomic-casualty.json"
        for _ in range(_PAYLOAD_ROUNDS):
            payloads = _payloads(_WRITERS, equal_size=False)
            errors = _write_concurrently(target, payloads, backup=False)
            assert errors == [], f"a concurrent writer failed: {errors[:3]}"
            assert target.read_bytes() in payloads

    def test_the_backup_default_is_safe_too(self, tmp_path: Path) -> None:
        """backup=True is bookkit's default and shared a second fixed temp
        name (`.<name>.bak.tmp`) all of its own."""
        target = tmp_path / "atomic-casualty.json"
        original = b"v0" * 1_000
        target.write_bytes(original)
        for _ in range(_PAYLOAD_ROUNDS):
            payloads = _payloads(_WRITERS, equal_size=False)
            errors = _write_concurrently(target, payloads, backup=True)
            assert errors == [], f"a concurrent writer failed: {errors[:3]}"
            assert target.read_bytes() in payloads
            # The sidecar is best-effort under concurrency — WHICH version it
            # holds is a race — but it must always hold a whole one.
            assert backup_path(target).read_bytes() in [*payloads, original]

    def test_no_temp_files_survive_the_storm(self, tmp_path: Path) -> None:
        target = tmp_path / "atomic-casualty.json"
        errors = _write_concurrently(
            target, _payloads(_WRITERS, equal_size=False), backup=True
        )
        assert errors == []
        assert list(tmp_path.glob(".*.tmp")) == []


class TestTempFileNaming:
    def _replace_spy(self, monkeypatch) -> list[str]:
        seen: list[str] = []
        real = os.replace

        def spy(src, dst):  # type: ignore[no-untyped-def]
            seen.append(os.fspath(src))
            return real(src, dst)

        monkeypatch.setattr(os, "replace", spy)
        return seen

    def test_two_writes_of_one_path_use_different_temp_files(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        target = tmp_path / "p.json"
        seen = self._replace_spy(monkeypatch)

        atomic_write_text(target, "a", backup=False)
        atomic_write_text(target, "b", backup=False)

        assert len(seen) == 2
        assert seen[0] != seen[1], (
            "the temp name is derived from the target alone — every writer of "
            "this file opens the same inode"
        )

    def test_the_temp_file_stays_hidden_and_dot_tmp_suffixed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The `.*.tmp` shape is load-bearing twice over: the repo ignores it,
        and the program browser globs `programs/*.json`."""
        target = tmp_path / "p.json"
        seen = self._replace_spy(monkeypatch)

        atomic_write_text(target, "a", backup=False)

        name = Path(seen[0]).name
        assert name.startswith(".") and name.endswith(".tmp")
        assert Path(seen[0]).parent == tmp_path  # same directory, so replace is atomic

    def test_the_backup_sidecar_temp_is_unique_too(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        target = tmp_path / "p.json"
        target.write_text("v1", encoding="utf-8")
        seen = self._replace_spy(monkeypatch)

        atomic_write_text(target, "v2")
        atomic_write_text(target, "v3")

        sidecar_temps = [s for s in seen if ".bak." in Path(s).name]
        assert len(sidecar_temps) == 2
        assert sidecar_temps[0] != sidecar_temps[1]


class TestDurability:
    def test_the_directory_entry_is_fsynced_after_the_replace(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A reported-successful save that comes back as the old contents
        after power loss: os.replace is atomic for readers, but the rename is
        only as durable as the directory that records it."""
        import stat as _stat

        synced_dirs: list[bool] = []
        real = os.fsync

        def spy(fd: int) -> None:
            synced_dirs.append(_stat.S_ISDIR(os.fstat(fd).st_mode))
            real(fd)

        monkeypatch.setattr(os, "fsync", spy)
        atomic_write_text(tmp_path / "p.json", "v1", backup=False)

        assert any(synced_dirs), "only the file was fsynced, never its directory"

    def test_a_directory_that_refuses_fsync_does_not_fail_the_write(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Best-effort by design: the bytes are already on the platter."""
        import stat as _stat

        real = os.fsync

        def spy(fd: int) -> None:
            if _stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EINVAL, "Invalid argument")
            real(fd)

        monkeypatch.setattr(os, "fsync", spy)
        target = tmp_path / "p.json"
        atomic_write_text(target, "v1", backup=False)
        assert target.read_text(encoding="utf-8") == "v1"


class TestPermissions:
    def test_an_existing_file_keeps_its_mode(self, tmp_path: Path) -> None:
        """The temp file is a NEW inode, so nothing carries the target's mode
        across unless this module carries it: a restricted program file was
        being handed back to everybody by a save that only changed the text."""
        target = tmp_path / "p.json"
        target.write_text("v1", encoding="utf-8")
        target.chmod(0o600)

        atomic_write_text(target, "v2", backup=False)

        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def test_a_group_readable_file_is_not_narrowed_either(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "p.json"
        target.write_text("v1", encoding="utf-8")
        target.chmod(0o664)

        atomic_write_text(target, "v2", backup=False)

        assert stat.S_IMODE(target.stat().st_mode) == 0o664

    def test_a_new_file_gets_exactly_what_a_plain_open_would_give_it(
        self, tmp_path: Path
    ) -> None:
        """No umask probe, no hard-coded 0644, and NOT mkstemp's private 0600
        — the umask is the user's answer and os.open applies it."""
        reference = tmp_path / "reference.json"
        with open(reference, "wb") as fh:
            fh.write(b"x")
        target = tmp_path / "new.json"

        atomic_write_bytes(target, b"x")

        assert stat.S_IMODE(target.stat().st_mode) == stat.S_IMODE(
            reference.stat().st_mode
        )
