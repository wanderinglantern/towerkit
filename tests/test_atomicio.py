"""The write primitive: a failed write must never cost the old contents.

These tests simulate the failure rather than filling a real disk — `fsync`
is the last call before the atomic replace, so raising there exercises the
exact window where `write_text` used to leave a truncated file.
"""

from __future__ import annotations

import errno
import os
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
