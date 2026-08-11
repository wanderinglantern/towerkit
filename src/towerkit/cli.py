"""towerctl — validate, render, compare, edit insurance program files."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__

if TYPE_CHECKING:
    from .model import Program


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    return int(handler(args))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="towerctl",
        description="Insurance program schematics: validate, render, compare, edit.",
    )
    parser.add_argument("--version", action="version", version=f"towerctl {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_validate = sub.add_parser("validate", help="validate program files; exit 1 on any error")
    p_validate.add_argument("paths", nargs="+", type=Path, metavar="program.json")
    p_validate.set_defaults(handler=_cmd_validate)

    p_render = sub.add_parser("render", help="render a program to SVG/PDF/PNG")
    p_render.add_argument("path", type=Path, metavar="program.json")
    p_render.add_argument("--theme", type=Path, default=None)
    p_render.add_argument("--out", type=Path, default=Path("dist"))
    p_render.add_argument("--format", default="svg", help="comma-separated: svg,pdf,png")
    p_render.add_argument("--gamma", type=float, default=0.35)
    p_render.add_argument(
        "--no-totals",
        action="store_true",
        help="omit the total limit / total premium line from the chart header",
    )
    p_render.add_argument(
        "--no-premiums",
        action="store_true",
        help="hide all premium figures (e.g. a hypothetical structure)",
    )
    p_render.set_defaults(handler=_cmd_render)

    p_compare = sub.add_parser("compare", help="render a renewal comparison of two programs")
    p_compare.add_argument("expiring", type=Path, metavar="expiring.json")
    p_compare.add_argument("proposed", type=Path, metavar="proposed.json")
    p_compare.add_argument("--theme", type=Path, default=None)
    p_compare.add_argument("--out", type=Path, default=Path("dist"))
    p_compare.add_argument("--format", default="svg", help="comma-separated: svg,pdf,png")
    p_compare.add_argument("--gamma", type=float, default=0.35)
    p_compare.add_argument(
        "--no-premiums",
        action="store_true",
        help="hide premium figures and deltas (e.g. comparing a hypothetical design)",
    )
    p_compare.set_defaults(handler=_cmd_compare)

    p_edit = sub.add_parser("edit", help="edit a program in the TUI")
    p_edit.add_argument("path", type=Path, nargs="?", metavar="program.json")
    p_edit.add_argument("--theme", type=Path, default=None, help="theme for preview and renders")
    p_edit.set_defaults(handler=_cmd_edit)

    p_new = sub.add_parser("new", help="create a blank program in the TUI")
    p_new.add_argument("--theme", type=Path, default=None, help="theme for preview and renders")
    p_new.set_defaults(handler=_cmd_edit)

    return parser


def _cmd_validate(args: argparse.Namespace) -> int:
    from .validate import validate_file

    exit_code = 0
    for path in args.paths:
        program, diags = validate_file(path)
        badge = "✓" if diags.ok and not diags.warnings else ("⚠" if diags.ok else "✗")
        print(f"{badge} {path}")
        for diag in diags.items:
            print(f"  {diag}")
        if not diags.ok:
            exit_code = 1
    return exit_code


def _load_for_render(path: Path) -> Program:
    from .validate import ProgramInvalidError, validate_file

    program, diags = validate_file(path)
    if program is None or not diags.ok:
        raise ProgramInvalidError(diags, source=str(path))
    for diag in diags.warnings:
        print(f"  {diag}", file=sys.stderr)
    return program


def _cmd_render(args: argparse.Namespace) -> int:
    from .render.mpl_program import render_program
    from .theme import load_theme
    from .validate import ProgramInvalidError

    try:
        program = _load_for_render(args.path)
    except ProgramInvalidError as exc:
        print(exc, file=sys.stderr)
        return 1
    theme = load_theme(args.theme)
    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    written = render_program(
        program, theme, out_dir=args.out, stem=args.path.stem, formats=formats,
        gamma=args.gamma, show_totals=not args.no_totals,
        show_premiums=not args.no_premiums,
    )
    for path in written:
        print(path)
    _maybe_open(written)
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    from .render.mpl_renewal import render_renewal
    from .theme import load_theme
    from .validate import ProgramInvalidError

    try:
        expiring = _load_for_render(args.expiring)
        proposed = _load_for_render(args.proposed)
    except ProgramInvalidError as exc:
        print(exc, file=sys.stderr)
        return 1
    theme = load_theme(args.theme)
    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    stem = f"{args.expiring.stem}-vs-{args.proposed.stem}"
    written = render_renewal(
        expiring, proposed, theme, out_dir=args.out, stem=stem, formats=formats,
        gamma=args.gamma, show_premiums=not args.no_premiums,
    )
    for path in written:
        print(path)
    _maybe_open(written)
    return 0


def _cmd_edit(args: argparse.Namespace) -> int:
    from .tui.app import TowerkitApp

    path = getattr(args, "path", None)
    app = TowerkitApp(path=path, new=args.command == "new", theme_path=args.theme)
    app.run()
    return 0


def _maybe_open(paths: list[Path]) -> None:
    open_cmd = os.environ.get("OPEN_CMD")
    if open_cmd and paths:
        subprocess.run([*shlex.split(open_cmd), str(paths[0])], check=False)


if __name__ == "__main__":
    raise SystemExit(main())
