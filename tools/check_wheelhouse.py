#!/usr/bin/env python3
"""Prove the published wheelhouse can still satisfy an offline install.

The wheelhouse is a hand-built release asset: adding a dependency to
pyproject.toml does nothing until someone downloads the wheel, re-zips it and
re-uploads it. That drill gets skipped — openpyxl landed on 2026-08-11 and the
asset stayed stale until an ``OFFLINE=1`` install failed on a machine with no
PyPI access, which is exactly the machine that cannot route around it.

This checks both halves of the drill against the *published* asset:

  1. its sha256 matches the pin in install.sh — uploading without bumping the
     pin leaves every installer refusing the download as tampered-with;
  2. every runtime dependency has a wheel in it, at a version the requirement
     actually accepts.

    python tools/check_wheelhouse.py [pyproject.toml ...]

Extra pyproject paths are for a wheelhouse that must also cover a sibling
checkout's dependencies (bookkit installs towerkit editable from ../towerkit).
Projects named by any of the given files are skipped: they install from
source, never from a wheel.
"""

from __future__ import annotations

import re
import sys
import tomllib
import urllib.request
import zipfile
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = ROOT / "install.sh"
# install.sh installs the build backend from the wheelhouse too, because it
# builds the project with --no-build-isolation (no index to fetch it from).
BUILD_REQUIRES = ("hatchling", "editables")


def shell_var(name: str, text: str) -> str:
    """Read a `NAME="value"` assignment out of install.sh — one source of truth."""
    match = re.search(rf'^{name}="([^"]+)"', text, re.MULTILINE)
    if match is None:
        sys.exit(f"error: {name} is not set in {INSTALL_SH}")
    return match.group(1)


def download(url: str) -> tuple[Path, str]:
    """Stream the asset to a temp file; return its path and sha256."""
    digest = sha256()
    with urllib.request.urlopen(url) as response:
        with NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            for chunk in iter(lambda: response.read(1 << 20), b""):
                digest.update(chunk)
                tmp.write(chunk)
            return Path(tmp.name), digest.hexdigest()


def wheel_versions(archive: Path) -> dict[str, list[Version]]:
    """Map canonical dist name -> every version present as a wheel."""
    found: dict[str, list[Version]] = {}
    with zipfile.ZipFile(archive) as zf:
        for entry in zf.namelist():
            filename = Path(entry).name
            if not filename.endswith(".whl"):
                continue
            dist, _, rest = filename.partition("-")
            try:
                version = Version(rest.split("-")[0])
            except InvalidVersion:
                continue
            found.setdefault(canonicalize_name(dist), []).append(version)
    return found


def requirements(paths: list[str]) -> list[Requirement]:
    """Runtime requirements from every pyproject, minus the projects themselves."""
    from_source: set[str] = set()
    reqs: list[Requirement] = []
    for path in paths:
        project = tomllib.loads(Path(path).read_text(encoding="utf-8"))["project"]
        from_source.add(canonicalize_name(project["name"]))
        reqs += [Requirement(dep) for dep in project.get("dependencies", [])]
    reqs += [Requirement(dep) for dep in BUILD_REQUIRES]
    # Two pyprojects usually share requirements; collapse the identical ones
    # but keep genuinely different constraints on the same dist visible.
    seen: dict[tuple[str, str], Requirement] = {}
    for req in reqs:
        name = canonicalize_name(req.name)
        if name not in from_source:
            seen.setdefault((name, str(req.specifier)), req)
    return list(seen.values())


def main(argv: list[str]) -> int:
    paths = argv or [str(ROOT / "pyproject.toml")]
    install_sh = INSTALL_SH.read_text(encoding="utf-8")
    url = shell_var("WHEELHOUSE_URL", install_sh)
    pinned = shell_var("WHEELHOUSE_SHA256", install_sh)

    print(f"→ downloading {url}")
    archive, digest = download(url)
    try:
        problems: list[str] = []
        if digest == pinned:
            print(f"✓ sha256 matches the pin in install.sh ({digest[:12]}…)")
        else:
            problems.append(
                f"sha256 mismatch: install.sh pins {pinned}, "
                f"the published asset is {digest}"
            )

        found = wheel_versions(archive)
        print(f"→ {sum(len(v) for v in found.values())} wheels, "
              f"{len(found)} distributions")
        for req in sorted(requirements(paths), key=lambda r: r.name.lower()):
            if req.marker is not None:
                print(f"  ? {req} — environment marker, not checked")
                continue
            versions = found.get(canonicalize_name(req.name))
            if not versions:
                problems.append(f"{req.name}: no wheel in the wheelhouse")
            elif not any(
                req.specifier.contains(v, prereleases=True) for v in versions
            ):
                have = ", ".join(str(v) for v in sorted(versions))
                problems.append(f"{req}: wheelhouse only has {have}")
            else:
                print(f"  ✓ {req}")
    finally:
        archive.unlink(missing_ok=True)

    if not problems:
        print("✓ the published wheelhouse satisfies an offline install")
        return 0

    asset = url.rsplit("/", 1)[-1]
    tag = url.rsplit("/", 2)[-2]
    print("\nerror: the published wheelhouse is out of date:", file=sys.stderr)
    for problem in problems:
        print(f"  ✗ {problem}", file=sys.stderr)
    print(
        f"\nRebuild it (needs PyPI access):\n"
        f"    python3 -m pip download <pkg> --no-deps -d wheelhouse\n"
        f"    (cd wheelhouse && zip -qXr ../{asset} .)\n"
        f"    gh release upload {tag} {asset} --clobber\n"
        f"    shasum -a 256 {asset}   # → WHEELHOUSE_SHA256 in install.sh\n"
        f"Upload and pin bump must land together, or the hash check rejects it.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
