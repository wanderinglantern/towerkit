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
    p_render.add_argument(
        "--cell-premiums",
        action="store_true",
        help="show each carrier's premium share inside its block",
    )
    p_render.add_argument(
        "--cell-dates",
        action="store_true",
        help="show the policy term inside each block",
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

    p_soi = sub.add_parser("soi", help="export a Schedule of Insurance workbook (.xlsx)")
    p_soi.add_argument("path", type=Path, metavar="program.json")
    p_soi.add_argument(
        "-o", "--out", type=Path, default=None,
        help="output file (default: '<Insured> - Schedule of Insurance.xlsx')",
    )
    p_soi.add_argument("--theme", type=Path, default=None)
    p_soi.add_argument(
        "--no-premiums", action="store_true",
        help="omit the Premium column and section roll-ups",
    )
    p_soi.add_argument(
        "--schematic", action="store_true",
        help="include the tower schematic as a second worksheet",
    )
    p_soi.set_defaults(handler=_cmd_soi)

    p_edit = sub.add_parser("edit", help="edit a program in the TUI")
    p_edit.add_argument("path", type=Path, nargs="?", metavar="program.json")
    p_edit.add_argument("--theme", type=Path, default=None, help="theme for preview and renders")
    p_edit.set_defaults(handler=_cmd_edit)

    p_new = sub.add_parser("new", help="create a blank program in the TUI")
    p_new.add_argument("--theme", type=Path, default=None, help="theme for preview and renders")
    p_new.set_defaults(handler=_cmd_edit)

    p_tpl = sub.add_parser("template", help="write a blank import template workbook")
    p_tpl.add_argument("out", type=Path, metavar="template.xlsx")
    p_tpl.set_defaults(handler=_cmd_template)

    p_imp = sub.add_parser(
        "import", help="build a program file from a schedule (xlsx/csv/text/stdin)"
    )
    p_imp.add_argument("source", help="schedule file, or - for pasted text on stdin")
    p_imp.add_argument("-o", "--out", type=Path, default=None)
    p_imp.add_argument("--insured", default="")
    p_imp.add_argument("--program", default="", dest="program_name")
    p_imp.add_argument("--inception", default="", help="period start for pasted text")
    p_imp.add_argument("--expiry", default="", help="period end for pasted text")
    p_imp.add_argument("--edit", action="store_true", help="open the result in the TUI")
    p_imp.set_defaults(handler=_cmd_import)

    p_mcp = sub.add_parser("mcp", help="stdio MCP server for design-level assist")
    p_mcp.add_argument(
        "--programs", type=Path, nargs="+", default=None,
        help="program roots to serve (default: ./programs)",
    )
    p_mcp.add_argument(
        "--connector-info", action="store_true",
        help="print the Add MCP Connector field values instead of serving",
    )
    p_mcp.add_argument(
        "--check", action="store_true",
        help="verify the connector starts and finds programs; exit 1 if not",
    )
    p_mcp.set_defaults(handler=_cmd_mcp)

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
    stored = program.render
    theme_path = args.theme or (Path(stored.theme) if stored and stored.theme else None)
    theme = load_theme(theme_path)
    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    written = render_program(
        program, theme, out_dir=args.out, stem=args.path.stem, formats=formats,
        gamma=args.gamma,
        show_totals=not args.no_totals and (stored.show_totals if stored else True),
        show_premiums=not args.no_premiums and (stored.show_premiums if stored else True),
        cell_premiums=args.cell_premiums or bool(stored and stored.cell_premiums),
        cell_dates=args.cell_dates or bool(stored and stored.cell_dates),
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


def _cmd_soi(args: argparse.Namespace) -> int:
    from .render.soi_xlsx import write_soi_workbook
    from .soi import default_filename
    from .theme import load_theme
    from .validate import ProgramInvalidError

    try:
        program = _load_for_render(args.path)
    except ProgramInvalidError as exc:
        print(exc, file=sys.stderr)
        return 1
    stored = program.render
    theme_path = args.theme or (Path(stored.theme) if stored and stored.theme else None)
    theme = load_theme(theme_path)
    out_path = args.out or Path(default_filename(program))
    written = write_soi_workbook(
        program,
        theme=theme,
        out_path=out_path,
        show_premiums=not args.no_premiums and (stored.show_premiums if stored else True),
        include_schematic=args.schematic or bool(stored and stored.soi_schematic),
    )
    print(written)
    _maybe_open([written])
    return 0


def _cmd_edit(args: argparse.Namespace) -> int:
    from .tui.app import TowerkitApp

    path = getattr(args, "path", None)
    app = TowerkitApp(path=path, new=args.command == "new", theme_path=args.theme)
    app.run()
    return 0


def _cmd_template(args: argparse.Namespace) -> int:
    from .ingest_template import write_template

    print(write_template(args.out))
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    from .ingest import import_schedule
    from .model import dump_program
    from .validate import ProgramInvalidError

    insured, program_name = args.insured, args.program_name
    source = args.source
    try:
        draft = import_schedule(
            None if source == "-" else source,
            text=sys.stdin.read() if source == "-" else None,
            insured=insured,
            program=program_name,
            inception=args.inception,
            expiry=args.expiry,
        )
    except ValueError as exc:
        print(exc)
        return 1
    for diag in draft.diagnostics.items:
        print(f"  {diag}")
    try:
        program = draft.to_program()
    except ProgramInvalidError as exc:
        for diag in exc.diagnostics.errors:
            print(f"  {diag}")
        return 1
    out = args.out or Path(f"{_file_slug(insured)}-{_file_slug(program_name)}.json")
    if out.exists():  # program files are the source of truth — never clobber one
        print(f"{out} already exists — refusing to overwrite (pass a different -o)")
        return 1
    dump_program(program, out)
    print(out)
    if args.edit:
        from .tui.app import TowerkitApp

        TowerkitApp(path=out, new=False, theme_path=None).run()
    return 0


def _cmd_mcp(args: argparse.Namespace) -> int:
    from . import connector

    if args.connector_info:
        try:
            found = connector.fields(args.programs)
        except connector.NoRoots as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        env = ", ".join(f"{k}={v}" for k, v in found.env.items()) or "(none)"
        print("Add MCP Connector — paste one line per field:\n")
        print(f"  Name         {found.name}")
        print(f"  Command      {found.command}")
        print(f"  Arguments    {', '.join(found.arguments)}")
        print(f"  Env Secrets  {env}")
        print(f"  Mode         {found.mode}")
        return 0

    if args.check:
        report = connector.check(args.programs)
        for result in report.checks:
            print(f"{'✓' if result.ok else '✗'} {result.label:<8}  {result.detail}")
        return 0 if report.ok else 1

    from .mcpserver import serve

    serve(args.programs)
    return 0


def _file_slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "program"


def _maybe_open(paths: list[Path]) -> None:
    open_cmd = os.environ.get("OPEN_CMD")
    if open_cmd and paths:
        subprocess.run([*shlex.split(open_cmd), str(paths[0])], check=False)


if __name__ == "__main__":
    raise SystemExit(main())
