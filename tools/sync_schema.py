#!/usr/bin/env python3
"""Bring `program.schema.json` back into line with `model.py`.

    python tools/sync_schema.py [--check]

Both copies are written: `schema/program.schema.json` (the published
reference) and `src/towerkit/schema/program.schema.json` (the one
`validate.py` actually loads through `resources.files`). Writing only one is
the mistake `test_schema_copies_are_identical` exists to catch, so this never
offers the option.

`--check` writes nothing and exits 1 if either copy is out of date — the same
answer `tests/test_conventions.py` gives, available without the suite.

The derivation itself lives in `towerkit.schemagen`, which is pure and
type-checked; this script is the half that knows where the repo keeps its
files. It is repo maintenance in the same sense as `check_wheelhouse.py`,
which is why it is here and not a `towerctl` subcommand: `towerctl` operates
on the broker's own program files and must not write into this checkout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from towerkit.schemagen import (  # noqa: E402
    SchemaDerivationError,
    dumps_schema,
    sync_document,
)

COPIES = (
    REPO / "schema" / "program.schema.json",
    REPO / "src" / "towerkit" / "schema" / "program.schema.json",
)


def main(argv: list[str]) -> int:
    check = "--check" in argv
    source = json.loads(COPIES[0].read_text("utf-8"))
    try:
        wanted = dumps_schema(sync_document(source))
    except SchemaDerivationError as exc:
        # Not a stack trace. This is the branch where the generator REFUSES to
        # guess — a hand-authored fact contradicts the model, or a shape needs a
        # `$def` nobody has written — and the message is the whole instruction.
        print(f"cannot derive: {exc}", file=sys.stderr)
        return 1

    stale = [path for path in COPIES if path.read_text("utf-8") != wanted]
    if not stale:
        print("schema is in sync with model.py")
        return 0
    if check:
        for path in stale:
            print(f"stale: {path.relative_to(REPO)}", file=sys.stderr)
        print("run: python tools/sync_schema.py", file=sys.stderr)
        return 1
    for path in stale:
        path.write_text(wanted, encoding="utf-8")
        print(f"wrote {path.relative_to(REPO)}")
    print("check the added properties: a generated one carries a type and no prose")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
